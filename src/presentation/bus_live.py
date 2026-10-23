"""Turn live TfL bus responses into rows for a live bus panel: route status, every bus on a
route with the time gap to the bus ahead (bunching now), and a stop countdown.

Pure functions (no Streamlit, no network), shared by the portfolio and future MCP tools.
Live only: nothing here is stored. Bus history is analysed daily from the VM's arrival events.

Positions: a bus's earliest prediction is its next stop, so it sits just before that stop in
the route's stop order. Gaps: the time between two consecutive buses is the difference of
their predicted times at the stop the leading bus reaches next (both are predicted there).
"""

from datetime import datetime, timedelta
from statistics import median

GOOD_SERVICE = 10
BUNCHED_SHARE = 0.5        # a gap under half the route's median gap counts as bunched
SECONDS_PER_STOP = 90      # rough spacing used only to place a bus between two stops
OVERTAKE_SECONDS = 60      # a bus up to this far "ahead" of the one in front is alongside it


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _route_sort_key(route_id):
    """Route numbers in numeric order: 3, 12, 25, 149, then lettered routes (N29, SL1)."""
    digits = "".join(ch for ch in route_id if ch.isdigit())
    return (not route_id[:1].isdigit(), int(digits) if digits else 0, route_id)


def bus_status_items(data):
    """Routes not running a good service, in route order. TfL marks diversions and planned
    roadworks as 'Special Service', so most items are planned, not breakdowns."""
    items = []
    for line in data:
        for status in line.get("lineStatuses", []):
            if status.get("statusSeverity") == GOOD_SERVICE:
                continue
            items.append({
                "route": line.get("name") or line["id"],
                "route_id": line["id"],
                "status": status.get("statusSeverityDescription"),
                "reason": (status.get("reason") or "").strip(),
            })
    return sorted(items, key=lambda item: _route_sort_key(item["route_id"]))


def route_stops(sequence):
    """Ordered (naptan_id, name) for one direction of a route, from /Line/{id}/Route/Sequence.
    Uses the longest stop sequence when a route has branches."""
    sequences = sequence.get("stopPointSequences") or []
    if not sequences:
        return []
    longest = max(sequences, key=lambda s: len(s.get("stopPoint") or []))
    return [(stop["id"], stop.get("name")) for stop in longest["stopPoint"]]


def buses_on_route(arrivals, stops_by_direction, now):
    """One row per bus on the route: direction, next stop, ETA and expected arrival time there,
    position along the route, gap to the bus ahead and whether it is bunched.

    arrivals: /Line/{route}/Arrivals. stops_by_direction: {"inbound": route_stops(...), ...}.
    """
    order = {direction: {stop_id: index for index, (stop_id, _) in enumerate(stops)}
             for direction, stops in stops_by_direction.items()}
    names = {direction: dict(stops) for direction, stops in stops_by_direction.items()}

    # Every prediction per (direction, vehicle), and each vehicle's next stop.
    predictions = {}
    for item in arrivals:
        vehicle, direction = item.get("vehicleId"), item.get("direction")
        if not vehicle or direction not in order or item.get("naptanId") not in order[direction]:
            continue
        eta = (_utc(item["expectedArrival"]) - now).total_seconds()
        predictions.setdefault((direction, vehicle), {})[item["naptanId"]] = eta

    rows = []
    for (direction, vehicle), etas in predictions.items():
        next_stop = min(etas, key=etas.get)
        index = order[direction][next_stop]
        eta = max(0.0, etas[next_stop])
        rows.append({
            "direction": direction, "vehicle_id": vehicle,
            "next_stop_id": next_stop, "next_stop": names[direction].get(next_stop),
            "eta_seconds": eta,
            "expected_arrival": now + timedelta(seconds=etas[next_stop]),   # UTC, as TfL predicts it
            # Just before the next stop: a bus 90 s away sits about one stop back.
            "position": max(0.0, index - min(eta / SECONDS_PER_STOP, 0.95)),
            "_etas": etas,
        })

    for direction in stops_by_direction:
        on_route = sorted((r for r in rows if r["direction"] == direction), key=lambda r: -r["position"])
        for ahead, behind in zip(on_route, on_route[1:]):
            # Both buses are predicted at the stop the leading bus reaches next.
            shared = ahead["next_stop_id"]
            if shared in behind["_etas"]:
                gap = behind["_etas"][shared] - ahead["_etas"][shared]
                # Slightly negative: the two buses are together (one overtaking): a gap of zero.
                # Far negative: predictions that contradict the route order (a bus on a stand or
                # a short working, seen on route 38 at -14 min): not a usable gap.
                if gap >= -OVERTAKE_SECONDS:
                    behind["gap_seconds"] = max(0.0, gap)
        gaps = [r["gap_seconds"] for r in on_route if r.get("gap_seconds") is not None]
        typical = median(gaps) if gaps else None
        for row in on_route:
            gap = row.get("gap_seconds")
            row["typical_gap_seconds"] = typical
            row["bunched"] = bool(typical and gap is not None and gap < BUNCHED_SHARE * typical)

    for row in rows:
        row.setdefault("gap_seconds", None)
        del row["_etas"]
    return sorted(rows, key=lambda r: (r["direction"], -r["position"]))


def stop_countdown(arrivals, now, limit=12):
    """Next buses at a stop (/StopPoint/{id}/Arrivals), soonest first: route, destination, minutes."""
    rows = []
    for item in arrivals:
        seconds = (_utc(item["expectedArrival"]) - now).total_seconds()
        if seconds < -30:
            continue
        rows.append({
            "route": item.get("lineName") or item.get("lineId"),
            "destination": item.get("destinationName") or item.get("towards"),
            "minutes": max(0, int(seconds // 60)),
            "seconds": seconds,
        })
    return sorted(rows, key=lambda r: r["seconds"])[:limit]
