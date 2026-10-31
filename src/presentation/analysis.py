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


# Statistical analyses -------------------------------------------------------------------------
# Beyond "how does X change with Y": distributions, a significance test, anomalies, persistence,
# early warning (lead-lag), concentration and countdown decay. Each has fixed SQL and its own
# statistics; a question chooses one by its wording, and may name the measure to analyse.

STATS = {   # name: (question words, default measure, example question)
    "distribution": (r"\b(distribution|histogram|spread|how (variable|spread)|skew\w*|percentiles?|typical range)\b",
                     "tube_wait", "What is the distribution of Tube waiting times?"),
    "significance": (r"\b(significan\w*|statistically|hypothesis|t-?test|by chance|difference real|real difference)\b",
                     "rail_disruption", "Is the weekend difference in rail disruption statistically significant?"),
    "anomalies": (r"\b(anomal\w*|unusual\w*|outliers?|abnormal|spikes?|stood out|stand out|exceptional)\b",
                  "rail_disruption", "Which line-days had unusual disruption?"),
    "persistence": (r"\b(persist\w*|once (a line is )?disrupted|next hour|stays? disrupted|how long .*last\w*|"
                    r"recover\w*|run length)\b", "rail_disruption",
                    "Once a line is disrupted, how likely is it to stay disrupted the next hour?"),
    "lead_lag": (r"\b(early warning|warn\w*|before (tfl|a|the) (disruption|report)\w*|lead\w*|lags?|ahead of)\b",
                 "tube_wait", "Do Tube waits rise before a disruption is reported?"),
    "concentration": (r"\b(concentrat\w*|pareto|gini|inequal\w*|80/20|few (lines|stations))\b",
                      "rail_disruption", "Is disruption concentrated on a few lines?"),
    "horizon": (r"\b(horizon|as the trains? approach\w*|closer|minutes? (before|out)|improves?)\b",
                "tube_countdown", "How does countdown accuracy improve as the train approaches?"),
}
STAT_SUGGESTIONS = [example for _, _, example in STATS.values()]
DISRUPTED_HOUR = 0.5   # an hour counts as disrupted when at least half its service time was


def stat_plan(question):
    """{"stat": name, "measure": name} for a statistical question, else None."""
    text = question.lower()
    for name, (pattern, default, _) in STATS.items():
        if name == "horizon" and not _has(r"\b(countdowns?|predict\w*|accura\w*)\b", text):
            continue
        if _has(pattern, text):
            found = sorted((m.start(), subject) for subject, p in SUBJECTS for m in [re.search(p, text)] if m)
            measure = _measure(found[0][1], text) if found else default
            if name in ("persistence", "concentration") and measure not in ("rail_disruption", "accessibility"):
                measure = default
            if name == "lead_lag" and measure not in ("tube_wait", "tube_bunching", "tube_regularity"):
                measure = default
            if name == "horizon":
                measure = "tube_countdown"
            return {"stat": name, "measure": measure}
    return None


def _values_sql(measure, group):
    """One value of a measure per group (entity-hour, day, or entity-day)."""
    table_key, where, expr, *_ = MEASURES[measure]
    table, entity, weight = TABLES[table_key]
    cols = {"entity_hour": f"{entity} AS e, hour_start AS t",
            "day": "DATE(hour_start, 'Europe/London') AS d, "
                   "EXTRACT(DAYOFWEEK FROM DATE(hour_start, 'Europe/London')) IN (1, 7) AS weekend",
            "entity_day": f"{entity} AS e, DATE(hour_start, 'Europe/London') AS d"}[group]
    keys = {"entity_hour": "e, t", "day": "d, weekend", "entity_day": "e, d"}[group]
    return f"""
        SELECT {cols}, {expr} AS v FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY {keys} HAVING SUM({weight}) > 0 AND {expr} IS NOT NULL ORDER BY {keys} LIMIT 6000"""


