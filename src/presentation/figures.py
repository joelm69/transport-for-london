"""Charts of the business recommendations' figures (rail, bus, bikes, roads; the last 7 complete
London days), for "Ask the data": every statistic the recommendations rest on can be asked for
and drawn, with how it is worked out.

A question chooses a figure by its wording ("theoretical versus actual wait", "passenger-hours
lost", "planned versus unplanned", "docks out of use", "roadworks overrun"...). An answer can carry
several charts, drawn side by side, including a "callout": one large number with its context.

Pure functions: `sources.recommendation(area)` returns (figures row, text module) or None.
Chart descriptions follow analysis.py, plus {"kind": "callout", "value", "label", "detail"}.
"""

import re

CHART_ASK = (r"\b(chart|graph|plot|visuali[sz]\w*|draw|show|display|compare|comparison|callout|bars?|pie|"
             r"side[- ]by[- ]side)\b")

# name: (area, question words, needs chart words too?)
FIGURES = {
    "bus_excess_wait": ("bus", r"\b(theoretical|even(ly)?[- ]spaced|even spacing|actual( experienced)? wait|"
                               r"experienced wait|extra wait\w*|excess wait\w*|uneven spacing|passenger[- ]hours|hidden cost|"
                               r"wait(ing)? penalty|cost of (bunching|uneven))\b", False),
    "bus_most_bunched": ("bus", r"\b(most bunched|bunch\w* most|worst bunching)\b", True),
    "bus_slowest": ("bus", r"\b(slowest|slow routes?|speeds? (of|by) routes?)\b", True),
    "rail_service_split": ("rail", r"\b(planned (works )?(vs\.?|versus|and|or|against) unplanned|"
                                   r"unplanned (disruption|delays?)|without planned)\b", False),
    "rail_train_vs_track": ("rail", r"\b(trains? (vs\.?|versus|or|not|against) (track|infrastructure)|"
                                    r"infrastructure|train faults?|fleet)\b", True),
    "rail_top_lines": ("rail", r"\b(top|worst|most disrupted|least reliable) lines\b", True),
    "bike_empty_full": ("bikes", r"\b(empty|full)\b", True),
    "bike_out_of_use": ("bikes", r"\b(out of use|broken docks?|unusable|faulty docks?)\b", False),
    "road_clear_times": ("roads", r"\b(time to clear|clear(ing|ed)? times?|how long .*(incidents?|clear))\b", False),
    "road_overrun": ("roads", r"\b(overrun\w*|roadworks? (late|on time|finish))\b", False),
    "road_delayed": ("roads", r"\b(seriously delayed|delayed main roads|main roads (rated|delayed))\b", True),
}
AREA_WORDS = {
    "bus": r"\b(bus(es)?|routes?)\b",
    "rail": r"\b(tube|underground|trains?|rail|lines?|overground|dlr|elizabeth)\b",
    "bikes": r"\b(bikes?|bicycles?|cycling|cyclists?|cycle hire|docks?|docking|stations?|santander)\b",
    "roads": r"\b(roads?|traffic|incidents?|roadworks?|works|corridors?)\b",
}
SUGGESTIONS = [
    "Plot theoretical versus actual bus wait time and the passenger-hours lost",
    "Show planned versus unplanned rail disruption",
    "Chart how often bike stations are empty or full",
    "Chart how long road incidents take to clear",
    "How many bike docks are out of use?",
    "Do roadworks overrun?",
]


def _has(pattern, text):
    return re.search(pattern, text) is not None


def choose(question):
    """The figure a question asks for or None."""
    text = question.lower()
    if _has(r"\b(right now|now|currently|at the moment)\b", text):
        return None   # the live answers cover "now"
    for name, (area, pattern, needs_chart) in FIGURES.items():
        if not _has(pattern, text):
            continue
        if needs_chart and not _has(CHART_ASK, text):
            continue
        # A figure shared by name with another mode (e.g. "empty" for bikes) needs its area named
        if area in ("bikes",) and not _has(AREA_WORDS[area], text):
            continue
        return name
    return None


def run(name, sources):
    area = FIGURES[name][0]
    found = sources.recommendation(area)
    if not found:
        return {"answer": f"The {area} figures aren't available right now.", "charts": []}
    row, text = found
    result = globals()[f"_{name}"](row)
    period = text.period(row) if hasattr(text, "period") else None
    if period:
        result["answer"] += f"\n\n_Figures for {period}, the last 7 complete days with data; they update daily._"
    return result


def _bar(title, data, fmt, x_title="", y_title="", horizontal=False):
    return {"kind": "bar", "title": title, "x": "x", "y": "y", "data": data, "format": fmt, "series": None,
            "horizontal": horizontal, "x_title": x_title, "y_title": y_title}


def _callout(value, label, detail):
    return {"kind": "callout", "value": value, "label": label, "detail": detail}


def _pct(v, d=0):
    return f"{v * 100:.{d}f}%"


