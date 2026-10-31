"""The "Ask the data" analysis engine: answers "how does X change with Y?" and "is X related to
Y?" questions from the platform's history, with the most suitable chart and the numbers that
support the answer.

A question is read for the measures it names (bus bunching, Tube waiting time, rail disruption...)
and the dimension it asks about (hour of day, day of week, weekday or weekend, date, line or
route). The chart follows from that:
- a measure through the day or over time: a line chart, with the peak, the trough and how far
  the measure moves (and a weekday/weekend line each when both are asked for);
- a measure by day of week, weekday/weekend, line or route: a bar chart;
- two measures ("is bunching related to waiting time?"): a scatter plot of line-hours with a
  trend line and the correlation coefficient;
- rail disruption by cause ("why are the lines disrupted?"): the disrupted hours each reason
  accounts for, as bars, or as a pie when asked (a pie suits parts of a whole).
A chart type the visitor asks for (pie, bar, line, scatter) is used when it suits the data;
otherwise the answer says why another chart was drawn.

The SQL is assembled only from the catalogue below (tables, measures, dimensions), never from the
visitor's words, and reads the serving dataset (tfl_portfolio) over the last 28 complete days.
Pure functions: the caller passes `sources` with `query(sql) -> rows`.

Chart description: {"kind": "bar" | "line" | "scatter" | "pie", "title", "x", "y", "data", "format",
"x_format", "series", "horizontal", "x_title", "y_title"}.
"""

import math
import re

DATASET = "transport-for-london-511318.tfl_portfolio"
DAYS = 28
LONDON = "DATETIME(hour_start, 'Europe/London')"
WINDOW = (f"DATE(hour_start, 'Europe/London') BETWEEN DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL {DAYS} DAY) "
          "AND DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY)")

# Tables: name, the column naming a line / route / station, and the weight behind each average.
TABLES = {
    "arrivals": ("arrival_line_hourly", "line_id", "headways"),
    "disruption": ("line_disruption_hourly", "line_id", "in_service_minutes"),
    "accuracy": ("arrival_accuracy_hourly", "line_id", "predictions"),
    "access": ("station_accessibility_hourly", "station", "1"),
}


def _weighted(column, weight):
    return f"SAFE_DIVIDE(SUM({column} * {weight}), SUM({weight}))"


# Measures: table, filter, SQL expression, number format, label, entity (what a row is).
MEASURES = {
    "bus_bunching": ("arrivals", "mode = 'bus' AND headways > 0", _weighted("bunching_rate", "headways"), "pct",
                     "Bus bunching (share of gaps under half the usual)", "route"),
    "tube_bunching": ("arrivals", "mode = 'tube' AND headways > 0", _weighted("bunching_rate", "headways"), "pct",
                      "Tube bunching (share of gaps under half the usual)", "line"),
    "bus_wait": ("arrivals", "mode = 'bus' AND headways > 0",
                 f"{_weighted('average_wait_seconds', 'headways')} / 60", "minutes", "Bus waiting time (minutes)",
                 "route"),
    "tube_wait": ("arrivals", "mode = 'tube' AND headways > 0",
                  f"{_weighted('average_wait_seconds', 'headways')} / 60", "minutes", "Tube waiting time (minutes)",
                  "line"),
    "bus_regularity": ("arrivals", "mode = 'bus' AND headways > 0", _weighted("headway_cv", "headways"), "ratio",
                       "Bus gap irregularity (coefficient of variation)", "route"),
    "tube_regularity": ("arrivals", "mode = 'tube' AND headways > 0", _weighted("headway_cv", "headways"), "ratio",
                        "Tube gap irregularity (coefficient of variation)", "line"),
    "tube_dropped": ("arrivals", "mode = 'tube' AND headways > 0",
                     "SAFE_DIVIDE(SUM(dropped), SUM(arrivals) + SUM(dropped))", "pct",
                     "Trains dropped from the countdown before arriving", "line"),
    "rail_disruption": ("disruption", "in_service_minutes > 0",
                        "SAFE_DIVIDE(SUM(disrupted_minutes), SUM(in_service_minutes))", "pct",
                        "Rail disruption (share of service time)", "line"),
    "rail_incidents": ("disruption", "in_service_minutes > 0",
                       "SAFE_DIVIDE(SUM(incidents) * 6000, SUM(in_service_minutes))", "rate",
                       "Rail incidents per 100 line-hours in service", "line"),
    "tube_countdown": ("accuracy", "mode = 'tube' AND horizon_minutes = 5 AND predictions > 0",
                       _weighted("median_abs_error_seconds", "predictions"), "seconds",
                       "Tube countdown error 5 minutes out (seconds)", "line"),
    "bus_countdown": ("accuracy", "mode = 'bus' AND horizon_minutes = 5 AND predictions > 0",
                      _weighted("median_abs_error_seconds", "predictions"), "seconds",
                      "Bus countdown error 5 minutes out (seconds)", "route"),
    "accessibility": ("access", "accessibility_minutes > 0", "SUM(accessibility_minutes) / 60", "hours",
                      "Station hours with a lift or step-free access notice", "station"),
}

