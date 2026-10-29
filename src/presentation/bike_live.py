"""Turn a live TfL BikePoint response into rows for a live bikes panel: one row per docking
station with its state (empty / full / ok) and network totals.

Pure functions (no Streamlit, no network), shared by the portfolio and future MCP tools.
Live only: history comes from the BikePoint collector (docs/endpoints/bikepoint.md).
"""

COUNT_FIELDS = {
    "NbBikes": "bikes",
    "NbStandardBikes": "standard_bikes",
    "NbEBikes": "e_bikes",
    "NbEmptyDocks": "empty_docks",
    "NbDocks": "docks",
}


def _properties(record):
    return {p.get("key"): p.get("value") for p in record.get("additionalProperties") or []
            if isinstance(p, dict)}


def station_state(bikes, empty_docks):
    """empty: no bike to take; full: nowhere to return one; ok otherwise."""
    if bikes == 0:
        return "empty"
    if empty_docks == 0:
        return "full"
    return "ok"


def stations(data):
    """One row per readable docking station, sorted by name. Stations with a missing or
    non-numeric count, or without a location, are skipped."""
    rows = []
    for record in data if isinstance(data, list) else []:
        if not isinstance(record, dict):
            continue
        props = _properties(record)
        values = {name: props.get(key) for key, name in COUNT_FIELDS.items()}
        if not all(isinstance(v, str) and v.isdigit() for v in values.values()):
            continue
        if not (isinstance(record.get("lat"), (int, float)) and isinstance(record.get("lon"), (int, float))):
            continue
        counts = {name: int(v) for name, v in values.items()}
        rows.append({
            "station_id": record.get("id"),
            # TfL writes "River Street , Clerkenwell": tidy the space before the comma.
            "name": " ".join((record.get("commonName") or "").replace(" ,", ",").split()),
            "lat": record["lat"],
            "lon": record["lon"],
            **counts,
            # Neither holding a bike nor free: broken or reserved docks. Derived, not reported by TfL.
            "out_of_use_docks": max(counts["docks"] - counts["bikes"] - counts["empty_docks"], 0),
            "state": station_state(counts["bikes"], counts["empty_docks"]),
            "installed": props.get("Installed") == "true",
            "locked": props.get("Locked") == "true",
        })
    return sorted(rows, key=lambda r: r["name"])


def summary(rows):
    """Network totals for the tiles."""
    docks = sum(r["docks"] for r in rows)
    out_of_use = sum(r["out_of_use_docks"] for r in rows)
    return {
        "stations": len(rows),
        "bikes": sum(r["bikes"] for r in rows),
        "e_bikes": sum(r["e_bikes"] for r in rows),
        "empty_docks": sum(r["empty_docks"] for r in rows),
        "docks": docks,
        "empty_stations": sum(r["state"] == "empty" for r in rows),
        "full_stations": sum(r["state"] == "full" for r in rows),
        "out_of_use_docks": out_of_use,
        "out_of_use_share": out_of_use / docks if docks else 0.0,
    }