def stat_sql(p):
    name, measure = p["stat"], p["measure"]
    if name == "distribution":
        return _values_sql(measure, "entity_hour")
    if name == "significance":
        return _values_sql(measure, "day")
    if name == "anomalies":
        return _values_sql(measure, "entity_day")
    if name == "persistence":
        return f"""
            WITH h AS (
              SELECT line_name, hour_start, disrupted_share >= {DISRUPTED_HOUR} AS dis,
                     LEAD(disrupted_share >= {DISRUPTED_HOUR}) OVER w AS nxt, LEAD(hour_start) OVER w AS next_hour
              FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW} AND in_service_minutes >= 30
              WINDOW w AS (PARTITION BY line_id ORDER BY hour_start))
            SELECT line_name AS e, COUNTIF(dis) AS dis, COUNTIF(dis AND nxt) AS stay,
                   COUNTIF(NOT dis) AS ok, COUNTIF(NOT dis AND nxt) AS start
            FROM h WHERE next_hour = TIMESTAMP_ADD(hour_start, INTERVAL 1 HOUR)
            GROUP BY e ORDER BY e"""
    if name == "lead_lag":
        _, where, expr, *_ = MEASURES[measure]
        return f"""
            WITH a AS (SELECT line_id, hour_start, {expr} AS v FROM `{DATASET}.arrival_line_hourly`
                       WHERE {WINDOW} AND {where} GROUP BY line_id, hour_start),
                 d AS (SELECT line_id, hour_start, disrupted_share AS s FROM `{DATASET}.line_disruption_hourly`
                       WHERE {WINDOW} AND in_service_minutes > 0)
            SELECT k, CORR(a.v, d.s) AS r, COUNT(*) AS n
            FROM a, UNNEST(GENERATE_ARRAY(-3, 3)) AS k
            JOIN d ON d.line_id = a.line_id AND d.hour_start = TIMESTAMP_ADD(a.hour_start, INTERVAL k HOUR)
            GROUP BY k ORDER BY k"""
    if name == "concentration":
        table_key, where, *_ = MEASURES[measure]
        table, entity, _ = TABLES[table_key]
        hours = "SUM(accessibility_minutes) / 60" if measure == "accessibility" else "SUM(disrupted_minutes) / 60"
        label = entity if measure == "accessibility" else "line_name"
        return f"""
            SELECT {label} AS e, {hours} AS v FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
            GROUP BY e HAVING v > 0 ORDER BY v DESC"""
    return f"""
        SELECT horizon_minutes AS h, {_weighted('median_abs_error_seconds', 'predictions')} AS error,
               {_weighted('mean_error_seconds', 'predictions')} AS bias, SUM(predictions) AS n
        FROM `{DATASET}.arrival_accuracy_hourly` WHERE {WINDOW} AND mode = 'tube' AND predictions > 0
        GROUP BY h ORDER BY h DESC"""


def _quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def run_stat(p, sources):
    rows = [dict(r) for r in sources.query(stat_sql(p))]
    if len(rows) < 2:
        return {"answer": "There isn't enough data for that analysis yet.", "chart": None}
    return globals()[f"_stat_{p['stat']}"](p, rows)


def _stat_distribution(p, rows):
    measure = p["measure"]
    fmt, entity = MEASURES[measure][3], MEASURES[measure][5]
    values = sorted(float(r["v"]) for r in rows)
    n, mean = len(values), sum(values) / len(values)
    q1, median, q3, p90 = (_quantile(values, q) for q in (0.25, 0.5, 0.75, 0.9))
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0
    skew = (sum((v - mean) ** 3 for v in values) / n) / sd ** 3 if sd else 0
    top = _quantile(values, 0.99)
    width = (top - values[0]) / 15 or 1
    bins = [0] * 15
    for v in values:
        bins[min(int((v - values[0]) / width), 14)] += 1
    data = [{"x": f"{_fmt(values[0] + i * width, fmt)}" + ("+" if i == 14 else ""), "y": count / n}
            for i, count in enumerate(bins)]
    shape = ("right-skewed: most hours are close to the median, with a long tail of much worse hours"
             if skew > 0.5 else "left-skewed: a tail of unusually good hours" if skew < -0.5
             else "roughly symmetric around the median")
    text = (f"Across {n:,} {entity}-hours, the median {_mid(measure)} was **{_fmt(median, fmt)}**; half of the "
            f"hours were between {_fmt(q1, fmt)} and {_fmt(q3, fmt)} (the interquartile range), and 1 hour in 10 "
            f"was above {_fmt(p90, fmt)}. The distribution is {shape} (skewness {skew:.2f}).")
    chart = {"kind": "bar", "title": f"Distribution of {_mid(measure)} per {entity}-hour", "x": "x", "y": "y",
             "data": data, "format": "pct", "horizontal": False, "series": None,
             "x_title": _label(measure), "y_title": f"Share of {entity}-hours"}
    return {"answer": text, "chart": chart}