# Disruption by cause: hours add up to the whole, so they can be shown as a pie. The cause is read
# from TfL's reason text (analytics/line_status/02_views.sql); "Not stated" is time with no reason.
CAUSE_SQL = f"""
    SELECT IFNULL(main_cause, 'Not stated') AS d0, SUM(disrupted_minutes) / 60 AS value,
           SUM(disrupted_minutes) AS weight,
           ANY_VALUE((SELECT COUNT(DISTINCT DATE(hour_start, 'Europe/London'))
                      FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW})) AS days
    FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW} AND disrupted_minutes > 0
    GROUP BY d0 ORDER BY value DESC"""

DIMENSIONS = {   # SQL value, SQL sort key, label
    "hour": (f"EXTRACT(HOUR FROM {LONDON})", f"EXTRACT(HOUR FROM {LONDON})", "Hour of day (London time)"),
    "weekday": (f"FORMAT_DATETIME('%a', {LONDON})", f"MOD(EXTRACT(DAYOFWEEK FROM {LONDON}) + 5, 7)", "Day of week"),
    "day_type": (f"IF(EXTRACT(DAYOFWEEK FROM {LONDON}) IN (1, 7), 'Weekend', 'Weekday')",
                 f"IF(EXTRACT(DAYOFWEEK FROM {LONDON}) IN (1, 7), 1, 0)", "Weekday or weekend"),
    "date": ("DATE(hour_start, 'Europe/London')", "DATE(hour_start, 'Europe/London')", "Date"),
}

# Question words naming each subject; buses and trains share some, told apart by the question's
# mode (Tube by default).
SUBJECTS = [
    ("bunching", r"\bbunch\w*|\bclump\w*"),
    ("wait", r"\bwait\w*"),
    ("regularity", r"\b(regular\w*|irregular\w*|evenly|spacing|headways?)\b"),
    ("dropped", r"\b(dropped|vanish\w*|disappear\w*|ghost)\b"),
    ("countdown", r"\b(countdowns?|predict\w*|accura\w*|arrival times?)\b"),
    ("incidents", r"\bincidents?\b"),
    ("disruption", r"\b(disrupt\w*|delays?|reliab\w*|good service)\b"),
    ("accessibility", r"\b(lifts?|step[- ]free|accessib\w*)\b"),
]
BUS = r"\bbus(es)?\b|\broutes?\b"
TUBE = r"\b(tube|underground|trains?|metro)\b"
RELATION = (r"\b(relat\w*|correlat\w*|link\w*|associat\w*|affect\w*|impact\w*|depend\w*|influenc\w*|"
            r"vs\.?|versus|against|compare\w*|change\w*|vary|varies|pattern\w*|trend\w*|differ\w*)\b")
CHART_WORDS = (r"\b(chart|graph|plot|visuali[sz]\w*|draw|show me|analy[sz]\w*|prove|distribution|pie|donut|"
               r"doughnut|breakdown|split)\b")
KINDS = [("pie", r"\b(pie|donut|doughnut)\b"), ("scatter", r"\bscatter\w*"), ("line", r"\bline (chart|graph)\b"),
         ("bar", r"\b(bar|column) (chart|graph)s?\b|\bbars\b")]
