"""Analyses the "Ask the data" assistant can run and chart: each is a fixed read-only SQL query on
the serving dataset (tfl_portfolio, last 7 complete London days) or the business recommendation
figures, a short written finding, and a chart description the app draws.

Pure functions: the caller passes `sources` (see src/presentation/assistant.py) with
`query(sql) -> rows` and `recommendation(area) -> (row, text module)`. No SQL is built from what a
visitor types; a question only chooses one of the analyses below.

Chart description: {"kind": "bar" | "line", "title", "x", "y", "data": [rows], "format": one of
"pct", "minutes", "hours", "seconds", "kmh", "count"}. Bars are drawn horizontally when
"horizontal" is true (rankings with long names).
"""

import re

DATASET = "transport-for-london-511318.tfl_portfolio"
WINDOW = ("DATE(hour_start, 'Europe/London') BETWEEN DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 7 DAY) "
          "AND DATE_SUB(CURRENT_DATE('Europe/London'), INTERVAL 1 DAY)")

SQL = {
    "rail_by_line": f"""
        SELECT line_name AS line, SAFE_DIVIDE(SUM(disrupted_minutes), SUM(in_service_minutes)) AS disrupted
        FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW}
        GROUP BY line HAVING SUM(in_service_minutes) > 0 ORDER BY disrupted DESC""",
    "rail_by_hour": f"""
        SELECT EXTRACT(HOUR FROM DATETIME(hour_start, 'Europe/London')) AS hour,
               SAFE_DIVIDE(SUM(disrupted_minutes), SUM(in_service_minutes)) AS disrupted
        FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW}
        GROUP BY hour HAVING SUM(in_service_minutes) > 0 ORDER BY hour""",
    "rail_by_day": f"""
        SELECT FORMAT_DATE('%a %d %b', DATE(hour_start, 'Europe/London')) AS day,
               1 - SAFE_DIVIDE(SUM(disrupted_minutes), SUM(in_service_minutes)) AS good_service
        FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW}
        GROUP BY DATE(hour_start, 'Europe/London'), day HAVING SUM(in_service_minutes) > 0
        ORDER BY DATE(hour_start, 'Europe/London')""",
    "rail_causes": f"""
        SELECT main_cause AS cause, SUM(disrupted_minutes) / 60 AS hours
        FROM `{DATASET}.line_disruption_hourly`
        WHERE {WINDOW} AND main_cause IS NOT NULL AND main_cause != 'Not stated'
        GROUP BY cause ORDER BY hours DESC LIMIT 10""",
    # Many stations have a notice the whole week (a tie at the top), so it also counts them.
    "accessibility": f"""
        SELECT station, hours, COUNTIF(hours >= 0.95 * longest) OVER () AS whole_period, COUNT(*) OVER () AS stations
        FROM (SELECT station, SUM(accessibility_minutes) / 60 AS hours,
                     MAX(SUM(accessibility_minutes) / 60) OVER () AS longest
              FROM `{DATASET}.station_accessibility_hourly` WHERE {WINDOW} GROUP BY station)
        ORDER BY hours DESC, station LIMIT 10""",
    "tube_bunching": f"""
        SELECT line_id AS line, SAFE_DIVIDE(SUM(bunching_rate * headways), SUM(headways)) AS bunching
        FROM `{DATASET}.arrival_line_hourly` WHERE {WINDOW} AND mode = 'tube' AND headways > 0
        GROUP BY line ORDER BY bunching DESC""",
    "tube_wait": f"""
        SELECT line_id AS line, SAFE_DIVIDE(SUM(average_wait_seconds * headways), SUM(headways)) / 60 AS wait
        FROM `{DATASET}.arrival_line_hourly` WHERE {WINDOW} AND mode = 'tube' AND headways > 0
        GROUP BY line ORDER BY wait DESC""",
    "prediction_accuracy": f"""
        SELECT line_id AS line, SAFE_DIVIDE(SUM(median_abs_error_seconds * predictions), SUM(predictions)) AS error
        FROM `{DATASET}.arrival_accuracy_hourly`
        WHERE {WINDOW} AND mode = 'tube' AND horizon_minutes = 5 AND predictions > 0
        GROUP BY line ORDER BY error DESC""",
}

CHART_WORDS = (r"\b(chart|graph|plot|visuali[sz]\w*|trend|over time|by hour|hour of (the )?day|time of day|"
               r"by day|daily|breakdown|compare|comparison|analy[sz]\w*|distribution|rank\w*|show me)\b")

CHART_SUGGESTIONS = [
    "Chart disruption by line this week",
    "Graph disruption by hour of day",
    "Chart the main causes of disruption",
    "Plot Tube bunching by line",
    "Chart the slowest bus routes",
    "Graph how long road incidents take to clear",
]


LINE_NAMES = {"hammersmith-city": "Hammersmith & City", "waterloo-city": "Waterloo & City"}


def _line(line_id):
    return LINE_NAMES.get(line_id, line_id.replace("-", " ").title())


def _pct(value):
    return f"{value * 100:.1f}%"