# Bus ------------------------------------------------------------------------------------------

def _bus_excess_wait(row):
    even, actual = row["even_wait_minutes"], row["wait_minutes"]
    extra, share, hours = row["extra_wait_minutes"], row["extra_wait_share"], row["passenger_hours_per_day"]
    journeys = 1_842_000_000 / 365
    text = (f"If buses came at perfectly even intervals, a passenger arriving at random would wait half the "
            f"typical gap: **{even:.1f} min** (gap {row['gap_minutes']:.1f} min). Because buses bunch, they actually "
            f"waited **{actual:.1f} min**: **{extra:.1f} min ({_pct(share, 1)}) longer**. Across about "
            f"{journeys / 1e6:.2f} million bus journeys a day (TfL: 1.842 billion in 2024/25), that is "
            f"{extra:.2f} min × {journeys:,.0f} ÷ 60 ≈ **{hours:,.0f} passenger-hours lost every day**. It is an upper "
            "estimate: it assumes passengers arrive at random, which holds on frequent routes but not on "
            "infrequent ones, where people time their arrival.")
    charts = [
        _bar("Bus wait: even spacing against actual",
             [{"x": "Even spacing (theory)", "y": even, "colour": "#8E8E93"},
              {"x": "Actual (experienced)", "y": actual, "colour": "#FF9F0A"}],
             "minutes", y_title="Average wait (minutes)"),
        _callout(f"{hours:,.0f}", "passenger-hours lost every day",
                 f"to uneven bus spacing: {extra:.1f} extra minutes ({_pct(share, 1)}) on each of about "
                 f"{journeys / 1e6:.2f} million daily journeys"),
    ]
    return {"answer": text, "charts": charts}


def _bus_most_bunched(row):
    routes = row.get("most_bunched") or []
    data = [{"x": f"Route {r['route']}", "y": r["bunching_rate"]} for r in routes]
    text = (f"The most bunched frequent routes (a typical gap of 8 minutes or less): "
            + ", ".join(f"route {r['route']} ({_pct(r['bunching_rate'])})" for r in routes)
            + f", against {_pct(row['bunching_rate'], 1)} across the network. A gap is bunched when it is under "
              "half that poll's median gap.")
    return {"answer": text, "charts": [_bar("Share of gaps bunched, most bunched frequent routes", data, "pct",
                                            y_title="Share of gaps bunched", horizontal=True)]}


def _bus_slowest(row):
    routes = row.get("slowest") or []
    data = [{"x": f"Route {r['route']}", "y": r["kmh"]} for r in routes]
    text = (f"The slowest big routes average {min(r['kmh'] for r in routes):.1f}–{max(r['kmh'] for r in routes):.1f} "
            f"km/h, against about {row['network_kmh']:.1f} km/h across London (door to door, including stops)."
            if routes else "No route had enough data yet.")
    return {"answer": text, "charts": [_bar("Average speed of the slowest routes (km/h)", data, "kmh",
                                            y_title="km/h", horizontal=True),
                                       _callout(f"{row['network_kmh']:.1f} km/h", "London bus average",
                                                "distance along each route ÷ time, door to door")]}


# Rail -----------------------------------------------------------------------------------------

def _rail_service_split(row):
    total, planned, unplanned = row["in_service_hours"], row["planned_hours"], row["unplanned_hours"]
    good = total - planned - unplanned
    text = (f"Of {total:,.0f} line-hours in service, **{_pct(good / total)}** had a good service, "
            f"**{_pct(planned / total)}** had planned works and **{_pct(unplanned / total)}** unplanned disruption. "
            f"Planned works were {_pct(row['planned_share'])} of all disruption. Without them, good service would "
            f"rise from {_pct(row['good_service'], 1)} to **{_pct(row['good_service_without_planned'], 1)}**.")
    data = [{"x": "Good service", "y": good}, {"x": "Planned works", "y": planned},
            {"x": "Unplanned disruption", "y": unplanned}]
    return {"answer": text, "charts": [
        {"kind": "pie", "title": "Line-hours in service, by state", "x": "x", "y": "y", "data": data,
         "format": "hours", "series": None, "horizontal": False, "x_title": "State", "y_title": "Line-hours"},
        _callout(_pct(row["good_service_without_planned"], 1), "good service without planned works",
                 f"against {_pct(row['good_service'], 1)} with them")]}


def _rail_train_vs_track(row):
    train, infra = row["train_share"], row["infrastructure_share"]
    other = max(0.0, 1 - train - infra)
    text = (f"Of the unplanned disruption, **{_pct(train)}** came from trains (cancellations and faults, i.e. "
            f"fleet and crew) and **{_pct(infra)}** from infrastructure (signals, points, track, power); the rest "
            f"({_pct(other)}) had other causes, such as customer incidents or none stated.")
    data = [{"x": "Trains (cancellations, faults)", "y": train}, {"x": "Infrastructure (signals, track, power)",
                                                                  "y": infra}, {"x": "Other or not stated", "y": other}]
    return {"answer": text, "charts": [_bar("Unplanned disruption by kind of cause", data, "pct",
                                            y_title="Share of unplanned disruption", horizontal=True)]}


