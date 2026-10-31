"""Travel advice and traffic tips worked out from the platform's data: the best and worst hours
to travel by Tube and bus (the hourly history), the lines and routes to allow extra time on, the
roads TfL keeps rating as seriously delayed, how long incidents take to clear, and what is wrong
right now (the live feeds). Every tip quotes the figure behind it.

Pure functions, shared like the assistant: `sources` provides query(sql) (the analysis engine's
catalogue SQL), recommendation(area), line_cards() and roads(). A source that fails is skipped,
so the advice still shows what it can.
"""

import re

from . import analysis

ADVICE = (r"\b(advi[cs]\w*|tips?|best time|worst time|when (should|to|is it best to) (i )?(travel|go|leave|ride|drive|"
          r"take|commute|cycle)|avoid|plan (my|a) (trip|journey)|commut\w*|journey planning|should i (take|travel|"
          r"drive|cycle|go)|quietest|least busy|travel smarter)\b")
TOPICS = [
    ("roads", r"\b(roads?|traffic|driv\w*|cars?|motorists?|congestion|roadworks?)\b"),
    ("bikes", r"\b(bikes?|cycl\w*|santander|docks?)\b"),
    ("bus", r"\b(bus(es)?|routes?)\b"),
    ("rail", r"\b(tube|underground|trains?|rail|lines?|overground|dlr|elizabeth|tram)\b"),
]
SUGGESTIONS = [
    "Any travel tips for the Tube?",
    "When is the best time to catch a bus?",
    "Give me traffic tips for driving in London",
    "Any advice for cycling with Santander bikes?",
]
DAYTIME = range(6, 24)   # advice is about when most people travel; overnight is left to the night services


def _station(name):
    """'Mudchute DLR, Cubitt Town' -> 'Mudchute DLR (Cubitt Town)'."""
    place, _, area = name.partition(", ")
    return f"{place} ({area})" if area else name


def asks_for_advice(question):
    return re.search(ADVICE, question.lower()) is not None


def _safely(build, *args):
    try:
        return build(*args) or []
    except Exception:   # noqa: BLE001 - one missing source shouldn't stop the other tips
        return []


def _best_worst(measure, sources):
    _, keep = analysis.hour_profile(measure, sources)
    day = [d for d in keep if d["h"] in DAYTIME]
    if len(day) < 4:
        return None
    return min(day, key=lambda d: d["y"]), max(day, key=lambda d: d["y"])


def _rail_tips(sources):
    tips = []
    waits = _best_worst("tube_wait", sources)
    if waits:
        best, worst = waits
        tips.append(f"**Shortest Tube waits** are around **{best['x']}** ({best['y']:.1f} min on average); the "
                    f"longest in the daytime are around {worst['x']} ({worst['y']:.1f} min). Trains run most often "
                    "in the rush hours, so waits are short then, but trains are fuller.")
    lines = [dict(r) for r in sources.query(analysis.build_sql(
        {"measures": ["rail_disruption"], "dimension": "entity", "series": None}))]
    lines = [r for r in lines if r.get("value") is not None and r["d0"] != "tram"]
    if len(lines) >= 4:
        worst = [analysis._entity_name(r["d0"], "line") for r in lines[:3]]
        best = [analysis._entity_name(r["d0"], "line") for r in lines[-3:]]
        tips.append(f"**Allow extra time on the {', '.join(worst[:-1])} and {worst[-1]} lines**: they were disrupted "
                    f"for the largest share of their service time ({lines[0]['value']:.0%} at worst, planned works "
                    f"included). The {', '.join(best[:-1])} and {best[-1]} were the most reliable.")
    days = [dict(r) for r in sources.query(analysis.build_sql(
        {"measures": ["rail_disruption"], "dimension": "day_type", "series": None}))]
    split = {r["d0"]: r["value"] for r in days if r.get("value") is not None}
    if {"Weekday", "Weekend"} <= split.keys() and split["Weekend"] > split["Weekday"] * 1.1:
        tips.append(f"**Check before travelling at weekends**: lines were disrupted {split['Weekend']:.0%} of the time "
                    f"at weekends against {split['Weekday']:.0%} on weekdays, mostly planned closures.")
    cards = _safely(sources.line_cards)
    problems = [c for c in cards if c.get("level") in ("stopped", "delays")]
    if problems:
        tips.append("**Right now:** " + "; ".join(f"{c['line_name']}: {c['status']}" for c in problems[:5]) + ".")
    return tips