def _stat_significance(p, rows):
    from itertools import combinations
    from random import Random
    measure = p["measure"]
    fmt = MEASURES[measure][3]
    weekend = [float(r["v"]) for r in rows if r["weekend"]]
    weekday = [float(r["v"]) for r in rows if not r["weekend"]]
    if len(weekend) < 2 or len(weekday) < 2:
        return {"answer": "There aren't enough weekend and weekday days yet to test the difference; it needs at "
                          "least two of each.", "chart": None}
    mean_we, mean_wd = sum(weekend) / len(weekend), sum(weekday) / len(weekday)
    observed = mean_we - mean_wd
    values, k = weekend + weekday, len(weekend)
    total = sum(values)
    # Permutation test: how often does a random split of the same days differ as much?
    if math.comb(len(values), k) <= 20000:
        splits = (list(c) for c in combinations(range(len(values)), k))
        count = math.comb(len(values), k)
    else:
        rng = Random(7)
        splits = (rng.sample(range(len(values)), k) for _ in range(20000))
        count = 20000
    extreme = 0
    for split in splits:
        s = sum(values[i] for i in split)
        if abs(s / k - (total - s) / (len(values) - k)) >= abs(observed) - 1e-12:
            extreme += 1
    p_value = extreme / count
    verdict = ("**unlikely to be chance**" if p_value < 0.05 else
               "**could well be chance**: more weeks of data are needed to be sure")
    higher = "weekends" if observed > 0 else "weekdays"
    text = (f"{_name(measure)} averaged {_fmt(mean_we, fmt)} on weekend days and {_fmt(mean_wd, fmt)} on weekdays: "
            f"higher on {higher}. A permutation test (re-splitting the same {len(values)} days at random) gives "
            f"p = {p_value:.3f}, so the difference is {verdict}. ({len(weekday)} weekdays and {len(weekend)} "
            "weekend days so far.)")
    if min(len(weekend), len(weekday)) < 4:
        text += " With so few days in one group, treat this as provisional."
    data = [{"x": "Weekend" if r["weekend"] else "Weekday", "y": float(r["v"]), "label": _day(r["d"])} for r in rows]
    chart = {"kind": "strip", "title": f"{_name(measure)} per day: weekdays against weekends", "x": "x", "y": "y",
             "data": data, "format": fmt, "horizontal": False, "series": None, "x_title": "",
             "y_title": _label(measure)}
    return {"answer": text, "chart": chart}


def _stat_anomalies(p, rows):
    measure = p["measure"]
    fmt, entity = MEASURES[measure][3], MEASURES[measure][5]
    by = {}
    for r in rows:
        by.setdefault(r["e"], []).append(r)
    found = []
    for e, days in by.items():
        if len(days) < 4:
            continue
        vals = [float(d["v"]) for d in days]
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
        if not sd:
            continue
        for d in days:
            z = (float(d["v"]) - mean) / sd
            found.append({"e": _entity_name(e, entity), "d": _day(d["d"]), "v": float(d["v"]), "mean": mean, "z": z})
    unusual = sorted((f for f in found if abs(f["z"]) >= 2), key=lambda f: -abs(f["z"]))
    if not unusual:
        return {"answer": f"No {entity}-day stood out: none was more than 2 standard deviations from that "
                          f"{entity}'s own usual {_mid(measure)}.", "chart": None}
    top = unusual[0]
    text = (f"**{len(unusual)} {entity}-day{'s' if len(unusual) != 1 else ''}** stood out (more than 2 standard "
            f"deviations from that {entity}'s own usual level). The most unusual: **{top['e']} on {top['d']}**, "
            f"unusually {'high' if top['z'] > 0 else 'low'} at {_fmt(top['v'], fmt)} against its usual "
            f"{_fmt(top['mean'], fmt)} (z = {top['z']:.1f}). Each {entity} is compared with itself, so one that is "
            f"always high isn't flagged just for that. Positive z is worse than usual for this measure.")
    data = [{"x": f"{f['e']} · {f['d']}", "y": f["z"]} for f in unusual[:12]]
    chart = {"kind": "bar", "title": "Most unusual days (z-score against the line's own normal)", "x": "x", "y": "y",
             "data": data, "format": "ratio", "horizontal": True, "series": None, "x_title": "",
             "y_title": "Standard deviations from normal (z)"}
    return {"answer": text, "chart": chart}


def _stat_persistence(p, rows):
    dis = sum(r["dis"] for r in rows)
    stay = sum(r["stay"] for r in rows)
    ok = sum(r["ok"] for r in rows)
    start = sum(r["start"] for r in rows)
    if not dis or not ok:
        return {"answer": "There aren't enough disrupted hours yet to measure persistence.", "chart": None}
    p_stay, p_start = stay / dis, start / ok
    run = 1 / (1 - p_stay) if p_stay < 1 else float("inf")
    text = (f"A disrupted hour (half or more of the service time disrupted) is followed by another disrupted hour "
            f"**{p_stay:.0%}** of the time; an undisrupted hour turns into a disrupted one only **{p_start:.0%}** "
            f"of the time. In a simple Markov model that means a disruption, once started, lasts about "
            f"**{run:.0f} hours** on average, and is {p_stay / p_start:.0f} times as likely to continue as to "
            "start. Planned closures count, which makes long runs more likely.")
    data = sorted(({"x": r["e"], "y": r["stay"] / r["dis"]} for r in rows if r["dis"] >= 10),
                  key=lambda d: -d["y"])
    chart = {"kind": "bar", "title": "Chance a disrupted hour is followed by another, by line", "x": "x", "y": "y",
             "data": data, "format": "pct", "horizontal": True, "series": None, "x_title": "",
             "y_title": "Chance the next hour is disrupted too"}
    return {"answer": text, "chart": chart}


