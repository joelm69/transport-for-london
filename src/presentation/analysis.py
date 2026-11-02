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
visitor's words and reads the serving dataset (tfl_portfolio) over the last 28 complete days.
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

# Tables: name, the column naming a line / route / station and the weight behind each average.
TABLES = {
    "arrivals": ("arrival_line_hourly", "line_id", "headways"),
    "disruption": ("line_disruption_hourly", "line_id", "in_service_minutes"),
    "accuracy": ("arrival_accuracy_hourly", "line_id", "predictions"),
    "access": ("station_accessibility_hourly", "station", "1"),
    "bikes": ("bike_station_hourly", "station_name", "observed_minutes"),
    "corridors": ("road_corridor_hourly", "corridor_name", "observed_minutes"),
    "road_incidents": ("road_incident_hourly", "'London'", "1"),
    "road_types": ("road_incident_type_hourly", "'London'", "1"),
    "routes": ("bus_route_bands", "route", "polls"),
}
# The all-routes bus table has time bands, not hours: hour_start is the band's start.
BANDS = {0: "Night 00–05", 5: "Early 05–07", 7: "Morning peak 07–10", 10: "Midday 10–16", 16: "Evening peak 16–19",
         19: "Evening 19–24"}


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
    "bike_empty": ("bikes", "observed_minutes > 0", "SAFE_DIVIDE(SUM(empty_minutes), SUM(observed_minutes))", "pct",
                   "Bike stations empty (share of station time)", "station"),
    "bike_full": ("bikes", "observed_minutes > 0", "SAFE_DIVIDE(SUM(full_minutes), SUM(observed_minutes))", "pct",
                  "Bike stations full (share of station time)", "station"),
    "bike_out_of_use": ("bikes", "observed_minutes > 0 AND docks > 0",
                        "SAFE_DIVIDE(SUM(avg_out_of_use_docks * observed_minutes), SUM(docks * observed_minutes))",
                        "pct", "Bike docks out of use (share of docks)", "station"),
    "bike_available": ("bikes", "observed_minutes > 0", _weighted("avg_bikes", "observed_minutes"), "number",
                       "Bikes available per station", "station"),
    "road_delayed": ("corridors", "observed_minutes > 0", "SAFE_DIVIDE(SUM(delayed_minutes), SUM(observed_minutes))",
                     "pct", "Main roads at Serious or worse (share of time)", "road"),
    "road_incidents": ("road_incidents", "TRUE", "AVG(incidents)", "number", "Road incidents reported per hour",
                       "network"),
    "road_collisions": ("road_types", "TRUE", "AVG(collisions)", "number", "Road collisions reported per hour",
                        "network"),
    "road_breakdowns": ("road_types", "TRUE", "AVG(breakdowns)", "number", "Vehicle breakdowns reported per hour",
                        "network"),
    "bus_network_bunching": ("routes", "polls > 0 AND bunching_rate IS NOT NULL", _weighted("bunching_rate", "polls"),
                             "pct", "Bus bunching, all routes (share of gaps bunched)", "route"),
    "bus_network_wait": ("routes", "polls > 0 AND average_wait_minutes IS NOT NULL",
                         _weighted("average_wait_minutes", "polls"), "minutes", "Bus waiting time, all routes (minutes)",
                         "route"),
    "bus_network_gap": ("routes", "polls > 0 AND typical_gap_minutes IS NOT NULL",
                        _weighted("typical_gap_minutes", "polls"), "minutes",
                        "Gap between buses, all routes (minutes)", "route"),
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

# Disrupted hours by line and reason, for a stacked bar chart.
CAUSE_BY_LINE_SQL = f"""
    SELECT line_name AS x, IFNULL(main_cause, 'Not stated') AS series, SUM(disrupted_minutes) / 60 AS y
    FROM `{DATASET}.line_disruption_hourly` WHERE {WINDOW} AND disrupted_minutes > 0
    GROUP BY x, series ORDER BY x"""

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
    ("collisions", r"\b(accidents?|collisions?|crash\w*|smash\w*|pile[- ]?ups?)\b"),
    ("breakdowns", r"\bbreak ?downs?\b|\bbroken[- ]down\b"),
    ("incidents", r"\bincidents?\b"),
    ("disruption", r"\b(disrupt\w*|delays?|reliab\w*|good service)\b"),
    ("accessibility", r"\b(lifts?|step[- ]free|accessib\w*)\b"),
    ("empty", r"\bempty\b|\bno bikes?\b"),
    ("full", r"\bfull\b|\bnowhere to return\b"),
    ("out_of_use", r"\bout of use\b|\bbroken docks?\b"),
    ("availability", r"\b(bikes? available|availability|how many bikes)\b"),
    ("congestion", r"\b(congest\w*|serious(ly)? delayed|main roads?|corridors?)\b"),
    ("gap", r"\b(gaps? between|frequency|frequent)\b"),
]
BIKE = r"\b(bikes?|bicycles?|cycling|cyclists?|cycle hire|docks?|docking|santander)\b"   # not "daily cycle"
ROAD = (r"\b(roads?|traffic|roadworks?|corridors?|congestion|a\d{1,3}|accidents?|collisions?|crash\w*|"
        r"break ?downs?|driv\w+)\b")
ALL_ROUTES = (r"\b(all|every|each) (bus )?routes\b|\bnetwork[- ]wide\b|\bwhole (bus )?network\b|\b675\b|"
              r"\bby routes?\b|\bwhich routes?\b|\bper route\b|\btime bands?\b")
BUS = r"\bbus(es)?\b|\broutes?\b"
TUBE = r"\b(tube|underground|trains?|metro)\b"
RELATION = (r"\b(relat\w*|correlat\w*|link\w*|associat\w*|affect\w*|impact\w*|depend\w*|influenc\w*|"
            r"vs\.?|versus|against|compare\w*|change\w*|vary|varies|pattern\w*|trend\w*|differ\w*|"
            r"makes?|lead(s)? to|result\w* in|drive\w*|go(es)? with|when there are more)\b")
CHART_WORDS = (r"\b(chart|graph|plot|visuali[sz]\w*|draw|show me|analy[sz]\w*|prove|distribution|pie|donut|"
               r"doughnut|breakdown|split|heat ?maps?|box ?plots?|whiskers?|rolling|moving average|stacked|"
               r"cumulative|trend)\b")
KINDS = [("heatmap", r"\bheat ?maps?\b"), ("box", r"\bbox(es)?[- ]?(plots?|and whiskers?)\b|\bwhiskers?\b"),
         ("control", r"\b(control charts?|process control)\b"), ("rolling", r"\b(rolling|moving)[- ]averages?\b|\bsmooth\w*"),
         ("area", r"\barea (charts?|graphs?)\b|\bcumulative\b"), ("stacked", r"\bstacked\b"),
         ("pie", r"\b(pie|donut|doughnut)\b"), ("scatter", r"\bscatter\w*"), ("line", r"\bline (chart|graph)\b"),
         ("bar", r"\b(bar|column) (chart|graph)s?\b|\bbars\b")]
CAUSE = r"\b(reasons?|causes?|caused|why|because)\b"
RANKING = r"\b(most|least|worst|best|highest|lowest|busiest|quietest|peak)\b|^when\b|\bwhat times?\b"
TREND_WORDS = (r"\b(trend\w*|(becoming|getting|growing) (more|less|worse|better|longer|shorter|higher|lower)|improv\w*|deteriorat\w*|worsen\w*|"
               r"over time)\b")

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


def _mode(text):
    if _has(BIKE, text):
        return "bike"
    if _has(ROAD, text):
        return "road"
    return "bus" if _has(BUS, text) and not _has(TUBE, text) else "tube"


def _measure(subject, text, mode=None):
    """The catalogue measure for a subject, using the question's mode (bus, Tube, bike or road), or
    None when that mode has no such measure."""
    mode = mode or _mode(text)
    if mode == "bike":
        return {"empty": "bike_empty", "full": "bike_full", "out_of_use": "bike_out_of_use",
                "availability": "bike_available", "disruption": "bike_empty"}.get(subject)
    if mode == "road":
        return {"congestion": "road_delayed", "disruption": "road_delayed", "incidents": "road_incidents",
                "wait": "road_delayed", "collisions": "road_collisions", "breakdowns": "road_breakdowns"}.get(subject)
    if mode == "bus" and _has(ALL_ROUTES, text) and subject in ("bunching", "wait", "gap"):
        return {"bunching": "bus_network_bunching", "wait": "bus_network_wait", "gap": "bus_network_gap"}[subject]
    if subject in ("empty", "full", "out_of_use", "availability", "congestion"):
        return None
    if subject == "gap":
        return "bus_network_gap" if mode == "bus" else "tube_wait"
    bus = mode == "bus"
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
    if found and found["dimension"] == "date" and _has(TREND_WORDS, text):
        found["trend"] = True
    if found and len(found["measures"]) == 1 and found["dimension"] != "cause":
        if kind in ("control", "area"):
            found.update(dimension="date", series=None)
        elif kind == "rolling":
            found.update(dimension="time", series=None)
        elif kind == "heatmap":
            found.update(dimension="hour", series="weekday")
        elif kind == "box" and (found["dimension"] in (None, "date") or (
                found["dimension"] == "hour" and not _has(r"\b(hours?|hourly|time of (the )?day)\b", text))):
            found.update(dimension="entity", series=None)   # by line or route unless the hour is asked for
    return {**found, "kind": kind} if found else None