CAUSE = r"\b(reasons?|causes?|caused|why|because)\b"

SUGGESTIONS = [
    "Is there a relationship between time of day and bus bunching?",
    "How does Tube waiting time change through the day?",
    "Is bunching related to waiting time on the Tube?",
    "Show a pie chart of the reasons for rail disruption",
    "Do weekends have less rail disruption than weekdays?",
    "Which day of the week has the most rail disruption?",
    "How accurate are Tube countdowns by hour?",
]


def _has(pattern, text):
    return re.search(pattern, text) is not None


def _measure(subject, text):
    """The catalogue measure for a subject, using the question's mode (bus or Tube)."""
    bus = _has(BUS, text) and not _has(TUBE, text)
    return {
        "bunching": "bus_bunching" if bus else "tube_bunching",
        "wait": "bus_wait" if bus else "tube_wait",
        "regularity": "bus_regularity" if bus else "tube_regularity",
        "dropped": "tube_dropped",
        "countdown": "bus_countdown" if bus else "tube_countdown",
        "incidents": "rail_incidents",
        "disruption": "rail_disruption",
        "accessibility": "accessibility",
    }[subject]


def plan(question):
    """{"measures": [1 or 2 names], "dimension": name or None, "series": name or None, "kind": the
    chart type asked for or None} for a question this engine can answer with a chart, else None."""
    text = question.lower()
    kind = next((name for name, pattern in KINDS if _has(pattern, text)), None)
    found = plan_without_kind(text)
    if found is None and kind and _has(CAUSE, text) and _has(r"\b(lines?|tube|rail|trains?)\b", text):
        found = {"measures": ["rail_disruption"], "dimension": "cause", "series": None}
    return {**found, "kind": kind} if found else None


