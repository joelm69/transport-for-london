"""The platform's tools for AI: one set of read-only functions, shared by the MCP server
(src/mcp_server/server.py, for any MCP client such as Claude Desktop) and the AI agent
(src/agent/agent.py, which calls them in-process).

Every tool returns text (JSON or a short report) and never raises for an expected failure: a
problem is returned as {"error": ...} so the model can say what went wrong. Nothing here writes.

- Live: TfL's API right now (line status, bike docking stations, main roads).
- History: the serving dataset tfl_portfolio only (the same tables the portfolio reads), with a
  per-query cost cap. The cloud service account can read nothing else.
- Docs: the platform's own definitions (src/agent/docs_search.py), so numbers are explained the
  way the dashboards define them.
"""

import json
import re
from datetime import date, datetime, timezone

from src.agent import docs_search
from src.presentation import (bike_live, bike_recommendation, bus_recommendation, line_board,
                              rail_recommendation, road_live, road_recommendation)

PROJECT = "transport-for-london-511318"
DATASET = f"{PROJECT}.tfl_portfolio"
MAX_BYTES_BILLED = 200 * 1024 * 1024     # 200 MB a query: the serving tables are far smaller
MAX_ROWS = 50
LINE_STATUS_PATH = "/Line/Mode/tube,dlr,overground,elizabeth-line,tram/Status"
SEVERITY_CLASSES = {"good": [10, 18], "closed": [20]}   # as config/line_status.json

RECOMMENDATIONS = {
    "rail": rail_recommendation,
    "bus": bus_recommendation,
    "bikes": bike_recommendation,
    "roads": road_recommendation,
}
TABLES = {"rail": "rail_recommendation", "bus": "bus_recommendation",
          "bikes": "bike_recommendation", "roads": "road_recommendation"}

_clients = {}