def _has(pattern, text):
    return re.search(pattern, text) is not None


def choose(question):
    """The analysis a question asks for, or None if it isn't asking for a chart or analysis."""
    text = question.lower()
    if not _has(CHART_WORDS, text) and not _has(r"\b(causes?|by line|each line|per line|every line)\b", text):
        return None
    tube = _has(r"\b(tube|underground|trains?|lines?|dlr|overground|elizabeth|stations?|platforms?)\b", text)
    if _has(r"\b(lifts?|step[- ]free|accessib\w*)\b", text):
        return "accessibility"
    if _has(r"\b(predict\w*|accura\w*|countdowns?)\b", text):
        return "prediction_accuracy"
    if _has(r"\bbus(es)?\b|\broutes?\b", text):
        return "bus_slowest" if _has(r"\b(slow\w*|speeds?|fast\w*)\b", text) else "bus_bunched"
    if tube and _has(r"\b(bunch\w*|headways?|clump\w*)\b", text):
        return "tube_bunching"
    if tube and _has(r"\b(waits?|waiting)\b", text):
        return "tube_wait"
    if _has(r"\b(bikes?|cycles?|docks?|docking|santander)\b", text):
        return "bikes"
    if _has(r"\b(roads?|traffic|incidents?|roadworks?|corridors?)\b", text):
        return "roads_clear" if _has(r"\b(clear\w*|types?|long|duration)\b", text) else "roads_delayed"
    if _has(r"\b(causes?|reasons?|why)\b", text):
        return "rail_causes"
    if _has(r"\b(hours?|time of day|hourly)\b", text):
        return "rail_by_hour"
    if not _has(r"\b(disrupt\w*|service|lines?|rail|tube|underground|tfl|transport|reliab\w*|delays?)\b", text):
        return None   # a chart of something this platform doesn't collect
    if _has(r"\b(day|days|daily|trend|over time|week)\b", text) and not _has(r"\bby line\b", text):
        return "rail_by_day"
    return "rail_by_line"


def _rows(sources, name):
    rows = [dict(r) for r in sources.query(SQL[name])]
    if name in ("tube_bunching", "tube_wait", "prediction_accuracy"):
        rows = [{**r, "line": _line(r["line"])} for r in rows]
    return rows


def run(name, sources):
    """{"answer": text, "chart": description or None} for one analysis."""
    if name in SQL:
        rows = [r for r in _rows(sources, name) if all(v is not None for v in r.values())]
        if not rows:
            return {"answer": "There isn't enough data for that chart yet.", "chart": None}
    return globals()[f"_{name}"](rows if name in SQL else None, sources)


def _chart(kind, title, x, y, data, fmt, horizontal=False):
    return {"kind": kind, "title": title, "x": x, "y": y, "data": data, "format": fmt, "horizontal": horizontal}


def _rail_by_line(rows, _):
    worst, best = rows[0], rows[-1]
    text = (f"Over the last 7 complete days, the **{worst['line']}** line was disrupted most "
            f"({_pct(worst['disrupted'])} of its service time) and **{best['line']}** least "
            f"({_pct(best['disrupted'])}). Planned works count as disruption.")
    return {"answer": text, "chart": _chart("bar", "Share of service time disrupted, by line", "line",
                                             "disrupted", rows, "pct", horizontal=True)}


def _rail_by_hour(rows, _):
    worst = max(rows, key=lambda r: r["disrupted"])
    best = min(rows, key=lambda r: r["disrupted"])
    text = (f"Across all lines, **{worst['hour']:02d}:00** was the most disrupted hour "
            f"({_pct(worst['disrupted'])} of service time) and **{best['hour']:02d}:00** the least "
            f"({_pct(best['disrupted'])}), over the last 7 complete days (London time).")
    data = [{"hour": f"{r['hour']:02d}:00", "disrupted": r["disrupted"]} for r in rows]
    return {"answer": text, "chart": _chart("bar", "Share of service time disrupted, by hour of day", "hour",
                                             "disrupted", data, "pct")}


def _rail_by_day(rows, _):
    best = max(rows, key=lambda r: r["good_service"])
    worst = min(rows, key=lambda r: r["good_service"])
    text = (f"Good service ranged from **{_pct(worst['good_service'])}** ({worst['day']}) to "
            f"**{_pct(best['good_service'])}** ({best['day']}) across all lines.")
    return {"answer": text, "chart": _chart("line", "Good service across all lines, by day", "day",
                                             "good_service", rows, "pct")}


def _rail_causes(rows, _):
    total = sum(r["hours"] for r in rows)
    top = rows[0]
    text = (f"**{top['cause']}** caused the most disruption: {top['hours']:.0f} line-hours, "
            f"{_pct(top['hours'] / total)} of the disrupted time with a stated cause, over the last 7 days. "
            "Time where TfL gave no reason is left out.")
    return {"answer": text, "chart": _chart("bar", "Disrupted line-hours by main cause", "cause", "hours",
                                             rows, "hours", horizontal=True)}


