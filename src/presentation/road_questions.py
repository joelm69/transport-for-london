"""Questions about individual road disruptions for the "Ask the data" chat: which types are most
common (collisions, breakdowns, signal faults...), which areas have the most and how many a
day. Read from tfl_portfolio.road_disruption_events (one row per disruption, rebuilt daily);
the SQL is fixed here, never built from the question's words.

Pure functions, shared by the portfolio and the TfL repository.
"""

import re

from . import analysis

EVENTS = f"`{analysis.DATASET}.road_disruption_events`"
ROAD = (r"\b(roads?|traffic|roadworks?|works|accidents?|collisions?|crash\w*|incidents?|break ?downs?|"
        r"disruptions?|driv\w+)\b")
NOT_ROAD = r"\b(tube|underground|trains?|rail|lines?|bus(es)?|bikes?|stations?|docks?)\b"
KINDS = {
    "types": r"\b(causes?|types?|kinds?|reasons?|categor\w*|most common|main sources?|breakdown of|pie)\b",
    "areas": (r"\bwhere\b|\bwhich (areas?|boroughs?|parts?( of london)?|places?|roads?|streets?|locations?)\b|"
              r"\bby (area|borough|location)\b"),
    "daily": r"\bhow many\b.*\b(day|daily|week|so far|in total|altogether)\b|\b(per|each|a|every) day\b",
}
EVENT_WORDS = r"\b(accidents?|collisions?|crash\w*|incidents?|break ?downs?|disruptions?|roadworks?|works)\b"


def choose(question):
    """"types", "areas" or "daily" for a question about road disruptions, else None."""
    text = question.lower()
    if not re.search(ROAD, text) or not re.search(EVENT_WORDS, text) or re.search(NOT_ROAD, text):
        return None
    if re.search(r"\b(right now|now|currently|at the moment|today)\b", text):
        return None   # the live road feed answers "now"
    return next((kind for kind, pattern in KINDS.items() if re.search(pattern, text)), None)


def _filter(text):
    """The rows a question is about and what to call them."""
    if re.search(r"\b(accidents?|collisions?|crash\w*)\b", text):
        return "category = 'Collisions'", "collisions"
    if re.search(r"\bbreak ?downs?\b", text):
        return "category = 'Breakdowns'", "breakdowns"
    if re.search(r"\b(roadworks?|works)\b", text) and not re.search(r"\bincidents?\b", text):
        return "NOT is_incident", "roadworks"
    if re.search(r"\bdisruptions?\b", text):
        return "TRUE", "road disruptions (incidents and roadworks)"
    return "is_incident", "road incidents"


def _num(value):
    return float(value) if value is not None else None


def _bar(title, data, x_title, y_title, horizontal=True):
    return {"kind": "bar", "title": title, "x": "x", "y": "y", "data": data, "format": "count", "series": None,
            "horizontal": horizontal, "x_title": x_title, "y_title": y_title}


def _hours(minutes):
    return f"{int(minutes // 60)}h {int(minutes % 60):02d}m" if minutes >= 60 else f"{minutes:.0f} min"


def run(kind, question, sources):
    """{"answer": markdown, "chart": description or None, "suggestions"}."""
    text = question.lower()
    where, name = _filter(text)
    if kind == "types":
        sql = f"""
            SELECT IFNULL(sub_category, category) AS x, COUNT(*) AS y,
                   APPROX_QUANTILES(minutes_to_clear, 2)[OFFSET(1)] AS clear
            FROM {EVENTS} WHERE {analysis.WINDOW} AND {where} GROUP BY x ORDER BY y DESC"""
    elif kind == "areas":
        sql = f"""
            SELECT IFNULL(borough, 'Not stated') AS x, COUNT(*) AS y FROM {EVENTS}
            WHERE {analysis.WINDOW} AND {where} GROUP BY x ORDER BY y DESC LIMIT 12"""
    else:
        sql = f"""
            SELECT FORMAT_DATE('%a %d %b', DATE(hour_start, 'Europe/London')) AS x, COUNT(*) AS y,
                   MIN(DATE(hour_start, 'Europe/London')) AS d
            FROM {EVENTS} WHERE {analysis.WINDOW} AND {where} GROUP BY x ORDER BY d"""
    rows = [dict(r) for r in sources.query(sql)]
    for r in rows:
        r["y"] = _num(r["y"])
    total = sum(r["y"] for r in rows)
    if not total:
        return {"answer": f"No {name} were recorded in the last {analysis.DAYS} days of data.", "chart": None}
    source = "TfL road disruptions recorded by this platform (one row each)"
    if kind == "types":
        top = rows[0]
        share = top["y"] / total
        answer = (f"**{top['x']}** is the most common of the {total:.0f} {name} recorded: {top['y']:.0f} "
                  f"({share:.0%}).")
        if len(rows) > 1:
            answer += " Next: " + "; ".join(f"{r['x']} {r['y']:.0f}" for r in rows[1:4]) + "."
        cleared = [r for r in rows if r.get("clear") is not None and r["y"] >= 2]
        if cleared:
            slowest = max(cleared, key=lambda r: _num(r["clear"]))
            answer += (f" The slowest to clear is {slowest['x']} (typically {_hours(_num(slowest['clear']))}"
                       ", the median).")
        chart = _bar(f"{name.capitalize()} by type", [{"x": r["x"], "y": r["y"]} for r in rows[:12]],
                     "", "Number recorded")
        if re.search(r"\b(pie|donut|doughnut)\b", text):
            chart["kind"] = "pie"
        follow = [f"Which areas have the most {name.split(' (')[0]}?", "How many road accidents are there each day?",
                  "When do most road accidents happen?"]
    elif kind == "areas":
        top = rows[0]
        answer = (f"By borough, **{top['x']}** had the most {name}: {top['y']:.0f} of {total:.0f} in the top "
                  "areas. Then " + ", ".join(f"{r['x']} ({r['y']:.0f})" for r in rows[1:4]) + ". TfL gives each "
                  "disruption a borough; for the busiest main roads ask \"which main road is most congested?\".")
        chart = _bar(f"{name.capitalize()} by borough", [{"x": r["x"], "y": r["y"]} for r in rows], "",
                     "Number recorded")
        follow = ["What are the most common types of road incident?", "When do most road accidents happen?",
                  "How many road accidents are there each day?"]
    else:
        days = len(rows)
        peak = max(rows, key=lambda r: r["y"])
        answer = (f"**About {total / days:.1f} {name} a day** on average over {days} days ({total:.0f} in all). "
                  f"The most were on {peak['x']} ({peak['y']:.0f}).")
        if days < 7:
            answer += " The first day may be partial (collection started part-way through it)."
        chart = _bar(f"{name.capitalize()} per day", [{"x": r["x"], "y": r["y"]} for r in rows], "Day",
                     "Number recorded", horizontal=False)
        follow = ["When do most road accidents happen?", "What are the most common types of road incident?",
                  "Which areas have the most road accidents?"]
    answer += "\n\n_Each disruption is counted once, on the day TfL first reported it._"
    return {"answer": answer, "chart": chart, "source": source, "suggestions": follow}