def _json(value):
    def default(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        return str(v)
    return json.dumps(value, default=default, ensure_ascii=False)


def _tfl():
    if "tfl" not in _clients:
        from src.clients.tfl import TflClient
        _clients["tfl"] = TflClient.from_environment(max_attempts=2, timeout=20)
    return _clients["tfl"]


def _bigquery():
    if "bigquery" not in _clients:
        from google.cloud import bigquery
        _clients["bigquery"] = bigquery.Client(project=PROJECT)
    return _clients["bigquery"]


# Docs ----------------------------------------------------------------------------------------

def search_docs(query: str) -> str:
    """Search the platform's documentation for how a figure is defined or measured.

    Use this before explaining what a number means (for example bunching, delayed share,
    headroom, docks out of use, time to clear) or its limits.

    Args:
        query: The question or the terms to look up, e.g. "how is bus bunching defined".
    """
    hits = docs_search.search(query, limit=4)
    if not hits:
        return _json({"results": [], "note": "Nothing in the documentation matches."})
    return _json({"results": [{"source": h["source"], "title": h["title"], "text": h["text"]}
                              for h in hits]})


# Live ----------------------------------------------------------------------------------------

def live_line_status() -> str:
    """Current status of every Tube, DLR, Overground, Elizabeth line and tram line, live from TfL.

    Returns each line with a problem (status, reason and how long it has lasted) and a count of
    lines with good service.
    """
    try:
        data = _tfl().get_json(LINE_STATUS_PATH, {"detail": "false"}).data
    except Exception as error:   # noqa: BLE001 - reported to the model, not raised
        return _json({"error": f"TfL's line status feed did not answer: {error}"})
    now = datetime.now(timezone.utc)
    cards = line_board.line_cards(line_board.board_rows(data, now, SEVERITY_CLASSES))
    problems = [{"line": c["line_name"], "mode": c["mode_name"], "status": c["status"],
                 "other_statuses": c["other_statuses"], "reasons": c["reasons"], "for": c["duration"]}
                for c in cards if c["level"] != "good"]
    return _json({"fetched_at_utc": now.isoformat(timespec="seconds"),
                  "lines": len(cards), "good_service": len(cards) - len(problems), "problems": problems})


def live_bike_stations(name: str) -> str:
    """Live bikes and free docks at Santander Cycles docking stations whose name matches.

    Args:
        name: Part of a station or area name, e.g. "Waterloo" or "Hyde Park".
    """
    try:
        rows = bike_live.stations(_tfl().get_json("/BikePoint").data)
    except Exception as error:   # noqa: BLE001
        return _json({"error": f"TfL's BikePoint feed did not answer: {error}"})
    words = name.lower().split()
    matches = [r for r in rows if all(w in r["name"].lower() for w in words)]
    return _json({
        "network": bike_live.summary(rows),
        "matches": len(matches),
        "stations": [{k: r[k] for k in ("name", "bikes", "e_bikes", "empty_docks", "docks",
                                        "out_of_use_docks", "state")} for r in matches[:15]],
    })


def live_roads() -> str:
    """Live state of London's roads from TfL: each main road's status and current incidents.

    Returns the main roads rated Serious or worse, network totals, and the live incidents
    (collisions, breakdowns, signal faults...) with their location and severity.
    """
    try:
        disruptions = road_live.disruptions(_tfl().get_json("/Road/all/Disruption").data)
        corridors = road_live.corridors(_tfl().get_json("/Road/all/Status").data)
    except Exception as error:   # noqa: BLE001
        return _json({"error": f"TfL's road feeds did not answer: {error}"})
    incidents = [d for d in disruptions if d.get("is_incident")]
    return _json({
        "summary": road_live.summary(disruptions, corridors),
        "main_roads_delayed": [c for c in corridors if not c.get("good")],
        "incidents": [{k: d[k] for k in ("category", "sub_category", "severity", "location", "roads",
                                         "comments", "start", "closed_streets")} for d in incidents[:25]],
    })


# History -------------------------------------------------------------------------------------

def get_recommendation(area: str) -> str:
    """The business recommendation for one area, from the last 7 complete London days: the
    summary, findings and recommendations shown on the portfolio, plus the figures behind them.

    Args:
        area: One of "rail", "bus", "bikes" or "roads".
    """
    module = RECOMMENDATIONS.get(area)
    if module is None:
        return _json({"error": f"Unknown area {area!r}: use rail, bus, bikes or roads."})
    try:
        rows = list(_bigquery().query_and_wait(f"SELECT * FROM `{DATASET}.{TABLES[area]}`"))
    except Exception as error:   # noqa: BLE001
        return _json({"error": f"Could not read the {area} figures: {error}"})
    if not rows:
        return _json({"error": f"No {area} figures yet."})
    row = dict(rows[0])
    report = {"area": area, "figures": row}
    if hasattr(module, "summary"):
        report["summary"] = module.summary(row)
    if hasattr(module, "findings"):
        report["findings"] = [{"headline": h, "text": t} for h, t in module.findings(row)]
    if hasattr(module, "recommendations"):
        report["recommendations"] = [f"{a} {d}" for a, d in module.recommendations(row)]
    if hasattr(module, "period"):
        report["period"] = module.period(row)
    return _json(report)


def describe_history_tables() -> str:
    """List the history tables the agent can query with query_history, with their columns."""
    sql = (f"SELECT table_name, STRING_AGG(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) "
           f"AS columns FROM `{DATASET}.INFORMATION_SCHEMA.COLUMNS` GROUP BY table_name ORDER BY table_name")
    try:
        rows = list(_bigquery().query_and_wait(sql))
    except Exception as error:   # noqa: BLE001
        return _json({"error": f"Could not list the tables: {error}"})
    return _json({"dataset": DATASET, "tables": {r["table_name"]: r["columns"] for r in rows}})


def check_sql(sql):
    """None if the SQL is a single read-only query on tfl_portfolio only, else the reason."""
    text = sql.strip().rstrip(";").strip()
    if ";" in text:
        return "Only one statement is allowed."
    if not re.match(r"(?is)^(select|with)\b", text):
        return "Only SELECT queries are allowed."
    if re.search(r"(?i)\b(insert|update|delete|merge|create|drop|alter|truncate|grant|revoke|call|execute)\b",
                 re.sub(r"'[^']*'|\"[^\"]*\"", "", text)):
        return "Only read-only queries are allowed."
    for ref in re.findall(r"`([^`]+)`", text):
        if not ref.startswith(f"{DATASET}."):
            return f"Only tables in {DATASET} can be queried (not {ref})."
    # Every FROM/JOIN must name a backticked table in the dataset, a CTE, UNNEST or a subquery.
    ctes = {m.lower() for m in re.findall(r"(?i)\b(\w+)\s+as\s*\(", text)}
    for target in re.findall(r"(?i)\b(?:from|join)\s+([^\s,()]+)", text):
        if target.startswith("`") or target.lower() in ctes or target.lower() == "unnest":
            continue
        return f"Name tables in full, e.g. `{DATASET}.line_disruption_hourly` (not {target})."
    return None


def query_history(sql: str) -> str:
    """Run one read-only BigQuery SQL query on the platform's history (dataset
    transport-for-london-511318.tfl_portfolio), for questions the recommendations don't answer.

    Call describe_history_tables first to see the tables and columns. Name tables in full with
    backticks. Returns at most 50 rows; each query may read at most 200 MB.

    Args:
        sql: A single SELECT (or WITH ... SELECT) statement in GoogleSQL.
    """
    problem = check_sql(sql)
    if problem:
        return _json({"error": problem})
    from google.cloud import bigquery
    config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED, use_legacy_sql=False)
    try:
        rows = _bigquery().query_and_wait(sql.strip().rstrip(";"), job_config=config, max_results=MAX_ROWS)
        result = [dict(r) for r in rows]
    except Exception as error:   # noqa: BLE001
        return _json({"error": f"The query failed: {error}"})
    return _json({"rows": result, "row_count": len(result), "truncated_at": MAX_ROWS})


TOOLS = [search_docs, live_line_status, live_bike_stations, live_roads, get_recommendation,
         describe_history_tables, query_history]
