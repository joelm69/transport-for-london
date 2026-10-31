"""What this platform collects, where it goes, and what could be built next: the assistant's
project awareness. Only endpoints implemented in this repository are listed (config/*.json,
src/collectors, scripts/bus_network_snapshot.py), with how often each is read and since when.

Pure data and functions, shared by the portfolio and the TfL repository.
"""

import re

# name, TfL endpoint, collection, refresh, since (London time), live use, history (where it is kept)
DATASETS = [
    ("Rail line status", "/Line/Mode/tube,dlr,overground,elizabeth-line,tram/Status", "VM service, changes only",
     "every 30 s", "21 Oct 2026", "London Rail board, line cards",
     "GCS → BigQuery line_disruption_hourly → Sheet line_reliability_daily → Tableau rail dashboard"),
    ("Station notices", "/StopPoint/Mode/tube,dlr,overground,elizabeth-line,tram/Disruption",
     "VM service, changes only", "every 60 s", "21 Oct 2026", "Station notices, accessibility over time",
     "GCS → BigQuery station_accessibility_hourly → Sheet station_accessibility_daily"),
    ("Tube, tram and 12 bus routes' arrivals", "/Mode/tube/Arrivals, /Mode/tram/Arrivals, /Line/{12 routes}/Arrivals",
     "VM, Kafka streaming (arrivals detected as predictions vanish)", "every 15 s", "22 Oct 2026",
     "Next buses at a stop",
     "GCS → BigQuery arrival_line_hourly, arrival_stop_hourly, arrival_accuracy_hourly → Sheet arrival_accuracy_daily"),
    ("All bus routes", "/Mode/bus/Arrivals (every route, about 100 MB a poll)", "Cloud Run job",
     "every 30 min", "26 Oct 2026", "Bus network notices",
     "GCS → BigQuery bus views → Sheet bus_routes_daily, bus_vehicles_daily → Tableau bus dashboard"),
    ("Bus route reference", "/Line/{id}/Route/Sequence, /StopPoint/Mode/bus, /Line/{ids}/Route",
     "one-off collection", "reference from 16 Oct 2026", "16 Oct 2026", "Bus route map",
     "GCS → BigQuery route_stop_occurrences → Sheet bus_route_paths"),
    ("Bus line status", "/Line/Mode/bus/Status", "read live", "live only", "—", "London Buses status", "not kept"),
    ("Santander Cycles docking stations", "/BikePoint", "VM service, changes only", "every 2 min",
     "29 Oct 2026 17:10", "Live bikes map and tiles",
     "GCS → BigQuery bikepoint_* → Sheet bike_stations_daily, bike_station_hours → Tableau bikes dashboard"),
    ("Road disruptions and main-road status", "/Road/all/Disruption, /Road/all/Status", "VM service, changes only",
     "every 5 min", "29 Oct 2026 18:38", "Live incidents map, heat map, road tiles",
     "GCS → BigQuery road_* → Sheet road_corridor_hours, road_disruptions → Tableau roads dashboard"),
]
NOT_COLLECTED = ("air quality (TfL's feed is only a daily London-wide forecast), weather, passenger numbers, "
                 "timetables (so no Excess Wait Time), car parks and taxi data")
CHAT_TABLES = ("This chat queries the serving dataset tfl_portfolio: line_disruption_hourly, "
               "station_accessibility_hourly, arrival_line_hourly, arrival_stop_hourly, arrival_accuracy_hourly and "
               "the four recommendation tables. The bike, road and all-routes bus history is in BigQuery but not "
               "yet in that dataset.")

FEATURES = {
    "Already built": [
        "Live rail, station, bus, bike and road boards on this page",
        "Tableau dashboards for rail reliability, buses, bikes and roads",
        "Data-driven business recommendations for rail, buses, bikes and roads",
        "This chat: patterns, relationships, statistics, recommendation figures and travel advice",
        "An MCP server giving AI models the same tools",
    ],
    "Possible with the data already collected": [
        "Bus route reliability ranking for all 675 routes (bunching, waits, speed) from the all-routes poll",
        "Station accessibility ranking: hours with lifts out, by station and line",
        "Disruption impact score: disrupted line-hours weighted by how busy the hour is",
        "Arrival reliability heatmap: countdown error by line and hour",
        "Bike station rebalancing priority list: empty or full in the peaks, by area",
        "Road corridor reliability: share of time Serious, with the roadworks on each",
        "Cross-modal comparison: does a rail disruption push up bus bunching nearby?",
        "Forecasting next week's disruption by line once there are several weeks of history",
    ],
    "Needs new data or development": [
        "Excess Wait Time (TfL's official measure): needs bus timetables",
        "Passenger impact in journeys: needs passenger counts (TfL's NUMBAT or Oyster data)",
        "Weather effects on delays: needs a weather feed",
        "Air quality along routes: needs measured air quality (e.g. the London Air Quality Network)",
        "Bike and road analysis in this chat: needs their hourly history added to the chat's dataset",
    ],
}

ASKS_DATA = (r"\b(what|which) (data|datasets?|endpoints?|apis?|sources?|feeds?)\b|\bdata sources?\b|"
             r"\bhow (often|frequently)\b.*\b(collect\w*|updat\w*|refresh\w*|poll\w*|read)\b|\bendpoints?\b|"
             r"\bwhere does .* data come from\b|\bcollection\b")
ASKS_FEATURES = (r"\b(new|more|future|next) (features?|analys\w*|ideas?)\b|\bsuggest\w* (a |some )?features?\b|"
                 r"\bwhat (else )?(could|can|should) (be|we) (built|build|add\w*|develop\w*)\b|\broadmap\b")


def asks_about_data(question):
    return re.search(ASKS_DATA, question.lower()) is not None


def asks_about_features(question):
    return re.search(ASKS_FEATURES, question.lower()) is not None


def describe(question=""):
    """The datasets, or just those the question names (e.g. "how often is BikePoint collected?")."""
    text = question.lower()
    chosen = [d for d in DATASETS if any(w in text for w in re.findall(r"[a-z]{4,}", d[0].lower() + " " + d[1].lower())
                                       if w not in ("line", "mode", "status", "route", "routes", "every"))]
    rows = chosen or DATASETS
    lines = [f"- **{name}** ({endpoint}): {collection}, {refresh}, since {since}. Live: {live}. History: {history}."
             for name, endpoint, collection, refresh, since, live, history in rows]
    text = ("Here's what this platform collects from the TfL Unified API:\n\n" if not chosen else "") + "\n".join(lines)
    if not chosen:
        text += f"\n\nNot collected: {NOT_COLLECTED}.\n\n{CHAT_TABLES}"
    return text


def features():
    parts = []
    for heading, items in FEATURES.items():
        parts.append(f"**{heading}**\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(parts)