ROAD_SUBJECTS = ("incidents", "disruption", "congestion", "collisions", "breakdowns")


RAIL = r"\b(tube|underground|trains?|rail|lines?|overground|dlr|elizabeth|metro)\b"
MODES = {"road": ROAD, "bus": BUS, "rail": RAIL, "bike": BIKE}
MODE_SUBJECTS = {   # the subjects each mode has a measure for
    "road": {"incidents", "disruption", "congestion", "collisions", "breakdowns"},
    "bus": {"bunching", "wait", "regularity", "countdown", "gap"},
    "rail": {"bunching", "wait", "regularity", "dropped", "countdown", "incidents", "disruption", "accessibility",
             "gap"},
    "bike": {"empty", "full", "out_of_use", "availability"},
}
MODE_DEFAULT = {"road": "road_delayed", "bus": "bus_bunching", "rail": "rail_disruption", "bike": "bike_empty"}
QUALITY = (r"\b(better|worse|worst|best|bad|good|reliable|quiet(er|est)?|bus(ier|iest)|hard(er|est)|easier|"
           r"smooth(er|est)?|slow(er|est)|fast(er|est))\b")


def _mode_measure(mode, subject, text):
    measure = _measure(subject, text, {"rail": "tube"}.get(mode, mode))
    return None if measure is None or measure.startswith("bus_network") else measure   # bands, not hours


def _cross_mode(text, found):
    """[measure, measure] when a question relates two modes ("do road incidents make buses bunch?",
    "does rail disruption push up bus bunching?", "bike shortages and Tube disruption"), else
    None. Each subject goes to the nearest mode mentioned that has a measure for it; a mode with
    no subject gets its main measure. Compared hour by hour across London."""
    if not _has(RELATION, text):
        return None
    mentions = {mode: [m.start() for m in re.finditer(pattern, text)] for mode, pattern in MODES.items()}
    mentions = {mode: spots for mode, spots in mentions.items() if spots}
    if "bus" in mentions and "rail" in mentions and not _has(RAIL.replace("lines?|", ""), text):
        mentions.pop("rail")   # "bus lines" or "routes and lines": still buses
    if len(mentions) < 2:
        return None
    picked = {}
    for at, subject in found:
        modes = [m for m in mentions if subject in MODE_SUBJECTS[m] and m not in picked]
        if not modes:
            continue
        mode = min(modes, key=lambda m: min(abs(at - spot) for spot in mentions[m]))
        measure = _mode_measure(mode, subject, text)
        if measure:
            picked[mode] = measure
    # The likely driver first, as the scatter's x axis: roads, then rail, then bikes, then buses
    order = sorted(sorted(mentions, key=lambda m: min(mentions[m]))[:2], key=["road", "rail", "bike", "bus"].index)
    pair = [picked.get(mode, MODE_DEFAULT[mode]) for mode in order]
    if pair[0] == pair[1]:
        return None
    return pair