def plan_without_kind(text):
    """The measures and dimension a (lower-case) question asks about, or None."""
    found = sorted((m.start(), subject) for subject, pattern in SUBJECTS for m in [re.search(pattern, text)] if m)
    measures = []
    for _, subject in found:
        name = _measure(subject, text)
        if name not in measures:
            measures.append(name)
    if not measures:
        return None

    hour = _has(r"\b(time of (the )?day|hours?|hourly|rush hours?|peak|morning|evening|night|through the day)\b", text)
    day_type = _has(r"\bweekends?\b", text)
    weekday = _has(r"\b(day of (the )?week|which day|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                   text)
    date = _has(r"\b(over time|trend|daily|each day|per day|by date|day by day|last \d+ days|this month)\b", text)
    entity = _has(r"\b(by line|each line|per line|which lines?|by route|each route|per route|which routes?|"
                  r"by station|which stations?)\b", text)
    asks = _has(RELATION, text) or _has(CHART_WORDS, text)

    if "rail_disruption" in measures and _has(CAUSE, text) and (asks or _has(r"\b(main|most|top)\b", text)):
        return {"measures": ["rail_disruption"], "dimension": "cause", "series": None}
    if len(measures) >= 2 and asks:
        return {"measures": measures[:2], "dimension": None, "series": None}
    measure = measures[0]
    if hour:
        return {"measures": [measure], "dimension": "hour", "series": "day_type" if day_type else None}
    if day_type:
        return {"measures": [measure], "dimension": "day_type", "series": None}
    if weekday:
        return {"measures": [measure], "dimension": "weekday", "series": None}
    if date:
        return {"measures": [measure], "dimension": "date", "series": None}
    if entity and asks:
        return {"measures": [measure], "dimension": "entity", "series": None}
    if _has(CHART_WORDS, text):
        return {"measures": [measure], "dimension": "hour", "series": None}
    return None


def build_sql(p):
    """SQL for a plan, from the catalogue only."""
    if p["dimension"] == "cause":
        return CAUSE_SQL
    if len(p["measures"]) == 2:
        return _pair_sql(*p["measures"])
    table_key, where, expr, *_ = MEASURES[p["measures"][0]]
    table, entity, weight = TABLES[table_key]
    dims = [p["dimension"]] + ([p["series"]] if p["series"] else [])
    selects, groups, orders = [], [], []
    for i, dim in enumerate(dims):
        value, sort = (entity, entity) if dim == "entity" else DIMENSIONS[dim][:2]
        selects += [f"{value} AS d{i}", f"MIN({sort}) AS s{i}"]
        groups.append(f"d{i}")
        orders.append(f"s{i}")
    order = "value DESC" if p["dimension"] == "entity" else ", ".join(orders)
    days = (f"(SELECT COUNT(DISTINCT DATE(hour_start, 'Europe/London')) FROM `{DATASET}.{table}` "
            f"WHERE {WINDOW} AND {where})")
    return f"""
        SELECT {', '.join(selects)}, {expr} AS value, SUM({weight}) AS weight, ANY_VALUE({days}) AS days
        FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY {', '.join(groups)} HAVING SUM({weight}) > 0
        ORDER BY {order} LIMIT 60"""


def _pair_sql(first, second):
    parts = []
    for i, name in enumerate((first, second)):
        table_key, where, expr, *_ = MEASURES[name]
        table, entity, weight = TABLES[table_key]
        parts.append(f"""m{i} AS (
            SELECT {entity} AS entity, hour_start, {expr} AS v{i}, SUM({weight}) AS w{i}
            FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where} GROUP BY entity, hour_start)""")
    return f"""
        WITH {', '.join(parts)}
        SELECT m0.entity, m0.hour_start, v0 AS x, v1 AS y
        FROM m0 JOIN m1 USING (entity, hour_start)
        WHERE v0 IS NOT NULL AND v1 IS NOT NULL AND w0 > 0 AND w1 > 0
        ORDER BY m0.hour_start DESC LIMIT 4000"""


# Writing up -----------------------------------------------------------------------------------

def _fmt(value, kind):
    return {
        "pct": f"{value * 100:.1f}%", "minutes": f"{value:.1f} min", "seconds": f"{value:.0f} s",
        "hours": f"{value:,.0f} h", "ratio": f"{value:.2f}", "rate": f"{value:.2f}",
    }.get(kind, f"{value:.2f}")


def _label(name):
    return MEASURES[name][4]


def _name(name):
    """'Bus bunching (share of gaps...)' -> 'Bus bunching'."""
    return _label(name).split(" (")[0]


def _mid(name):
    """The measure's name inside a sentence: 'bus bunching', but 'Tube waiting time'."""
    text = _name(name)
    return text if text.startswith("Tube") else text[0].lower() + text[1:]


LINE_NAMES = {"hammersmith-city": "Hammersmith & City", "waterloo-city": "Waterloo & City", "dlr": "DLR"}
WEEKDAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday",
            "Sat": "Saturday", "Sun": "Sunday"}


def _entity_name(value, entity):
    if entity == "station":
        return value
    if entity == "route":
        return f"Route {str(value).upper()}"
    return LINE_NAMES.get(value, str(value).replace("-", " ").title())


def _when(x, dim):
    """'at 19:00', 'on Sundays', 'at weekends', 'on Sun 25 Oct', 'on the District line'."""
    if dim == "hour":
        return f"at **{x}**"
    if dim == "weekday":
        return f"on **{WEEKDAYS.get(x, x)}s**"
    if dim == "day_type":
        return "at **weekends**" if x == "Weekend" else "on **weekdays**"
    if dim == "date":
        return f"on **{x}**"
    return f"for **{x}**"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def _strength(r):
    a = abs(r)
    return "a strong" if a >= 0.5 else "a moderate" if a >= 0.3 else "a weak" if a >= 0.1 else "no clear"


def run(p, sources):
    """{"answer": markdown, "chart": description or None} for a plan."""
    rows = [dict(r) for r in sources.query(build_sql(p))]
    rows = [r for r in rows if r.get("value", r.get("y")) is not None]
    if len(rows) < 2:
        return {"answer": "There isn't enough data for that analysis yet.", "chart": None}
    if len(p["measures"]) == 2:
        result = _relationship(p, rows)
    elif p["dimension"] == "cause":
        result = _by_cause(p, rows)
    elif p["series"]:
        result = _by_hour_and_day_type(p, rows)
    else:
        result = _by_dimension(p, rows)
    return _apply_kind(p.get("kind"), result, p)


