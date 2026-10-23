"""Summaries of one network-wide bus poll (/Mode/bus/Arrivals?count=-1), for history.

One poll covers every bus route running (about 94,000 predictions, 5,300 buses, 84 MB at
19:00 on 2026-10-23), too large for the e2-micro VM, so a Cloud Run Job polls every 30
minutes (scripts/bus_network_snapshot.py) and keeps:

  - slim: the predictions with only the fields analysis uses (kept 30 days in GCS);
  - vehicle rows: one per bus, where it is now (route, direction, next stop, destination);
  - route rows: one per route and direction, the predicted gaps between its buses.

Pure functions: no network, no storage. The network-wide feed has no timeToStation (unlike
/Line/{id}/Arrivals), so the time to each stop is expectedArrival minus the poll time.

Predicted gap: at each stop, the time between successive buses of the same route and
direction predicted to arrive in the next GAP_HORIZON_SECONDS. A route's typical gap is the
median over all its stops; bunched = a gap under half that median (as on the live page).
"""

from datetime import datetime
from statistics import median

SLIM_FIELDS = ("vehicleId", "lineId", "direction", "naptanId", "stationName",
               "expectedArrival", "destinationName")
GAP_HORIZON_SECONDS = 1800   # gaps only among buses due within 30 minutes: predictions further out are vague
BUNCHED_SHARE = 0.5


def slim(predictions):
    """The predictions with only SLIM_FIELDS (about a fifth of the size)."""
    return [{k: p.get(k) for k in SLIM_FIELDS} for p in predictions if isinstance(p, dict)]


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timed(predictions, snapshot_at):
    """Usable predictions with `seconds`: time from the poll to the expected arrival."""
    taken = _utc(snapshot_at)
    for p in predictions:
        if not (isinstance(p, dict) and p.get("vehicleId") and p.get("lineId") and p.get("naptanId")
                and p.get("expectedArrival")):
            continue
        try:
            seconds = round((_utc(p["expectedArrival"]) - taken).total_seconds())
        except ValueError:
            continue
        yield {**p, "seconds": seconds}


def vehicle_rows(predictions, snapshot_at):
    """One row per bus: its soonest prediction is its next stop."""
    soonest = {}
    for p in _timed(predictions, snapshot_at):
        current = soonest.get(p["vehicleId"])
        if current is None or p["seconds"] < current["seconds"]:
            soonest[p["vehicleId"]] = p
    return [
        {"snapshot_at": snapshot_at, "vehicle_id": v, "line_id": p["lineId"], "direction": p.get("direction"),
         "next_naptan_id": p["naptanId"], "next_stop": p.get("stationName"),
         "eta_seconds": p["seconds"], "destination": p.get("destinationName")}
        for v, p in sorted(soonest.items())
    ]


def route_rows(predictions, snapshot_at):
    """One row per route and direction: buses running and their predicted gaps."""
    buses, due = {}, {}
    for p in _timed(predictions, snapshot_at):
        key = (p["lineId"], p.get("direction"))
        buses.setdefault(key, set()).add(p["vehicleId"])
        if 0 <= p["seconds"] <= GAP_HORIZON_SECONDS:
            # One time per bus per stop (TfL can repeat a prediction).
            due.setdefault(key, {}).setdefault(p["naptanId"], {})[p["vehicleId"]] = p["seconds"]

    rows = []
    for key in sorted(buses, key=lambda k: (k[0], k[1] or "")):
        gaps = []
        for times in due.get(key, {}).values():
            ordered = sorted(times.values())
            gaps += [later - earlier for earlier, later in zip(ordered, ordered[1:])]
        typical = median(gaps) if gaps else None
        rows.append({
            "snapshot_at": snapshot_at, "line_id": key[0], "direction": key[1],
            "buses": len(buses[key]),
            "gaps": len(gaps),
            "median_gap_seconds": typical,
            "max_gap_seconds": max(gaps) if gaps else None,
            "bunched_gaps": sum(g < BUNCHED_SHARE * typical for g in gaps) if typical else 0,
        })
    return rows
