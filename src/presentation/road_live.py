"""Turn live TfL road responses into rows for a live roads panel: every current road disruption
(works and live incidents) with its location, and the status of the main road corridors.

Pure functions (no Streamlit, no network), shared by the portfolio and future MCP tools.
"""

import json

# Weight of each severity on the disruption heat map.
SEVERITY_WEIGHT = {"No impact": 0, "Minimal": 1, "Moderate": 2, "Serious": 3, "Severe": 4}


def _point(record):
    """(lat, lon) from TfL's "[lon,lat]" text, or None."""
    try:
        lon, lat = json.loads(record.get("point") or "")
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _closed_streets(record):
    """Street sections TfL marks as closed. The record's own `hasClosures` flag was false on
    every record seen on 2026-10-29, including those with closed streets, so it isn't used."""
    return sorted({s.get("name") for s in record.get("streets") or []
                   if isinstance(s, dict) and s.get("closure") not in (None, "", "Open") and s.get("name")})


def disruptions(data):
    """One row per located road disruption, live incidents first, then by severity."""
    rows = []
    for record in data if isinstance(data, list) else []:
        if not isinstance(record, dict):
            continue
        point = _point(record)
        if point is None:
            continue
        severity = record.get("severity") or "Minimal"
        category = record.get("category") or "Other"
        rows.append({
            "id": record.get("id"),
            "lat": point[0],
            "lon": point[1],
            "category": category,
            "sub_category": record.get("subCategory") or "",
            "is_incident": category != "Works",
            "severity": severity,
            "weight": SEVERITY_WEIGHT.get(severity, 1),
            "location": " ".join((record.get("location") or "").split()),
            "roads": [c.upper() for c in record.get("corridorIds") or []],
            "comments": " ".join((record.get("comments") or "").split()),
            "update": " ".join((record.get("currentUpdate") or "").split()),
            "start": record.get("startDateTime"),
            "end": record.get("endDateTime"),
            "closed_streets": _closed_streets(record),
        })
    return sorted(rows, key=lambda r: (not r["is_incident"], -r["weight"], r["location"]))


def corridors(data):
    """The main road corridors and their status, worst first."""
    rows = [{
        "id": r.get("id"),
        "name": r.get("displayName"),
        "status": r.get("statusSeverity"),
        "description": r.get("statusSeverityDescription"),
        "good": r.get("statusSeverity") == "Good",
    } for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: (r["good"], r["name"] or ""))


def summary(disruption_rows, corridor_rows):
    """Totals for the tiles."""
    incidents = [r for r in disruption_rows if r["is_incident"]]
    return {
        "disruptions": len(disruption_rows),
        "incidents": len(incidents),
        "serious_incidents": sum(r["weight"] >= 3 for r in incidents),
        "works": len(disruption_rows) - len(incidents),
        "with_closures": sum(bool(r["closed_streets"]) for r in disruption_rows),
        "corridors": len(corridor_rows),
        "corridors_delayed": sum(not r["good"] for r in corridor_rows),
    }