def _apply_kind(kind, result, p):
    """Use the chart type the visitor asked for when it suits the data; otherwise keep the best
    one and say why."""
    chart = result["chart"]
    if not kind or not chart or kind == chart["kind"]:
        return result
    note = None
    if kind == "pie":
        if p["dimension"] == "cause":
            chart["kind"] = "pie"
        else:
            note = (f"A pie chart shows parts of a whole, and {_mid(p['measures'][0])} is a rate, not a total "
                    f"that adds up, so here is a {chart['kind']} chart instead.")
    elif kind == "scatter":
        note = "A scatter plot compares two measures; ask how one relates to another, e.g. bunching and waiting time."
    elif kind in ("bar", "line") and chart["kind"] in ("bar", "line"):
        if kind == "line" and p["dimension"] in ("entity", "cause", "day_type"):
            note = "A line chart suggests an order or a trend, which these groups don't have, so here is a bar chart."
        else:
            chart["kind"] = kind
            chart["horizontal"] = chart["horizontal"] and kind == "bar"
    elif chart["kind"] in ("scatter", "pie"):
        note = f"A {kind} chart doesn't suit this comparison, so here is a {chart['kind']} chart."
    if note:
        result["answer"] += f"\n\n_{note}_"
    return result


def _by_cause(p, rows):
    total = sum(r["value"] for r in rows)
    stated = [r for r in rows if r["d0"] != "Not stated"]
    data = [{"x": r["d0"], "y": r["value"]} for r in rows[:8]]
    if len(rows) > 8:
        data.append({"x": "Other", "y": sum(r["value"] for r in rows[8:])})
    top = stated[0] if stated else rows[0]
    unstated = total - sum(r["value"] for r in stated)
    text = (f"**{top['d0']}** was the most common stated reason: {top['value']:,.0f} disrupted line-hours, "
            f"{top['value'] / total * 100:.0f}% of all disrupted time")
    if len(stated) > 1:
        text += (f", ahead of {stated[1]['d0'].lower()} ({stated[1]['value'] / total * 100:.0f}%)")
    text += "."
    if unstated:
        text += (f" TfL gave no reason for {unstated / total * 100:.0f}% of the disrupted time (\"Not stated\"). "
                 "Reasons are read from TfL's own wording.")
    text += f"\n\n_Based on {int(rows[0]['days'])} days of data._"
    chart = {"kind": "bar", "title": "Disrupted line-hours by reason", "x": "x", "y": "y", "data": data,
             "format": "hours", "horizontal": True, "x_title": "Reason", "y_title": "Disrupted line-hours",
             "series": None}
    return {"answer": text, "chart": chart}


def _day(value):
    """'2026-10-25' -> 'Sun 25 Oct'."""
    from datetime import date
    d = value if isinstance(value, date) else date.fromisoformat(str(value))
    return f"{d:%a} {d.day} {d:%b}"


def _mean(points):
    total = sum(d["w"] for d in points)
    return sum(d["y"] * d["w"] for d in points) / total if total else 0