def plan_without_kind(text):
    """The measures and dimension a (lower-case) question asks about, or None."""
    found = sorted((m.start(), subject) for subject, pattern in SUBJECTS for m in [re.search(pattern, text)] if m)
    cross = _cross_mode(text, found)
    if cross:
        return {"measures": cross, "dimension": None, "series": None}
    measures = []
    for _, subject in found:
        name = _measure(subject, text)
        if name and name not in measures:
            measures.append(name)
    if not measures:   # a mode named with a time, ranking or quality word: its main measure
        from .phrasing import DIMENSION
        named = next((mode for mode in ("bike", "road", "bus", "rail") if _has(MODES[mode], text)), None)
        if named == "rail" and _has(BUS, text):
            named = "bus"
        if named and (_has(CHART_WORDS, text) or _has(RELATION, text) or _has(RANKING, text)
                      or _has(QUALITY, text) or _has(DIMENSION, text)):
            measures = [MODE_DEFAULT[named]]
    if not measures:
        return None

    hour = _has(r"\b(time of (the )?day|hours?|hourly|rush hours?|peak|morning|evening|night|through the day|"
                r"time bands?)\b", text)
    day_type = _has(r"\bweekends?\b", text)
    weekday = _has(r"\b(day of (the )?week|which day|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                   text)
    date = _has(r"\b(over time|trend\w*|daily|each day|per day|by date|day by day|last \d+ days|this month|"
                r"(becoming|getting|growing) (more|less|worse|better|longer|shorter|higher|lower|bigger|smaller)|improv\w*|deteriorat\w*|worsen\w*)\b", text)
    entity = _has(r"\b(by line|each line|per line|which lines?|by route|each route|per route|which routes?|"
                  r"by station|which (bike )?stations?|by road|each road|per road|which (main )?roads?)\b", text)
    # A ranking ("which line is worst?") asks for a chart, unless it is about this week: the weekly
    # recommendation figures answer those
    asks = _has(RELATION, text) or _has(CHART_WORDS, text) or (
        _has(RANKING, text) and not _has(r"\b(this|last|past) week\b", text))
    hour = hour or _has(r"^when\b|\bwhat times?\b", text)   # "when do most accidents happen?"

    if "rail_disruption" in measures and _has(CAUSE, text) and (asks or _has(r"\b(main|most|top|why|what)\b", text)):
        return {"measures": ["rail_disruption"], "dimension": "cause", "series": None}
    if len(measures) >= 2 and asks:
        return {"measures": measures[:2], "dimension": None, "series": None}
    measure = measures[0]
    if hour and weekday and not day_type:
        return {"measures": [measure], "dimension": "hour", "series": "weekday"}
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
        return CAUSE_BY_LINE_SQL if p.get("kind") == "stacked" else CAUSE_SQL
    if len(p["measures"]) == 2:
        return _pair_sql(*p["measures"])
    table_key, where, expr, *_ = MEASURES[p["measures"][0]]
    table, entity, weight = TABLES[table_key]
    if p["dimension"] == "time":   # one value per clock hour, for rolling averages
        return f"""
        SELECT hour_start AS d0, {expr} AS value, SUM({weight}) AS weight
        FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY d0 HAVING SUM({weight}) > 0 ORDER BY d0"""
    if p.get("kind") == "box":    # one value per entity-hour, grouped for the box plot
        dim = p["dimension"]
        group, sort = (entity, entity) if dim == "entity" else DIMENSIONS[dim][:2]
        return f"""
        SELECT {group} AS g, MIN({sort}) AS s, {expr} AS v
        FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY g, {entity}, hour_start HAVING SUM({weight}) > 0 AND {expr} IS NOT NULL
        ORDER BY FARM_FINGERPRINT(CONCAT(CAST(g AS STRING), CAST(hour_start AS STRING))) LIMIT 12000"""
    dims = [p["dimension"]] + ([p["series"]] if p["series"] else [])
    selects, groups, orders = [], [], []
    for i, dim in enumerate(dims):
        value, sort = (entity, entity) if dim == "entity" else DIMENSIONS[dim][:2]
        selects += [f"{value} AS d{i}", f"MIN({sort}) AS s{i}"]
        groups.append(f"d{i}")
        orders.append(f"s{i}")
    order = "value DESC" if p["dimension"] == "entity" else ", ".join(orders)
    limit = 1500 if p["dimension"] == "entity" else 400   # every bus route (675) or bike station (800)
    days = (f"(SELECT COUNT(DISTINCT DATE(hour_start, 'Europe/London')) FROM `{DATASET}.{table}` "
            f"WHERE {WINDOW} AND {where})")
    latest = f"(SELECT MAX(hour_start) FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where})"
    return f"""
        SELECT {', '.join(selects)}, {expr} AS value, SUM({weight}) AS weight, ANY_VALUE({days}) AS days,
               ANY_VALUE({latest}) AS last_hour
        FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY {', '.join(groups)} HAVING SUM({weight}) > 0
        ORDER BY {order} LIMIT {limit}"""   # room for every hour × day of week cell (168)


def london_wide(first, second):
    """Whether a pair is compared for all London hour by hour: when either is London-wide (e.g.
    road incidents) or they are counted for different things (main roads and bus routes)."""
    return "network" in (MEASURES[first][5], MEASURES[second][5]) or MEASURES[first][0] != MEASURES[second][0]


def _pair_sql(first, second):
    network = london_wide(first, second)
    parts = []
    for i, name in enumerate((first, second)):
        table_key, where, expr, *_ = MEASURES[name]
        table, entity, weight = TABLES[table_key]
        if network:
            entity = "'London'"
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
        "hours": f"{value:,.0f} h", "ratio": f"{value:.2f}", "rate": f"{value:.2f}", "number": f"{value:,.1f}",
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
    if entity in ("station", "road", "network"):
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
    rows = [r for r in rows if r.get("value", r.get("y", r.get("v"))) is not None]
    if len(rows) < 2:
        return {"answer": "There isn't enough data for that analysis yet.", "chart": None}
    kind = p.get("kind")
    if len(p["measures"]) == 2:
        result = _relationship(p, rows)
    elif p["dimension"] == "cause":
        result = _stacked_causes(p, rows) if kind == "stacked" else _by_cause(p, rows)
    elif kind == "box":
        result = _box(p, rows)
    elif p["dimension"] == "time":
        result = _rolling(p, rows)
    elif p["series"] == "weekday":
        result = _heatmap(p, rows)
    elif p["series"]:
        result = _by_hour_and_day_type(p, rows)
    elif kind == "control":
        result = _control(p, rows)
    else:
        result = _by_dimension(p, rows)
    result = _apply_kind(kind, result, p)
    latest = next((r["last_hour"] for r in rows if r.get("last_hour")), None)
    if latest is not None and result.get("chart"):
        result["answer"] += f"\n\n_Latest data used: {_when_hour(latest)}._"
    return result


def _when_hour(value):
    """A timestamp as London time: 'Sat 31 Oct, 21:00'."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace(" UTC", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(ZoneInfo("Europe/London"))
    return f"{t:%a} {t.day} {t:%b}, {t:%H:%M} London time"


def follow_ups(p):
    """Questions that take the same measure further, offered after an answer."""
    if p and len(p["measures"]) == 2 and p["dimension"] != "cause":   # a relationship: each measure on its own
        first, second = p["measures"]
        options = [f"How does {_mid(first)} change through the day?", f"Is {_mid(second)} getting worse over time?"]
        if MEASURES[second][5] != "network":
            options.append(f"Compare {_mid(second)} by {MEASURES[second][5]}")
        return options
    if not p or len(p["measures"]) != 1 or p["dimension"] == "cause":
        first = ("Show a pie chart of the reasons for rail disruption" if p and p.get("kind") == "stacked"
                 else "Show a stacked bar chart of disruption causes by line")
        return [first, "Is rail disruption getting worse over time?", "Which line-days had unusual disruption?"]
    name = _mid(p["measures"][0])
    entity = MEASURES[p["measures"][0]][5]
    options = [f"Heatmap of {name} by hour and day of week", f"Box plot of {name} by {entity}",
               f"Is {name} getting worse over time?", f"Control chart of {name}",
               f"Rolling average of {name}"]
    done = {"hour": 0 if p["series"] == "weekday" else None, "entity": 1 if p.get("kind") == "box" else None,
            "date": 2 if p.get("kind") != "control" else 3, "time": 4}.get(p["dimension"])
    return [q for i, q in enumerate(options) if i != done][:3]


def _apply_kind(kind, result, p):
    """Use the chart type the visitor asked for when it suits the data; otherwise keep the best
    one and say why."""
    chart = result["chart"]
    if not kind or not chart or kind == chart["kind"] or kind in ("box", "rolling", "control", "heatmap", "stacked"):
        if kind in ("box", "rolling", "control", "heatmap", "stacked") and chart and kind not in (
                chart["kind"], "rolling") and not (kind == "rolling" and chart["kind"] == "line"):
            result["answer"] += (f"\n\n_A {kind} chart doesn't suit this question, so here is a {chart['kind']} "
                                 "chart._")
        return result
    if kind == "area" and chart["kind"] == "line" and not chart.get("series"):
        chart["kind"] = "area"
        return result
    note = None
    if kind == "pie":
        if p["dimension"] == "cause":
            chart["kind"] = "pie"
        else:
            note = (f"A pie chart shows parts of a whole and {_mid(p['measures'][0])} is a rate, not a total "
                    f"that adds up, so here is a {chart['kind']} chart instead.")
    elif kind == "scatter" and p["dimension"] == "hour":
        chart.update(kind="scatter", x_format="hour", x_title="Hour of day (London time)",
                     data=[{"x": int(d["x"][:2]), "y": d["y"]} for d in chart["data"]])
        note = "Each dot is one hour of the day; the line shows the overall trend."
    elif kind == "scatter":
        note = ("A scatter plot needs two numbers per point, such as hour and bunching, or bunching and waiting "
                "time; these groups suit a bar chart better.")
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


THIN = 0.25   # a group with less than a quarter of the median group's data is too thin to compare


def reliable(data):
    """(groups with enough data, thin groups): thin ones are left out of the verdict, the peak
    and the trough, so a few Night Tube trains at 04:00 can't decide "through the day"."""
    weights = sorted(d["w"] for d in data)
    median = weights[len(weights) // 2] if weights else 0
    keep = [d for d in data if d["w"] >= THIN * median]
    return keep, [d for d in data if d["w"] < THIN * median]


def _variation(points):
    """Weighted coefficient of variation: how much a measure moves, relative to its average."""
    mean = _mean(points)
    total = sum(d["w"] for d in points)
    if not mean or not total:
        return 0
    return math.sqrt(sum(d["w"] * (d["y"] - mean) ** 2 for d in points) / total) / mean


def hour_profile(measure, sources):
    """The measure by hour of day (all hours and the reliable ones), for the travel advice."""
    rows = [dict(r) for r in sources.query(build_sql({"measures": [measure], "dimension": "hour", "series": None}))]
    data = [{"x": f"{int(r['d0']):02d}:00", "h": int(r["d0"]), "y": float(r["value"]), "w": float(r["weight"])}
            for r in rows if r.get("value") is not None]
    return data, reliable(data)[0]


def _by_dimension(p, rows):
    name, dim = p["measures"][0], p["dimension"]
    fmt, label, entity = MEASURES[name][3], _label(name), MEASURES[name][5]
    if dim == "hour":
        bands = MEASURES[name][0] == "routes"
        data = [{"x": f"{int(r['d0']):02d}:00" + (f" {BANDS[int(r['d0'])]}" if bands else ""), "y": r["value"],
                 "w": r["weight"]} for r in rows]
    elif dim == "entity":
        data = [{"x": _entity_name(r["d0"], entity), "y": r["value"], "w": r["weight"]} for r in rows]
    elif dim == "date":
        data = [{"x": _day(r["d0"]), "y": r["value"], "w": r["weight"]} for r in rows]
    else:
        data = [{"x": str(r["d0"]), "y": r["value"], "w": r["weight"]} for r in rows]

    keep, thin = reliable(data)
    high, low = max(keep, key=lambda d: d["y"]), min(keep, key=lambda d: d["y"])
    mean = _mean(keep)
    variation = _variation(keep)
    what = {"hour": "through the day", "weekday": "across the week", "day_type": "between weekdays and weekends",
            "date": "from day to day", "entity": f"between {entity}s"}[dim]
    if variation >= 0.25:
        text = f"**Yes: {_mid(name)} changes strongly {what}.** "
    elif variation >= 0.10:
        text = f"**Yes: {_mid(name)} changes {what}, moderately.** "
    else:
        text = f"**Not much: {_mid(name)} stays fairly steady {what}.** "
    text += (f"It is highest {_when(high['x'], dim)} ({_fmt(high['y'], fmt)}) and lowest {_when(low['x'], dim)} "
             f"({_fmt(low['y'], fmt)}), against an average of {_fmt(mean, fmt)}; it typically moves "
             f"{variation:.0%} either side of that average (coefficient of variation).")
    if dim == "hour":
        rush = (7, 8, 9, 16, 17, 18)
        peak = [d for d in keep if int(d["x"][:2]) in rush]
        other = [d for d in keep if int(d["x"][:2]) not in rush]
        if peak and other:
            text += (f" In the rush hours (07–10 and 16–19) it averages {_fmt(_mean(peak), fmt)}, against "
                     f"{_fmt(_mean(other), fmt)} at other times.")
    if thin:
        names = ", ".join(d["x"] for d in thin) if len(thin) <= 5 else f"{len(thin)} of them"
        why = ("" if dim != "hour" else " (fewer buses run overnight)" if name.startswith("bus")
               else " (only the Night Tube runs overnight)" if name.startswith("tube") else " (overnight)")
        them = "them" if len(thin) > 1 else "it"
        text += (f" {names} {'have' if len(thin) > 1 else 'has'} too little data to compare{why}, so the chart "
                 f"shows {them} but the comparison leaves {them} out.")
    fitted = None
    if dim == "date" and len(data) >= 3:
        trend_text, fitted = trend(data, fmt, _mid(name))
        if p.get("trend"):   # the question asks about a trend: answer that first
            slope = fitted[-1] - fitted[0]
            significant = "unlikely to be chance" in trend_text
            lead = ("**No clear trend yet.**" if not significant else
                    f"**Yes: {_mid(name)} is getting {'worse' if slope > 0 else 'better'}.**")
            body = text.split("** ", 1)[-1]
            text = (lead + trend_text.replace(": no clear trend yet", "") + " Day to day: "
                    + body[0].lower() + body[1:])
        else:
            text += trend_text
    text += f"\n\n_Based on {int(rows[0]['days'])} days of data; times are London time._"
    x_title = entity.title() if dim == "entity" else DIMENSIONS[dim][2]
    if dim == "entity" and len(data) > 15:   # the statistics cover every one; the chart, the 15 highest
        text = text.replace("\n\n_Based on", f" The chart shows the 15 highest of {len(data)} {entity}s.\n\n_Based on", 1)
        data = sorted(data, key=lambda d: -d["y"])[:15]
    chart = {"kind": "line" if dim in ("hour", "date") else "bar", "title": f"{_name(name)} by {x_title.lower()}",
             "x": "x", "y": "y", "data": [{"x": d["x"], "y": d["y"], **({"fit": fitted[i]} if fitted else {})}
                                          for i, d in enumerate(data)], "format": fmt,
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


def unit_word(rows, first, second):
    return "hours" if london_wide(first, second) else f"{MEASURES[first][5]}-hours"


def correlation_p(r, n):
    """Two-sided p-value for a correlation r over n pairs (t-test, normal approximation), or None."""
    if r is None or n < 5 or abs(r) >= 1:
        return 0.0 if r is not None and abs(r) >= 1 and n >= 5 else None
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return math.erfc(abs(t) / math.sqrt(2))


def hour_adjusted(rows):
    """(r, n) between x and y after subtracting each London hour's average from both (a partial
    correlation controlling for time of day), or None without enough hours to do it."""
    by = {}
    for row in rows:
        when = row.get("hour_start")
        if when is None:
            return None
        try:
            hour = _london(when).hour
        except (TypeError, ValueError):
            return None
        by.setdefault(hour, []).append((float(row["x"]), float(row["y"])))
    groups = [g for g in by.values() if len(g) >= 2]
    if len(groups) < 4 or sum(len(g) for g in groups) < 20:
        return None
    xs, ys = [], []
    for g in groups:
        mx, my = sum(x for x, _ in g) / len(g), sum(y for _, y in g) / len(g)
        xs += [x - mx for x, _ in g]
        ys += [y - my for _, y in g]
    r = pearson(xs, ys)
    return (r, len(xs)) if r is not None else None


def _relationship(p, rows):
    first, second = p["measures"]
    xs, ys = [r["x"] for r in rows], [r["y"] for r in rows]
    r = pearson(xs, ys)
    if r is None:
        return {"answer": "There isn't enough overlapping data to compare those two yet.", "chart": None}
    entity = MEASURES[first][5]
    unit = "hours" if london_wide(p["measures"][0], second) else f"{entity}-hours"
    change = ("tends to rise" if r > 0 else "tends to fall") if abs(r) >= 0.1 else "shows no consistent change"
    text = (f"**There is {_strength(r)} relationship** (correlation r = {r:.2f}, over {len(rows):,} "
            f"{unit}): when {_mid(first)} is higher, {_mid(second)} {change}. "
            "r runs from −1 to 1 and 0 means no straight-line relationship. It shows the two move together, "
            "not that one causes the other.")
    slope, _, r2 = linear_fit(xs, ys)
    step = 0.01 if MEASURES[first][3] == "pct" else 1
    per = {"road_incidents": "each extra incident reported in the hour"}.get(first)
    if per is None:
        unit = {"pct": "percentage point", "minutes": "minute", "seconds": "second", "hours": "hour"}.get(
            MEASURES[first][3], "unit")
        per = f"each extra {unit} of {_mid(first)}"
    text += (f" A straight-line (least-squares) regression gives {_fmt(abs(slope * step), MEASURES[second][3])} "
             f"{'more' if slope > 0 else 'less'} {_mid(second)} for {per} and explains {r2:.0%} of the variation "
             f"(R² = {r2:.2f}).")
    if "road_delayed" in (first, second):
        text += " Bear in mind TfL's main-road rating changes rarely (it is often set by long-running roadworks)"
        text += (", so short incidents barely move it: a weak link here doesn't mean incidents don't slow traffic."
                 if abs(r) < 0.3 else ".")
    adjusted = hour_adjusted(rows)
    if adjusted is not None:
        ar, n = adjusted
        text += (f"\n\n**Allowing for the time of day** (comparing each hour with that hour's usual level, so the "
                 f"shared rush-hour rhythm is taken out), r = {ar:.2f}: ")
        if abs(r) >= 0.2 and abs(ar) < 0.1:
            text += "the link is mostly the daily rhythm both follow, not one driving the other."
        elif abs(ar) >= 0.6 * abs(r) and abs(ar) >= 0.1:
            text += "the link holds beyond the rush hours they share."
        elif abs(ar) >= 0.1:
            text += "part of the link is the shared daily rhythm and part goes beyond it."
        else:
            text += "there is no link once the time of day is allowed for."
        pv = correlation_p(ar, n)
    else:
        pv = correlation_p(r, len(rows))
    if pv is not None:
        text += (f" {'This is statistically significant' if pv < 0.05 else 'This could be chance'} "
                 f"(p {'< 0.001' if pv < 0.001 else f'= {pv:.3f}'}); neighbouring hours are alike, so treat the "
                 "p-value as a little optimistic.")
    if len(rows) < 100:
        text += f" With only {len(rows)} {unit_word(rows, first, second)} so far, treat this as provisional."
    chart = {"kind": "scatter", "title": f"{_name(second)} against {_mid(first)}", "x": "x", "y": "y",
             "data": [{"x": x, "y": y} for x, y in zip(xs, ys)], "format": MEASURES[second][3],
             "x_format": MEASURES[first][3], "series": None, "horizontal": False,
             "x_title": _label(first), "y_title": _label(second)}
    return {"answer": text, "chart": chart}


# Statistical analyses -------------------------------------------------------------------------
# Beyond "how does X change with Y": distributions, a significance test, anomalies, persistence,
# early warning (lead-lag), concentration and countdown decay. Each has fixed SQL and its own
# statistics; a question chooses one by its wording and may name the measure to analyse.

STATS = {   # name: (question words, default measure, example question)
    "forecast": (r"\b(forecast\w*|next 24 hours|tomorrow|what will .{0,40} be|expect\w* (tomorrow|next|later)|"
                 r"predict\w* (for |of )?(tomorrow|the next|next))\b", "tube_wait",
                 "Forecast Tube waiting time for the next 24 hours"),
    "decomposition": (r"\b(decompos\w*|seasonal\w*|daily cycle|underlying trend|trend and (daily )?(pattern|season\w*))\b",
                      "bus_bunching", "Decompose bus bunching into trend and daily cycle"),
    "clusters": (r"\b(cluster\w*|segment\w*|similar|alike|group(s|ing)? (of )?(lines|routes|stations|roads)|"
                 r"kinds? of (stations|routes|lines))\b", "bike_empty", "Cluster bike stations by when they run empty"),
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
HOURLY_EQUIVALENT = {"bus_network_bunching": "bus_bunching", "bus_network_wait": "bus_wait",
                     "bus_network_gap": "bus_wait"}
CONCENTRATION = {   # hours that add up, for the Pareto curve
    "rail_disruption": "SUM(disrupted_minutes) / 60", "accessibility": "SUM(accessibility_minutes) / 60",
    "bike_empty": "SUM(empty_minutes) / 60", "bike_full": "SUM(full_minutes) / 60",
    "road_delayed": "SUM(delayed_minutes) / 60",
}
DISRUPTED_HOUR = 0.5   # an hour counts as disrupted when at least half its service time was


def stat_plan(question):
    """{"stat": name, "measure": name} for a statistical question, else None."""
    text = question.lower()
    for name, (pattern, default, _) in STATS.items():
        if name == "horizon" and not _has(r"\b(countdowns?|predict\w*|accura\w*)\b", text):
            continue
        if _has(pattern, text):
            found = sorted((m.start(), subject) for subject, p in SUBJECTS for m in [re.search(p, text)] if m)
            measures = [m for m in (_measure(subject, text) for _, subject in found) if m]
            named = next((m for m in ("bike", "road", "bus") if _has(MODES[m], text)), None)
            mode_default = MODE_DEFAULT.get(named, default)
            measure = measures[0] if measures else mode_default
            if name == "persistence" and measure != "rail_disruption":
                measure = default
            if name == "concentration" and measure not in CONCENTRATION:
                measure = default
            if name == "lead_lag" and measure not in ("tube_wait", "tube_bunching", "tube_regularity"):
                measure = default
            if name == "horizon":
                measure = "tube_countdown"
            if name in ("forecast", "decomposition") and MEASURES[measure][0] in ("routes", "access"):
                measure = HOURLY_EQUIVALENT.get(measure, default)   # these need an hourly series
            if name == "clusters" and _mode(text) == "bus":   # group every route, not the 12 tracked hourly
                measure = {"bus_bunching": "bus_network_bunching", "bus_wait": "bus_network_wait"}.get(
                    measure, "bus_network_bunching" if not measures else measure)
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
    # A big table (800 bike stations) is sampled evenly, not cut off alphabetically.
    order = "FARM_FINGERPRINT(CONCAT(CAST(e AS STRING), CAST(t AS STRING)))" if group == "entity_hour" else keys
    limit = 6000 if group == "entity_hour" else 20000
    return f"""
        SELECT {cols}, {expr} AS v FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
        GROUP BY {keys} HAVING SUM({weight}) > 0 AND {expr} IS NOT NULL
        ORDER BY {order} LIMIT {limit}"""


def stat_sql(p):
    name, measure = p["stat"], p["measure"]
    if name in ("forecast", "decomposition"):
        return build_sql({"measures": [measure], "dimension": "time", "series": None})
    if name == "clusters":
        table_key, where, expr, *_ = MEASURES[measure]
        table, entity, weight = TABLES[table_key]
        return f"""
            SELECT {entity} AS e, EXTRACT(HOUR FROM {LONDON}) AS h, {expr} AS v, SUM({weight}) AS w
            FROM `{DATASET}.{table}` WHERE {WINDOW} AND {where}
            GROUP BY e, h HAVING SUM({weight}) > 0 AND {expr} IS NOT NULL ORDER BY e, h LIMIT 40000"""
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
        hours = CONCENTRATION[measure]
        label = "line_name" if measure == "rail_disruption" else entity
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
    zero_share = sum(v == 0 for v in values) / n
    if zero_share >= 0.5:
        rest = [v for v in values if v > 0]
        mid = _quantile(rest, 0.5) if rest else 0
        text = (f"Across {n:,} {entity}-hours, **{zero_share:.0%} had none at all** ({_mid(measure)} was zero). In the "
                f"{len(rest):,} hours where it happened, the median was **{_fmt(mid, fmt)}** and 1 hour in 10 "
                f"overall was above {_fmt(p90, fmt)}: the problem is concentrated in a minority of hours.")
        chart = {"kind": "bar", "title": f"Distribution of {_mid(measure)} per {entity}-hour", "x": "x", "y": "y",
                 "data": data, "format": "pct", "horizontal": False, "series": None,
                 "x_title": _label(measure), "y_title": f"Share of {entity}-hours"}
        return {"answer": text, "chart": chart}
    shape = ("right-skewed: most hours are close to the median, with a long tail of much worse hours"
             if skew > 0.5 else "left-skewed: a tail of unusually good hours" if skew < -0.5
             else "roughly symmetric around the median")
    text = (f"Across {n:,} {entity}-hours, the median {_mid(measure)} was **{_fmt(median, fmt)}**; half of the "
            f"hours were between {_fmt(q1, fmt)} and {_fmt(q3, fmt)} (the interquartile range) and 1 hour in 10 "
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
    boot, rng_ci = [], Random(3)
    for _ in range(4000):
        we = [rng_ci.choice(weekend) for _ in weekend]
        wd = [rng_ci.choice(weekday) for _ in weekday]
        boot.append(sum(we) / len(we) - sum(wd) / len(wd))
    boot.sort()
    ci = (boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1])
    verdict = ("**unlikely to be chance**" if p_value < 0.05 else
               "**could well be chance**: more weeks of data are needed to be sure")
    higher = "weekends" if observed > 0 else "weekdays"
    text = (f"{_name(measure)} averaged {_fmt(mean_we, fmt)} on weekend days and {_fmt(mean_wd, fmt)} on weekdays: "
            f"higher on {higher}. A permutation test (re-splitting the same {len(values)} days at random) gives "
            f"p = {p_value:.3f}, so the difference is {verdict}. A bootstrap 95% confidence interval for the "
            f"difference (weekend minus weekday) runs from {_fmt(ci[0], fmt)} to {_fmt(ci[1], fmt)}"
            f"{'; it includes zero' if ci[0] <= 0 <= ci[1] else ''}. ({len(weekday)} weekdays and {len(weekend)} "
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
            f"**{run:.0f} hours** on average and is {p_stay / p_start:.0f} times as likely to continue as to "
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
    what = {"accessibility": "stations", "bike_empty": "stations", "bike_full": "stations",
            "road_delayed": "roads"}.get(measure, "lines")
    hours = {"accessibility": "hours with a lift or step-free access notice", "bike_empty": "hours stations sat empty",
             "bike_full": "hours stations sat full", "road_delayed": "hours main roads were seriously delayed"
             }.get(measure, "disrupted hours")
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


# When an analysis can't be done ---------------------------------------------------------------
# A chart or statistic asked for something this engine has no history for (or for no measure at
# all) gets an explanation and suggestions of the same kind of analysis it can do, instead of a
# chart of something else.

NO_HISTORY = [   # (topic, question words, why)
    ("air quality", r"\b(air quality|pollution|no2|pm2\.?5|pm10|ozone)\b",
     "this platform doesn't collect air quality"),
    ("weather", r"\b(weather|rain\w*|temperature|snow\w*|wind)\b",
     "this platform doesn't collect weather data"),
]
TOPIC_SUGGESTIONS = {
    "bikes": ["How many bikes are at Waterloo?", "Chart how often bike stations are empty or full",
              "How many bike docks are out of use?"],
    "roads": ["Which main roads are delayed?", "Chart how long road incidents take to clear", "Do roadworks overrun?"],
}
KIND_SUGGESTIONS = {
    "histogram": ["What is the distribution of Tube waiting times?", "What is the distribution of bus bunching?",
                  "What is the distribution of bus waiting times?"],
    "pie": ["Show a pie chart of the reasons for rail disruption"],
    "scatter": ["Is bunching related to waiting time on the Tube?", "Is bus bunching related to bus waiting time?",
                "Is rail disruption related to countdown accuracy?"],
    "line": ["How does Tube waiting time change through the day?", "Plot rail disruption over time",
             "Bus bunching by hour on weekdays and weekends"],
    "bar": ["Compare Tube bunching by line", "Which day of the week has the most rail disruption?",
            "Compare bus waiting time by route"],
}
KIND_NAMES = {"histogram": "a histogram", "pie": "a pie chart", "scatter": "a scatter plot", "line": "a line chart",
              "bar": "a bar chart", None: "that analysis"}
ASKS_ANALYSIS = (CHART_WORDS + r"|\b(histogram|frequency|significan\w*|anomal\w*|unusual|outliers?|persist\w*|"
                 r"correlat\w*|relationship|concentrat\w*|gini|pareto|early warning|lead.?lag)\b")


def _requested_kind(text):
    if _has(r"\b(histogram|distribution|frequency|spread)\b", text):
        return "histogram"
    return next((name for name, pattern in KINDS if _has(pattern, text)), None)


def cannot_analyse(question):
    """{"answer", "suggestions"} when a chart or statistic is asked for something this engine
    can't analyse, or for no measure at all; None when the question isn't asking for one or can
    be answered."""
    text = question.lower()
    if not _has(ASKS_ANALYSIS, text) or (stat_plan(question) and not _requested_kind(text)):
        return None   # a statistic with a default measure answers it
    kind = _requested_kind(text)
    rail_or_bus = _has(r"\b(tube|underground|trains?|rail|lines?|bus(es)?|routes?|dlr|overground|elizabeth)\b", text)
    measures = [subject for subject, pattern in SUBJECTS if _has(pattern, text)]
    for topic, pattern, why in NO_HISTORY:
        if _has(pattern, text) and not (rail_or_bus and measures):
            suggestions = TOPIC_SUGGESTIONS.get(topic, []) + KIND_SUGGESTIONS.get(kind, STAT_SUGGESTIONS[:3])
            opening = (f"I can't draw {KIND_NAMES[kind]} of {topic} yet" if topic in TOPIC_SUGGESTIONS
                       else f"I can't analyse {topic}")
            return {"answer": f"{opening}: {why}. Here's what I can do instead:", "suggestions": suggestions[:5]}
    if _mode(text) in ("bike", "road"):
        return None   # bike and road history is in the chat's dataset; their defaults apply
    if not measures and not _has(r"\b(lifts?|step[- ]free|accessib\w*|countdowns?|disrupt\w*)\b", text):
        what = KIND_NAMES[kind].replace("that analysis", "an analysis")
        return {"answer": f"I can draw {what} of several measures: bunching, waiting time and gap regularity for "
                          "buses and the Tube, rail disruption and incidents, countdown accuracy and station "
                          "accessibility. Which one? Try one of these:",
                "suggestions": KIND_SUGGESTIONS.get(kind, STAT_SUGGESTIONS[:4] + SUGGESTIONS[:2])}
    return None


# More charts and statistics ---------------------------------------------------------------------

def linear_fit(xs, ys):
    """(slope, intercept, r²) of a least-squares straight line."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return 0.0, my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    r = pearson(xs, ys)
    return slope, my - slope * mx, (r or 0) ** 2


def permutation_p(xs, ys, rounds=4000):
    """Two-sided p-value for a straight-line trend: how often shuffled data fits as steep a slope."""
    from random import Random
    observed = abs(linear_fit(xs, ys)[0])
    rng, values, extreme = Random(11), list(ys), 0
    for _ in range(rounds):
        rng.shuffle(values)
        if abs(linear_fit(xs, values)[0]) >= observed - 1e-12:
            extreme += 1
    return extreme / rounds


def trend(points, fmt, name):
    """(text, fitted values) for a measure's daily values: the slope per day, how much of the
    day-to-day change it explains and whether it could be chance."""
    xs, ys = list(range(len(points))), [d["y"] for d in points]
    slope, intercept, r2 = linear_fit(xs, ys)
    p = permutation_p(xs, ys)
    per_day = _fmt(abs(slope), fmt).replace("%", " points") if fmt == "pct" else _fmt(abs(slope), fmt)
    direction = "rising" if slope > 0 else "falling"
    if len(points) < 7:
        verdict = "too few days to call it a trend yet"
    elif p < 0.05:
        verdict = f"unlikely to be chance (p = {p:.3f}): a real {direction} trend so far"
    else:
        verdict = f"could be chance (p = {p:.2f}): no clear trend yet"
    text = (f" A straight-line trend through the days is {direction} by {per_day} a day "
            f"(R² = {r2:.2f}, so it explains {r2:.0%} of the day-to-day change); {verdict}.")
    worse = slope > 0   # every measure here is better when lower
    text += f" For {name} that would mean getting {'worse' if worse else 'better'}." if p < 0.05 and len(points) >= 7 else ""
    return text, [intercept + slope * x for x in xs]


def _box(p, rows):
    name, dim = p["measures"][0], p["dimension"]
    fmt, entity = MEASURES[name][3], MEASURES[name][5]
    groups, order = {}, []
    for r in rows:
        key = r["g"]
        if key not in groups:
            groups[key], _ = [], order.append(key)
        groups[key].append(float(r["v"]))
    data = []
    for key in order:
        values = sorted(groups[key])
        if len(values) < 5:
            continue
        q1, median, q3 = (_quantile(values, q) for q in (0.25, 0.5, 0.75))
        iqr = q3 - q1
        lo = min(v for v in values if v >= q1 - 1.5 * iqr)
        hi = max(v for v in values if v <= q3 + 1.5 * iqr)
        outliers = sum(v < lo or v > hi for v in values)
        label = (_entity_name(key, entity) if dim == "entity" else f"{int(key):02d}:00" if dim == "hour" else str(key))
        data.append({"x": label, "q1": q1, "median": median, "q3": q3, "lo": lo, "hi": hi, "n": len(values),
                     "outliers": outliers})
    if len(data) < 2:
        return {"answer": "There isn't enough data for a box plot yet.", "chart": None}
    if dim == "entity":
        data.sort(key=lambda d: -d["median"])
        data = data[:15]
    widest = max(data, key=lambda d: d["q3"] - d["q1"])
    top, low = max(data, key=lambda d: d["median"]), min(data, key=lambda d: d["median"])
    unit = f"{entity}-hour"
    text = (f"Each box shows the middle half of {_mid(name)} across {unit}s: the line in the box is the median, "
            f"the whiskers reach the furthest values within 1.5 times the box's height and dots beyond them are "
            f"outliers. The highest median is **{top['x']}** ({_fmt(top['median'], fmt)}), the lowest "
            f"**{low['x']}** ({_fmt(low['median'], fmt)}). **{widest['x']}** varies most: half its {unit}s fall "
            f"between {_fmt(widest['q1'], fmt)} and {_fmt(widest['q3'], fmt)}, so it is the least predictable. "
            f"{sum(d['outliers'] for d in data)} of {sum(d['n'] for d in data):,} {unit}s are outliers.")
    x_title = entity.title() if dim == "entity" else DIMENSIONS[dim][2]
    chart = {"kind": "box", "title": f"{_name(name)} by {x_title.lower()} (box plot)", "x": "x", "y": "median",
             "data": data, "format": fmt, "horizontal": dim == "entity", "series": None, "x_title": x_title,
             "y_title": _label(name)}
    return {"answer": text, "chart": chart}


def _rolling(p, rows):
    name = p["measures"][0]
    fmt = MEASURES[name][3]
    keep, thin = reliable([{**r, "w": float(r["weight"])} for r in rows])   # overnight hours with few trains
    rows = keep
    values = [float(r["value"]) for r in rows]
    window = 24
    data = []
    for i, r in enumerate(rows):
        recent = values[max(0, i - window + 1): i + 1]
        stamp = _when_hour(r["d0"]).replace(" London time", "")
        data.append({"x": stamp, "y": values[i], "series": "Each hour"})
        if len(recent) == window:
            data.append({"x": stamp, "y": sum(recent) / window, "series": "24-hour rolling average"})
    smooth = [d["y"] for d in data if d["series"] != "Each hour"]
    if len(smooth) < 2:
        return {"answer": "There isn't enough data for a 24-hour rolling average yet.", "chart": None}
    text = (f"The 24-hour rolling average smooths out the daily cycle of {_mid(name)} to show the underlying "
            f"level: it ranged from {_fmt(min(smooth), fmt)} to {_fmt(max(smooth), fmt)} and stands at "
            f"**{_fmt(smooth[-1], fmt)}** for the latest 24 hours, against {_fmt(sum(smooth) / len(smooth), fmt)} "
            "on average.")
    if thin:
        text += (f" {len(thin)} hours with too little service to measure (mostly overnight) are left out, so the "
                 "average follows the hours people travel.")
    chart = {"kind": "line", "title": f"{_name(name)}: each hour and 24-hour rolling average", "x": "x", "y": "y",
             "data": data, "format": fmt, "series": "series", "horizontal": False, "x_title": "Hour (London time)",
             "y_title": _label(name), "temporal_labels": True}
    return {"answer": text, "chart": chart}


def _heatmap(p, rows):
    name = p["measures"][0]
    fmt = MEASURES[name][3]
    cells = [{"x": f"{int(r['d0']):02d}:00", "y": r["d1"], "value": float(r["value"]), "w": float(r["weight"])}
             for r in rows]
    keep, thin = reliable([{**c, "y_": c["y"], "y": c["value"]} for c in cells])
    keep = [{**c, "y": c["y_"]} for c in keep]
    high = max(keep, key=lambda c: c["value"])
    low = min(keep, key=lambda c: c["value"])
    text = (f"The darkest cells are the worst times for {_mid(name)}: highest on **{WEEKDAYS.get(high['y'])}s at "
            f"{high['x']}** ({_fmt(high['value'], fmt)}), lowest on {WEEKDAYS.get(low['y'])}s at {low['x']} "
            f"({_fmt(low['value'], fmt)}).")
    if thin:
        text += f" {len(thin)} cells have too little data to compare and are left out of that comparison."
    text += f"\n\n_Based on {int(rows[0]['days'])} days of data, so each weekday appears only once or twice so far._"
    chart = {"kind": "heatmap", "title": f"{_name(name)} by hour and day of week", "x": "x", "y": "y",
             "data": [{"x": c["x"], "y": c["y"], "value": c["value"]} for c in cells], "format": fmt,
             "series": None, "horizontal": False, "x_title": "Hour of day (London time)", "y_title": "Day of week",
             "y_order": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    return {"answer": text, "chart": chart}


def _control(p, rows):
    name = p["measures"][0]
    fmt = MEASURES[name][3]
    data = [{"x": _day(r["d0"]), "y": float(r["value"])} for r in rows]
    values = [d["y"] for d in data]
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1)) if len(values) > 1 else 0
    upper, lower = mean + 3 * sd, max(mean - 3 * sd, 0)
    outside = [d for d in data if d["y"] > upper or d["y"] < lower]
    text = (f"A control chart shows whether day-to-day changes in {_mid(name)} are normal variation or a signal. "
            f"The centre line is the average ({_fmt(mean, fmt)}); the control limits are three standard deviations "
            f"either side ({_fmt(lower, fmt)} to {_fmt(upper, fmt)}). ")
    text += (f"**{len(outside)} day{'s' if len(outside) != 1 else ''}** fell outside the limits ("
             + ", ".join(f"{d['x']}: {_fmt(d['y'], fmt)}" for d in outside) + "): worth investigating."
             if outside else "**Every day is within the limits**, so the changes are ordinary variation, not a "
                             "signal that something changed.")
    text += f" With only {len(values)} days the limits are wide; they tighten as data builds."
    chart = {"kind": "control", "title": f"{_name(name)}: control chart by day", "x": "x", "y": "y", "data": data,
             "format": fmt, "series": None, "horizontal": False, "x_title": "Day", "y_title": _label(name),
             "centre": mean, "upper": upper, "lower": lower}
    return {"answer": text, "chart": chart}


def _stacked_causes(p, rows):
    totals, causes = {}, {}
    for r in rows:
        totals[r["x"]] = totals.get(r["x"], 0) + float(r["y"])
        causes[r["series"]] = causes.get(r["series"], 0) + float(r["y"])
    top_causes = [c for c, _ in sorted(causes.items(), key=lambda kv: -kv[1])[:6]]
    lines = [l for l, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:12]]
    data = {}
    for r in rows:
        if r["x"] not in lines:
            continue
        cause = r["series"] if r["series"] in top_causes else "Other"
        data[(r["x"], cause)] = data.get((r["x"], cause), 0) + float(r["y"])
    worst = lines[0]
    worst_cause = max(((c, v) for (l, c), v in data.items() if l == worst), key=lambda kv: kv[1])
    total = sum(totals.values())
    text = (f"**{worst}** had the most disrupted line-hours ({totals[worst]:,.0f}), mostly "
            f"{worst_cause[0].lower()}. Across all lines, the biggest reasons were "
            + ", ".join(f"{c.lower()} ({causes[c] / total:.0%})" for c in top_causes[:3])
            + ". Each bar is a line; its segments add up to that line's disrupted hours.")
    chart = {"kind": "stacked", "title": "Disrupted line-hours by line and reason", "x": "x", "y": "y",
             "data": [{"x": l, "series": c, "y": v} for (l, c), v in data.items()], "format": "hours",
             "series": "series", "horizontal": True, "x_title": "Line", "y_title": "Disrupted line-hours",
             "x_order": lines}
    return {"answer": text, "chart": chart}


# Forecasting, decomposition, clustering ---------------------------------------------------------
# Simple, explainable methods that grow with the history: with a week or two of data they show the
# daily cycle; weekly patterns and a reliable trend need several weeks.

def _london(value):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace(" UTC", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo("Europe/London"))


def _hourly(rows):
    """[(London time, value)] for the hours with enough data (thin overnight hours left out)."""
    keep, _ = reliable([{**r, "w": float(r["weight"]), "y": float(r["value"])} for r in rows])
    return [(_london(r["d0"]), r["y"]) for r in keep]


FORECAST_METHODS = {   # name: (description, minimum days of history before the forecast day)
    "yesterday": ("repeating the day before", 1),
    "hour_mean": ("the average of the same hour over the last 7 days", 2),
    "recent_weighted": ("the same hour, weighting recent days more (each day back counts half as much)", 2),
    "day_type": ("the same hour on the same kind of day (weekday or weekend)", 2),
    "weekday": ("the same hour on the same day of the week", 7),
}


def _method_forecast(method, history, day):
    """{hour: value} for `day` from `history` [(time, value)] before it, or None when the method
    hasn't enough history yet."""
    from datetime import timedelta
    days = sorted({t.date() for t, _ in history})
    if len(days) < FORECAST_METHODS[method][1]:
        return None
    by = {}
    for t, v in history:
        age = (day - t.date()).days
        if method == "yesterday" and age != 1:
            continue
        if method in ("hour_mean", "recent_weighted") and age > 7:
            continue
        if method == "day_type" and ((t.weekday() >= 5) != (day.weekday() >= 5) or age > 28):
            continue
        if method == "weekday" and (t.weekday() != day.weekday() or age > 28):
            continue
        weight = 0.5 ** (age - 1) if method == "recent_weighted" else 1.0
        by.setdefault(t.hour, []).append((v, weight))
    if not by:
        return None
    return {h: sum(v * w for v, w in vw) / sum(w for _, w in vw) for h, vw in by.items()}


def backtest(series, test_days=7):
    """Each method's mean absolute error and its errors, forecasting each of the last `test_days`
    days from only the days before it (a rolling-origin test). {method: (mae, errors, days)}."""
    from datetime import timedelta  # noqa: F401
    days = sorted({t.date() for t, _ in series})
    scores = {}
    for method in FORECAST_METHODS:
        errors, tested = [], 0
        for day in days[-test_days:]:
            history = [(t, v) for t, v in series if t.date() < day]
            guess = _method_forecast(method, history, day)
            if guess is None:
                continue
            actual = [(t.hour, v) for t, v in series if t.date() == day and t.hour in guess]
            errors += [v - guess[h] for h, v in actual]
            tested += 1
        if errors:
            scores[method] = (sum(abs(e) for e in errors) / len(errors), errors, tested)
    return scores


def _stat_forecast(p, rows):
    """A 24-hour forecast that learns from the data: every method is backtested on the recent days
    and the most accurate one is used, so the choice changes as history accumulates (e.g. the
    weekday method takes over once there are enough weeks)."""
    from datetime import timedelta
    name = p["measures"][0] if "measures" in p else p["measure"]
    fmt = MEASURES[name][3]
    series = _hourly(rows)
    days = sorted({t.date() for t, _ in series})
    if len(days) < 3:
        return {"answer": "A forecast needs at least three days of history; there isn't enough yet.", "chart": None}
    scores = backtest(series)
    fair = {m: s for m, s in scores.items() if s[2] >= 1}
    best = min(fair, key=lambda m: fair[m][0])
    mae, errors, tested = fair[best]
    sd = math.sqrt(sum(e * e for e in errors) / max(len(errors) - 1, 1))
    start = max(t for t, _ in series) + timedelta(hours=1)
    data = []
    for t, v in [x for x in series if x[0] >= start - timedelta(hours=48)]:
        data.append({"x": f"{t:%a} {t.day} {t:%b}, {t:%H}:00", "y": v, "series": "Recent hours"})
    forecasts = {}
    for i in range(24):
        t = start + timedelta(hours=i)
        if t.date() not in forecasts:
            forecasts[t.date()] = _method_forecast(best, [x for x in series if x[0].date() < t.date()], t.date()) or {}
    future = []
    for i in range(24):
        t = start + timedelta(hours=i)
        value = forecasts[t.date()].get(t.hour)
        if value is None:
            continue
        label = f"{t:%a} {t.day} {t:%b}, {t:%H}:00"
        future.append((t, value))
        data.append({"x": label, "y": value, "series": "Forecast"})
        data.append({"x": label, "y": value + 1.96 * sd, "series": "95% range (high)"})
        data.append({"x": label, "y": max(value - 1.96 * sd, 0), "series": "95% range (low)"})
    if not future:
        return {"answer": "There isn't enough recent history to forecast the next 24 hours yet.", "chart": None}
    peak = max(future, key=lambda f: f[1])
    low = min(future, key=lambda f: f[1])
    text = (f"Forecast for the 24 hours from {start:%a %d %b, %H}:00: {_mid(name)} should peak around "
            f"**{peak[0]:%H}:00** (about {_fmt(peak[1], fmt)}) and be lowest around {low[0]:%H}:00 (about "
            f"{_fmt(low[1], fmt)}), give or take {_fmt(1.96 * sd, fmt)} (95% range, from the method's real errors).")
    ranked = sorted(fair.items(), key=lambda kv: kv[1][0])
    text += (f"\n\n**How it chose:** I tested {len(fair)} methods by forecasting each of the last {tested} days "
             "from only the days before it. Most accurate: **" + FORECAST_METHODS[best][0] + f"** (off by "
             f"{_fmt(mae, fmt)} an hour on average)")
    if len(ranked) > 1:
        text += "; then " + "; ".join(f"{FORECAST_METHODS[m][0]} ({_fmt(s[0], fmt)})" for m, s in ranked[1:3])
    text += "."
    waiting = [FORECAST_METHODS[m][0] for m in FORECAST_METHODS if m not in fair]
    text += (f" The test is rerun with every new day of data, so the method changes when another starts doing "
             f"better. Built on {len(days)} days")
    text += (f"; {', '.join(waiting)} joins once there are {FORECAST_METHODS['weekday'][1] + 1} days." if waiting
             else ".")
    chart = {"kind": "line", "title": f"{_name(name)}: recent hours and 24-hour forecast", "x": "x", "y": "y",
             "data": data, "format": fmt, "series": "series", "horizontal": False, "x_title": "Hour (London time)",
             "y_title": _label(name), "temporal_labels": True}
    return {"answer": text, "chart": chart}


def _stat_decomposition(p, rows):
    name = p["measure"]
    fmt = MEASURES[name][3]
    series = _hourly(rows)
    if len(series) < 72:
        return {"answer": "Decomposition needs at least three days of hourly data; there isn't enough yet.",
                "chart": None}
    values = [v for _, v in series]
    trend = [sum(values[i - 12:i + 12]) / 24 if 12 <= i < len(values) - 12 else None for i in range(len(values))]
    detrended = {}
    for (t, v), tr in zip(series, trend):
        if tr is not None:
            detrended.setdefault(t.hour, []).append(v - tr)
    cycle = {h: sum(d) / len(d) for h, d in detrended.items()}
    centre = sum(cycle.values()) / len(cycle)
    cycle = {h: c - centre for h, c in cycle.items()}
    points = [(t, v, tr, cycle.get(t.hour, 0)) for (t, v), tr in zip(series, trend) if tr is not None]
    mean = sum(v for _, v, _, _ in points) / len(points)

    def var(xs):
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / len(xs)

    total = var([v for _, v, _, _ in points]) or 1
    shares = {"daily cycle": var([c for *_, c in points]) / total, "slow trend": var([tr for _, _, tr, _ in points]) / total,
              "irregular": var([v - tr - c for _, v, tr, c in points]) / total}
    peak = max(cycle, key=cycle.get)
    trough = min(cycle, key=cycle.get)
    days = len({t.date() for t, _ in series})
    text = (f"{_name(name)} splits into three parts: a **slow trend** (the 24-hour moving average), a **daily "
            f"cycle** (the typical hour-of-day pattern) and what's left (**irregular** variation). The daily cycle "
            f"accounts for about **{shares['daily cycle']:.0%}** of the hour-to-hour variation, the slow trend "
            f"{shares['slow trend']:.0%} and irregular movements {shares['irregular']:.0%}. The cycle is highest "
            f"around {peak:02d}:00 ({_fmt(cycle[peak], fmt).replace('-', '−')} against the trend) and lowest around "
            f"{trough:02d}:00. The trend ran from {_fmt(min(tr for _, _, tr, _ in points), fmt)} to "
            f"{_fmt(max(tr for _, _, tr, _ in points), fmt)}. With {days} days, a weekly cycle can't be separated yet; "
            "it needs at least two weeks.")
    line = [{"x": f"{t:%a} {t.day} {t:%b}, {t:%H}:00", "y": v, "series": "Each hour"} for t, v, _, _ in points]
    line += [{"x": f"{t:%a} {t.day} {t:%b}, {t:%H}:00", "y": tr, "series": "Slow trend (24-hour average)"}
             for t, _, tr, _ in points]
    bars = [{"x": f"{h:02d}:00", "y": cycle[h]} for h in sorted(cycle)]
    charts = [
        {"kind": "line", "title": f"{_name(name)}: each hour and the slow trend", "x": "x", "y": "y", "data": line,
         "format": fmt, "series": "series", "horizontal": False, "x_title": "Hour (London time)",
         "y_title": _label(name), "temporal_labels": True},
        {"kind": "bar", "title": "The daily cycle: above (+) or below (−) the trend, by hour", "x": "x", "y": "y",
         "data": bars, "format": fmt, "series": None, "horizontal": False, "x_title": "Hour of day (London time)",
         "y_title": "Difference from the trend"},
    ]
    return {"answer": text, "chart": charts[0], "charts": charts}


def kmeans(vectors, k, rounds=60):
    """Plain k-means with a fixed start (the vectors at the low, middle and high end of the average
    level), so the same data always gives the same groups."""
    order = sorted(range(len(vectors)), key=lambda i: sum(vectors[i]))
    centres = [list(vectors[order[int(j * (len(order) - 1) / max(k - 1, 1))]]) for j in range(k)]
    labels = [0] * len(vectors)
    for _ in range(rounds):
        labels = [min(range(k), key=lambda c: sum((a - b) ** 2 for a, b in zip(v, centres[c]))) for v in vectors]
        for c in range(k):
            members = [vectors[i] for i in range(len(vectors)) if labels[i] == c]
            if members:
                centres[c] = [sum(col) / len(members) for col in zip(*members)]
    return labels, centres


def _stat_clusters(p, rows):
    name = p["measure"]
    fmt, entity = MEASURES[name][3], MEASURES[name][5]
    hours = sorted({int(r["h"]) for r in rows})
    by = {}
    for r in rows:
        by.setdefault(r["e"], {})[int(r["h"])] = float(r["v"])
    complete = {e: prof for e, prof in by.items() if len(prof) >= 0.7 * len(hours)}
    if len(complete) < 6:
        return {"answer": f"Grouping needs at least six {entity}s with data through the day; there aren't enough yet.",
                "chart": None}
    names = sorted(complete)
    vectors = []
    for e in names:
        prof = complete[e]
        fill = sum(prof.values()) / len(prof)
        vectors.append([prof.get(h, fill) for h in hours])
    k = 3 if len(names) >= 9 else 2
    labels, centres = kmeans(vectors, k)
    groups = []
    for c in range(k):
        members = [names[i] for i in range(len(names)) if labels[i] == c]
        if not members:
            continue
        level = sum(centres[c]) / len(centres[c])
        peak = hours[max(range(len(hours)), key=lambda i: centres[c][i])]
        groups.append({"c": c, "members": members, "level": level, "peak": peak})
    groups.sort(key=lambda g: -g["level"])
    words = ["Highest", "Middle", "Lowest"] if len(groups) == 3 else ["Higher", "Lower"]
    lines, data = [], []
    for word, g in zip(words, groups):
        label = f"{word}: {len(g['members'])} {entity}s"
        # The most typical members: closest to the group's average day
        typical = sorted(g["members"], key=lambda m: sum(
            (a - b) ** 2 for a, b in zip(vectors[names.index(m)], centres[g["c"]])))
        examples = ", ".join(_entity_name(m, entity) for m in typical[:4])
        lines.append(f"- **{label}**, averaging {_fmt(g['level'], fmt)} and peaking around {g['peak']:02d}:00 "
                     f"(e.g. {examples})")
        for i, h in enumerate(hours):
            band = f" {BANDS[h]}" if MEASURES[name][0] == "routes" and h in BANDS else ""
            data.append({"x": f"{h:02d}:00{band}", "y": centres[g["c"]][i], "series": label})
    text = (f"Grouping {len(names)} {entity}s by their hour-by-hour pattern of {_mid(name)} (k-means, {len(groups)} "
            "groups) gives:\n\n" + "\n".join(lines)
            + f"\n\nEach line in the chart is a group's average day. Groups will settle as more days are collected.")
    chart = {"kind": "line", "title": f"{_name(name)}: average day of each group", "x": "x", "y": "y", "data": data,
             "format": fmt, "series": "series", "horizontal": False, "x_title": "Hour of day (London time)",
             "y_title": _label(name)}
    return {"answer": text, "chart": chart}


# Where in London: a map of the bike stations, with spatial statistics ---------------------------
# Station history (bike_station_hourly) joined to the live BikePoint feed's coordinates by station
# id. Two spatial measures: how much of the problem lies near the centre (Charing Cross), and
# Moran's I, whether stations with the problem sit next to each other (spatial autocorrelation).

MAP_WORDS = (r"\b(maps?|where in london|where (do|does|are|is)|which (areas?|parts?) of london|geograph\w*|spatial\w*|neighbou?rhoods?|"
             r"concentrated in the (centre|center)|where are)\b")
CENTRE = (51.5074, -0.1278)   # Charing Cross, the traditional centre of London
PERIODS = [   # question words, label, SQL filter on London time
    (r"\b(evening (rush|peak)|after work|16-19|4 ?pm)\b", "the weekday evening rush (16:00–19:00)",
     f"EXTRACT(HOUR FROM {LONDON}) BETWEEN 16 AND 18 AND EXTRACT(DAYOFWEEK FROM {LONDON}) NOT IN (1, 7)"),
    (r"\b(morning (rush|peak)|before work|07-10|7 ?am|commute)\b", "the weekday morning rush (07:00–10:00)",
     f"EXTRACT(HOUR FROM {LONDON}) BETWEEN 7 AND 9 AND EXTRACT(DAYOFWEEK FROM {LONDON}) NOT IN (1, 7)"),
    (r"\b(night|overnight)\b", "overnight (00:00–05:00)", f"EXTRACT(HOUR FROM {LONDON}) < 5"),
    (r"\bweekends?\b", "weekends", f"EXTRACT(DAYOFWEEK FROM {LONDON}) IN (1, 7)"),
]


def map_plan(question):
    """{"measure", "period", "label"} for a "where in London" bike question, else None."""
    text = question.lower()
    if not _has(MAP_WORDS, text) or _mode(text) != "bike":
        return None
    found = sorted((m.start(), subject) for subject, p in SUBJECTS for m in [re.search(p, text)] if m)
    measures = [m for m in (_measure(subject, text) for _, subject in found) if m]
    if _has(r"\bout of bikes\b|\brun(s)? out\b", text) and not measures:
        measures = ["bike_empty"]
    period = next(((label, sql) for pattern, label, sql in PERIODS if _has(pattern, text)), ("all day", "TRUE"))
    return {"measure": measures[0] if measures else "bike_empty", "label": period[0], "period": period[1]}


def map_sql(p):
    _, where, expr, *_ = MEASURES[p["measure"]]
    return f"""
        SELECT station_id, ANY_VALUE(station_name) AS name, {expr} AS v, SUM(observed_minutes) AS w,
               COUNT(DISTINCT DATE(hour_start, 'Europe/London')) AS days
        FROM `{DATASET}.bike_station_hourly` WHERE {WINDOW} AND {where} AND {p["period"]}
        GROUP BY station_id HAVING SUM(observed_minutes) > 0"""


def _km(a, b):
    """Great-circle distance in km between (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def morans_i(points, band_km=0.8, rounds=199):
    """(Moran's I, permutation p-value): do neighbouring stations (within band_km) have similar
    values? I > 0: alike values cluster; ~0: scattered at random. p: share of random reshuffles of
    the values across the same stations giving an I at least as large."""
    from random import Random
    n = len(points)
    neighbours = [[j for j in range(n) if j != i and _km(points[i][:2], points[j][:2]) <= band_km] for i in range(n)]
    links = sum(len(nb) for nb in neighbours)
    if not links:
        return None, None

    def stat(values):
        mean = sum(values) / n
        dev = [v - mean for v in values]
        denom = sum(d * d for d in dev)
        if not denom:
            return 0.0
        return (n / links) * sum(dev[i] * dev[j] for i in range(n) for j in neighbours[i]) / denom

    values = [p[2] for p in points]
    observed = stat(values)
    rng, shuffled, extreme = Random(5), list(values), 0
    for _ in range(rounds):
        rng.shuffle(shuffled)
        if stat(shuffled) >= observed:
            extreme += 1
    return observed, (extreme + 1) / (rounds + 1)


def run_map(p, sources):
    name = p["measure"]
    fmt = MEASURES[name][3]
    rows = [dict(r) for r in sources.query(map_sql(p))]
    try:
        places = {s["station_id"]: (s["lat"], s["lon"]) for s in sources.bike_rows()}
    except Exception:   # noqa: BLE001 - the coordinates come from the live feed
        places = {}
    points = [(places[r["station_id"]][0], places[r["station_id"]][1], float(r["v"]), float(r["w"]), r["name"])
              for r in rows if r["station_id"] in places and r.get("v") is not None]
    if len(points) < 20:
        return {"answer": "The map needs the live station locations, which aren't available right now; please try "
                          "again in a minute.", "chart": None}
    total = sum(v * w for _, _, v, w, _ in points) or 1
    near = [pt for pt in points if _km(pt[:2], CENTRE) <= 3]
    near_share = sum(v * w for _, _, v, w, _ in near) / total
    station_share = len(near) / len(points)
    overall = sum(v * w for _, _, v, w, _ in points) / sum(w for *_, w, _ in points)
    moran, p_value = morans_i([(lat, lon, v) for lat, lon, v, _, _ in points])
    worst = sorted(points, key=lambda pt: -pt[2])[:10]
    days = int(max(r["days"] for r in rows))
    what = _mid(name)
    text = (f"During {p['label']}, {what} averaged **{_fmt(overall, fmt)}** across {len(points)} stations. "
            f"**{near_share:.0%} of it was within 3 km of Charing Cross**, where {station_share:.0%} of the stations "
            "are, so the problem is " + ("concentrated in the centre." if near_share > station_share * 1.2 else
                                         "concentrated outside the centre." if near_share < station_share * 0.8 else
                                         "spread roughly in line with where stations are."))
    if moran is not None:
        text += (f" Spatial autocorrelation (Moran's I, neighbours within 800 m) is **{moran:.2f}** (p = "
                 f"{p_value:.3f}): " + ("stations with the problem **cluster together** in hotspots rather than "
                                        "being scattered." if moran > 0.05 and p_value < 0.05 else
                                        "no clear clustering; affected stations are scattered."))
    text += (" Worst: " + "; ".join(f"{n} ({_fmt(v, fmt)})" for _, _, v, _, n in worst[:5]) + "."
             f"\n\n_Based on {days} days of history; station locations from the live TfL BikePoint feed._")
    data = [{"lat": lat, "lon": lon, "value": v, "name": n} for lat, lon, v, _, n in points]
    charts = [
        {"kind": "map", "title": f"{_name(name)} during {p['label']}, by station", "data": data, "format": fmt,
         "x": "lon", "y": "lat", "series": None, "horizontal": False, "x_title": "", "y_title": _label(name)},
        {"kind": "bar", "title": "The 10 worst stations", "x": "x", "y": "y",
         "data": [{"x": n, "y": v} for _, _, v, _, n in worst], "format": fmt, "series": None, "horizontal": True,
         "x_title": "", "y_title": _label(name)},
    ]
    return {"answer": text, "chart": charts[0], "charts": charts}