def _stat_lead_lag(p, rows):
    measure = p["measure"]
    data = [{"x": "same hour" if int(r["k"]) == 0 else f"{int(r['k']):+d} h", "y": float(r["r"]), "k": int(r["k"])}
            for r in rows if r["r"] is not None]
    best = max(data, key=lambda d: d["y"])
    same = next((d for d in data if d["k"] == 0), best)
    lead = [d for d in data if d["k"] > 0]
    warning = lead and max(d["y"] for d in lead) > same["y"] + 0.03
    text = (f"Correlation between {_mid(measure)} and rail disruption, with {_mid(measure)} measured up to 3 hours "
            f"before (+) or after (−) the disruption: strongest at **{best['x']}** (r = {best['y']:.2f}).")
    text += (f" {_name(measure)} rises **before** disruption shows in TfL's status, so it could serve as an early "
             "warning." if warning else
             f" {_name(measure)} moves **with or after** the disruption, not before it: no early warning in the "
             "data so far.")
    chart = {"kind": "bar", "title": f"Correlation of {_mid(measure)} with disruption, by hours ahead",
             "x": "x", "y": "y", "data": [{"x": d["x"], "y": d["y"]} for d in data], "format": "ratio",
             "horizontal": False, "series": None,
             "x_title": f"{_name(measure)} measured this many hours before (+) or after (−) the disruption",
             "y_title": "Correlation (r)"}
    return {"answer": text, "chart": chart}


def _stat_concentration(p, rows):
    measure = p["measure"]
    values = [float(r["v"]) for r in rows]
    total, n = sum(values), len(values)
    cumulative, running = [], 0
    for r, v in zip(rows, values):
        running += v
        cumulative.append({"x": str(r["e"]), "y": running / total})
    top = max(1, round(n * 0.2))
    top_share = cumulative[top - 1]["y"]
    ascending = sorted(values)
    gini = (2 * sum((i + 1) * v for i, v in enumerate(ascending)) / (n * total) - (n + 1) / n) if total else 0
    what = "stations" if measure == "accessibility" else "lines"
    hours = "hours with a lift or step-free access notice" if measure == "accessibility" else "disrupted hours"
    text = (f"The top {top} of {n} {what} (20%) account for **{top_share:.0%}** of all {hours}. The Gini "
            f"coefficient is **{gini:.2f}** (0 = spread evenly, 1 = all on one {what[:-1]}): "
            + ("strongly concentrated." if gini >= 0.5 else "moderately concentrated." if gini >= 0.3
               else "fairly evenly spread."))
    chart = {"kind": "line", "title": f"Cumulative share of {hours} (Pareto curve)", "x": "x", "y": "y",
             "data": cumulative[:40], "format": "pct", "horizontal": False, "series": None,
             "x_title": f"{what.title()}, most affected first", "y_title": f"Cumulative share of {hours}"}
    return {"answer": text, "chart": chart}


def _stat_horizon(p, rows):
    data = [{"x": f"{int(r['h'])} min before", "y": float(r["error"]), "bias": float(r["bias"])} for r in rows]
    far, near = data[0], data[-1]
    late = near["bias"] > 0
    text = (f"TfL's Tube countdown gets steadily more accurate as the train approaches: the typical error falls "
            f"from **{far['y']:.0f} s** {far['x']} to **{near['y']:.0f} s** {near['x']}. On average trains arrive "
            f"{abs(near['bias']):.0f} s {'later' if late else 'earlier'} than shown {near['x']} "
            f"({abs(far['bias']):.0f} s {far['x']}), so the countdown is {'optimistic' if late else 'cautious'}.")
    chart = {"kind": "line", "title": "Typical Tube countdown error by time before arrival", "x": "x", "y": "y",
             "data": [{"x": d["x"], "y": d["y"]} for d in data], "format": "seconds", "horizontal": False,
             "series": None, "x_title": "When the countdown was shown", "y_title": "Typical error (seconds)"}
    return {"answer": text, "chart": chart}