def _accessibility(rows, _):
    whole, stations = rows[0]["whole_period"], rows[0]["stations"]
    text = (f"{stations} stations had a lift or step-free access notice in the last 7 days, and "
            f"**{whole}** of them had one for (almost) the whole period. The chart shows 10 of the longest.")
    return {"answer": text, "chart": _chart("bar", "Hours with an accessibility notice, by station", "station",
                                             "hours", rows, "hours", horizontal=True)}


def _tube_bunching(rows, _):
    top, low = rows[0], rows[-1]
    text = (f"Trains bunched most on the **{top['line']}** ({_pct(top['bunching'])} of gaps under half the "
            f"usual gap) and least on the **{low['line']}** ({_pct(low['bunching'])}), over the last 7 days.")
    return {"answer": text, "chart": _chart("bar", "Share of train gaps bunched, by line", "line", "bunching",
                                             rows, "pct", horizontal=True)}


def _tube_wait(rows, _):
    top, low = rows[0], rows[-1]
    text = (f"Passengers arriving at random waited longest on the **{top['line']}** (about "
            f"{top['wait']:.1f} min) and shortest on the **{low['line']}** ({low['wait']:.1f} min).")
    return {"answer": text, "chart": _chart("bar", "Average wait for a train (minutes), by line", "line", "wait",
                                             rows, "minutes", horizontal=True)}


def _prediction_accuracy(rows, _):
    top, low = rows[0], rows[-1]
    text = (f"Five minutes before a train arrived, TfL's countdown was furthest off on the **{top['line']}** "
            f"(typically {top['error']:.0f} s) and closest on the **{low['line']}** ({low['error']:.0f} s).")
    return {"answer": text, "chart": _chart("bar", "Typical countdown error 5 minutes out (seconds), by line",
                                             "line", "error", rows, "seconds", horizontal=True)}


def _recommendation(sources, area):
    found = sources.recommendation(area)
    if not found:
        raise LookupError(area)
    row, text = found
    return row, text.period(row)


def _bus_bunched(_, sources):
    row, period = _recommendation(sources, "bus")
    data = [{"route": f"Route {b['route']}", "bunching": b["bunching_rate"]} for b in row.get("most_bunched") or []]
    text = (f"The most bunched frequent routes ({period}): " +
            ", ".join(f"{d['route']} ({_pct(d['bunching'])})" for d in data) + ". The network average is "
            f"{_pct(row['bunching_rate'])}.")
    return {"answer": text, "chart": _chart("bar", "Share of gaps bunched, most bunched frequent routes", "route",
                                             "bunching", data, "pct", horizontal=True)}


def _bus_slowest(_, sources):
    row, period = _recommendation(sources, "bus")
    data = [{"route": f"Route {s['route']}", "speed": s["kmh"]} for s in row.get("slowest") or []]
    text = (f"The slowest big routes ({period}) average {data[0]['speed']:.1f}–{data[-1]['speed']:.1f} km/h, "
            f"against about {row['network_kmh']:.0f} km/h across London.") if data else "No route had enough data yet."
    return {"answer": text, "chart": _chart("bar", "Average speed (km/h), slowest routes", "route", "speed", data,
                                             "kmh", horizontal=True)}


def _bikes(_, sources):
    row, period = _recommendation(sources, "bikes")
    data = ([{"station": f"{s['station_name']} (empty)", "share": s["share"]} for s in row.get("most_empty") or []]
            + [{"station": f"{s['station_name']} (full)", "share": s["share"]} for s in row.get("most_full") or []])
    text = (f"The docking stations that failed riders most ({period}): the share of time each was empty "
            f"(no bike to take) or full (nowhere to return one). Across the network, stations were empty "
            f"{_pct(row['empty_share'])} and full {_pct(row['full_share'])} of the time.")
    return {"answer": text, "chart": _chart("bar", "Share of time empty or full, worst stations", "station",
                                             "share", data, "pct", horizontal=True)}


def _roads_clear(_, sources):
    row, period = _recommendation(sources, "roads")
    data = [{"type": t["sub_category"], "hours": t["median_minutes"] / 60} for t in row.get("types") or []]
    text = (f"Typical time to clear an incident ({period}), by type with at least two cleared: "
            + ", ".join(f"{d['type'].lower()} {d['hours']:.1f} h" for d in data) + ".") if data else (
        "Not enough incidents have cleared yet.")
    return {"answer": text, "chart": _chart("bar", "Median hours to clear, by incident type", "type", "hours", data,
                                             "hours", horizontal=True)}


def _roads_delayed(_, sources):
    row, period = _recommendation(sources, "roads")
    data = [{"road": r["corridor_name"], "roadworks": r["works"]} for r in row.get("mostly_delayed") or []]
    text = (f"{row['roads_mostly_delayed']} of {row['roads']} main roads were rated Serious or worse at least "
            f"half the time ({period}); the chart shows the roadworks on each.")
    return {"answer": text, "chart": _chart("bar", "Roadworks on the main roads rated Serious", "road",
                                             "roadworks", data, "count", horizontal=True)}