def _bus_tips(sources):
    tips = []
    bunching = _best_worst("bus_bunching", sources)
    if bunching:
        best, worst = bunching
        tips.append(f"**Buses are most evenly spaced around {best['x']}** ({best['y']:.0%} of gaps bunched) and "
                    f"bunch most around **{worst['x']}** ({worst['y']:.0%}): then, a long wait is often followed by "
                    "two buses together, so the second one is usually emptier.")
    waits = _best_worst("bus_wait", sources)
    if waits:
        best, worst = waits
        tips.append(f"**Shortest bus waits** are around {best['x']} ({best['y']:.1f} min), the longest in the daytime "
                    f"around {worst['x']} ({worst['y']:.1f} min), on the frequent routes the platform tracks.")
    found = sources.recommendation("bus")
    if found and found[0].get("slowest"):
        row = found[0]
        slow = row["slowest"][:3]
        tips.append(f"**Allow extra time on routes {', '.join(str(s['route']) for s in slow)}**: they average "
                    f"{min(s['kmh'] for s in slow):.1f}–{max(s['kmh'] for s in slow):.1f} km/h, against about "
                    f"{row['network_kmh']:.0f} km/h across London.")
    return tips


def _bike_tips(sources):
    found = sources.recommendation("bikes")
    if not found:
        return []
    row, tips = found[0], []
    if row.get("worst_hour") is not None:
        hour = int(row["worst_hour"])
        when = (f"around {hour:02d}:00" if hour in DAYTIME else
                f"overnight (around {hour:02d}:00), before the rebalancing vans refill them")
        tips.append(f"**Docking stations are emptiest {when}**, with {row['worst_hour_empty_share']:.0%} of them "
                    "empty: early or late riders should check the app for bikes before setting off.")
    if row.get("peak_empty_share") is not None:
        tips.append(f"**In the weekday rush hours** about {row['peak_empty_share']:.0%} of stations are empty and "
                    f"{row['peak_full_share']:.0%} full: have a second station in mind for picking up and returning.")
    if row.get("most_empty"):
        tips.append("**Often empty, so have another station in mind:** "
                    + "; ".join(_station(s["station_name"]) for s in row["most_empty"][:3])
                    + (". **Often full, so plan where else to return:** "
                       + "; ".join(_station(s["station_name"]) for s in row["most_full"][:3])
                       if row.get("most_full") else "") + ".")
    return tips


def _road_tips(sources):
    tips = []
    live = _safely(sources.roads)
    if live:
        disruptions, corridors = live
        delayed = [c["name"] for c in corridors if not c["good"]]
        serious = [d for d in disruptions if d["is_incident"] and d["weight"] >= 3]
        if delayed:
            tips.append(f"**Right now TfL rates {len(delayed)} main roads as seriously delayed:** "
                        f"{', '.join(delayed[:6])}{'…' if len(delayed) > 6 else ''}.")
        if serious:
            tips.append("**Serious incidents now:** " + "; ".join(
                f"{d['sub_category'] or d['category']} at {d['location']}" for d in serious[:3]) + ".")
    found = sources.recommendation("roads")
    if found:
        row = found[0]
        if row.get("mostly_delayed"):
            names = [r["corridor_name"] for r in row["mostly_delayed"][:5]]
            tips.append(f"**Plan around {', '.join(names)}**: TfL rated them seriously delayed at least half the "
                        "time this week, mostly where roadworks are in place.")
        types = row.get("types") or []
        if types:
            clear = ", ".join(f"{t['sub_category'].lower()} about {t['median_minutes'] / 60:.1f} h" for t in types)
            tips.append(f"**If an incident is reported on your route**, typical clearing times are: {clear}. For "
                        "anything but a breakdown, an alternative route usually saves time.")
    return tips


BUILDERS = {"rail": _rail_tips, "bus": _bus_tips, "bikes": _bike_tips, "roads": _road_tips}
TITLES = {"rail": "Tube and rail", "bus": "Buses", "bikes": "Santander Cycles", "roads": "Driving and traffic"}


def advise(question, sources):
    """{"answer": markdown, "source", "suggestions"} with tips for the transport the question
    names, or a few for each when it names none."""
    text = question.lower()
    topics = [name for name, pattern in TOPICS if re.search(pattern, text)] or list(BUILDERS)
    sections = []
    for topic in topics:
        tips = _safely(BUILDERS[topic], sources)
        if len(topics) > 1:
            tips = tips[:2]
        if tips:
            sections.append(f"**{TITLES[topic]}**\n" + "\n".join(f"- {t}" for t in tips))
    if not sections:
        return {"answer": "I couldn't read the data for travel advice just now. Please try again in a minute.",
                "source": None, "suggestions": SUGGESTIONS}
    answer = ("Travel advice from this platform's data:\n\n" + "\n\n".join(sections)
              + f"\n\n_From the last {analysis.DAYS} days of history, this week's figures and the live feeds. Check "
                "TfL Journey Planner before you travel._")
    others = [s for s in SUGGESTIONS if not any(re.search(p, s.lower()) for n, p in TOPICS if n in topics)]
    return {"answer": answer, "source": "BigQuery history, this week's figures and live TfL feeds",
            "suggestions": others if len(topics) == 1 else []}