def _by_dimension(p, rows):
    name, dim = p["measures"][0], p["dimension"]
    fmt, label, entity = MEASURES[name][3], _label(name), MEASURES[name][5]
    if dim == "hour":
        data = [{"x": f"{int(r['d0']):02d}:00", "y": r["value"], "w": r["weight"]} for r in rows]
    elif dim == "entity":
        data = [{"x": _entity_name(r["d0"], entity), "y": r["value"], "w": r["weight"]} for r in rows[:15]]
    elif dim == "date":
        data = [{"x": _day(r["d0"]), "y": r["value"], "w": r["weight"]} for r in rows]
    else:
        data = [{"x": str(r["d0"]), "y": r["value"], "w": r["weight"]} for r in rows]

    high, low = max(data, key=lambda d: d["y"]), min(data, key=lambda d: d["y"])
    mean = _mean(data)
    spread = (high["y"] - low["y"]) / mean if mean else 0
    what = {"hour": "through the day", "weekday": "across the week", "day_type": "between weekdays and weekends",
            "date": "from day to day", "entity": f"between {entity}s"}[dim]
    if spread >= 0.5:
        text = f"**Yes: {_mid(name)} changes strongly {what}.** "
    elif spread >= 0.2:
        text = f"**Yes: {_mid(name)} changes clearly {what}.** "
    else:
        text = f"**Not much: {_mid(name)} varies little {what}.** "
    text += (f"It is highest {_when(high['x'], dim)} ({_fmt(high['y'], fmt)}) and lowest {_when(low['x'], dim)} "
             f"({_fmt(low['y'], fmt)}), against an average of {_fmt(mean, fmt)}")
    text += f": the highest is {high['y'] / low['y']:.1f} times the lowest." if low["y"] > 0 else "."
    if dim == "hour":
        rush = (7, 8, 9, 16, 17, 18)
        peak = [d for d in data if int(d["x"][:2]) in rush]
        other = [d for d in data if int(d["x"][:2]) not in rush]
        if peak and other:
            text += (f" In the rush hours (07–10 and 16–19) it averages {_fmt(_mean(peak), fmt)}, against "
                     f"{_fmt(_mean(other), fmt)} at other times.")
    text += f"\n\n_Based on {int(rows[0]['days'])} days of data; times are London time._"
    x_title = entity.title() if dim == "entity" else DIMENSIONS[dim][2]
    chart = {"kind": "line" if dim in ("hour", "date") else "bar", "title": f"{_name(name)} by {x_title.lower()}",
             "x": "x", "y": "y", "data": [{"x": d["x"], "y": d["y"]} for d in data], "format": fmt,
             "horizontal": dim == "entity", "x_title": x_title, "y_title": label, "series": None}
    return {"answer": text, "chart": chart}


def _by_hour_and_day_type(p, rows):
    name = p["measures"][0]
    fmt, label = MEASURES[name][3], _label(name)
    data = [{"x": f"{int(r['d0']):02d}:00", "y": r["value"], "w": r["weight"], "series": r["d1"]} for r in rows]
    parts = []
    for series in ("Weekday", "Weekend"):
        points = [d for d in data if d["series"] == series]
        if points:
            high = max(points, key=lambda d: d["y"])
            parts.append(f"on **{series.lower()}s** it averages {_fmt(_mean(points), fmt)} and peaks at "
                         f"{high['x']} ({_fmt(high['y'], fmt)})")
    text = (f"{_name(name)} through the day, weekdays against weekends: " + "; ".join(parts) + "."
            f"\n\n_Based on {int(rows[0]['days'])} days of data; times are London time._")
    chart = {"kind": "line", "title": f"{_name(name)} by hour: weekdays and weekends", "x": "x", "y": "y",
             "data": [{k: d[k] for k in ("x", "y", "series")} for d in data], "format": fmt, "series": "series",
             "horizontal": False, "x_title": "Hour of day (London time)", "y_title": label}
    return {"answer": text, "chart": chart}


def _relationship(p, rows):
    first, second = p["measures"]
    xs, ys = [r["x"] for r in rows], [r["y"] for r in rows]
    r = pearson(xs, ys)
    if r is None:
        return {"answer": "There isn't enough overlapping data to compare those two yet.", "chart": None}
    entity = MEASURES[first][5]
    change = ("tends to rise" if r > 0 else "tends to fall") if abs(r) >= 0.1 else "shows no consistent change"
    text = (f"**There is {_strength(r)} relationship** (correlation r = {r:.2f}, over {len(rows):,} "
            f"{entity}-hours): when {_mid(first)} is higher, {_mid(second)} {change}. "
            "r runs from −1 to 1, and 0 means no straight-line relationship. It shows the two move together, "
            "not that one causes the other.")
    chart = {"kind": "scatter", "title": f"{_name(second)} against {_mid(first)}", "x": "x", "y": "y",
             "data": [{"x": x, "y": y} for x, y in zip(xs, ys)], "format": MEASURES[second][3],
             "x_format": MEASURES[first][3], "series": None, "horizontal": False,
             "x_title": _label(first), "y_title": _label(second)}
    return {"answer": text, "chart": chart}