def _rail_top_lines(row):
    lines = row.get("top_lines") or []
    data = [{"x": f"{l['line_name']} ({l['main_cause'].lower()})", "y": l["unplanned_hours"]} for l in lines]
    text = (f"The lines with the most unplanned disruption, {_pct(row['top_lines_share'])} of it between them: "
            + "; ".join(f"**{l['line_name']}** ({l['unplanned_hours']:.0f} line-hours, mostly "
                        f"{l['main_cause'].lower()})" for l in lines) + ".")
    return {"answer": text, "charts": [_bar("Unplanned disruption, most affected lines (line-hours)", data, "hours",
                                            y_title="Line-hours", horizontal=True)]}


# Bikes ----------------------------------------------------------------------------------------

def _bike_empty_full(row):
    data = [{"x": "Empty, all day", "y": row["empty_share"]}, {"x": "Full, all day", "y": row["full_share"]},
            {"x": "Empty, weekday peaks", "y": row["peak_empty_share"]},
            {"x": "Full, weekday peaks", "y": row["peak_full_share"]}]
    text = (f"Docking stations were empty (no bike to take) {_pct(row['empty_share'], 1)} of the time and full "
            f"(nowhere to return one) {_pct(row['full_share'], 1)}. In the weekday rush hours, when riders need them "
            f"most, those fall to {_pct(row['peak_empty_share'], 1)} and {_pct(row['peak_full_share'], 1)}. The worst "
            f"hour is {int(row['worst_hour']):02d}:00 ({_pct(row['worst_hour_empty_share'])} empty).")
    return {"answer": text, "charts": [_bar("Share of station time empty or full", data, "pct",
                                            y_title="Share of station time")]}


def _bike_out_of_use(row):
    worst = row.get("most_out_of_use") or []
    text = (f"About **{row['out_of_use_docks']:,.0f} docks** were out of use on average, "
            f"{_pct(row['out_of_use_share'], 1)} of the network (docks − bikes − free docks, so broken bikes left "
            f"in docks count too). At {row['quarter_out_stations']} stations a quarter or more of the docks were out "
            "of use.")
    data = [{"x": s["station_name"], "y": s["out_of_use_docks"] / s["docks"]} for s in worst]
    charts = [_callout(f"{row['out_of_use_docks']:,.0f}", "docks out of use",
                       f"{_pct(row['out_of_use_share'], 1)} of all docks, on average")]
    if data:
        charts.insert(0, _bar("Share of docks out of use, worst stations", data, "pct",
                              y_title="Share of docks out of use", horizontal=True))
    return {"answer": text, "charts": charts}


# Roads ----------------------------------------------------------------------------------------

def _road_clear_times(row):
    types = row.get("types") or []
    data = [{"x": t["sub_category"], "y": t["median_minutes"] / 60} for t in types]
    text = (f"Half of all incidents were cleared within **{row['median_clear_minutes'] / 60:.1f} hours** (the median, "
            "so a few very long ones don't distort it). By type: "
            + ", ".join(f"{t['sub_category'].lower()} {t['median_minutes'] / 60:.1f} h" for t in types) + ".")
    return {"answer": text, "charts": [_bar("Median hours to clear, by incident type", data, "hours",
                                            y_title="Median hours to clear", horizontal=True)]}


def _road_overrun(row):
    ended, overran = row["works_ended"], row["works_overran"]
    text = (f"{overran} of the {ended} roadworks that ended (**{_pct(row['works_overrun_share'])}**) were still "
            f"listed after their planned end, but by a median of only **{row['median_overrun_minutes']:.0f} "
            f"minutes** and {row['works_overran_day']} overran by more than a day. Most \"overruns\" are TfL taking "
            "the listing down a little late.")
    data = [{"x": "Finished on time", "y": ended - overran}, {"x": "Overran", "y": overran}]
    return {"answer": text, "charts": [
        _bar("Roadworks that ended: on time or late", data, "count", y_title="Roadworks"),
        _callout(f"{row['median_overrun_minutes']:.0f} min", "median overrun",
                 f"{row['works_overran_day']} of {ended} overran by more than a day")]}


def _road_delayed(row):
    roads = row.get("mostly_delayed") or []
    data = [{"x": r["corridor_name"], "y": r["works"]} for r in roads]
    text = (f"{row['roads_mostly_delayed']} of {row['roads']} main roads were rated Serious or worse at least half "
            f"the time ({_pct(row['delayed_share'])} of all main-road time); the chart shows the roadworks on each.")
    return {"answer": text, "charts": [_bar("Roadworks on the main roads rated Serious", data, "count",
                                            y_title="Roadworks", horizontal=True)]}
