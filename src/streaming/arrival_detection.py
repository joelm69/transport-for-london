"""Arrival detection from TfL arrival predictions. Pure functions and one state class:
no Kafka, no network, so every rule is unit-testable.

Design: docs/endpoints/arrivals.md, section 2.

Flow per accepted snapshot of one line:
  slim()        keep predictions within the horizon, with the fields we use
  next_visits() the earliest prediction per (vehicle, stop): the visit currently predicted
  LineTracker   compares visits with the previous snapshot of the same line and emits
                arrival and dropped events, with prediction checkpoints for accuracy analysis
"""

from datetime import datetime

FIELDS = ("vehicleId", "naptanId", "stationName", "lineId", "platformName", "direction",
          "destinationNaptanId", "currentLocation", "timestamp", "expectedArrival")


def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def snapshot_time(records):
    """TfL's time for a response: the latest prediction `timestamp` in it."""
    return max((r["timestamp"] for r in records if r.get("timestamp")), default=None)


def is_newer(snapshot, last_accepted):
    """TfL serves this endpoint from several caches; a response can be older than one already
    accepted. Only strictly newer snapshots are used."""
    return snapshot is not None and (last_accepted is None or snapshot > last_accepted)


def valid_vehicle(vehicle_id):
    """Blank IDs (all DLR) and all-zero placeholders such as "000" cannot be tracked."""
    return bool(vehicle_id) and vehicle_id.strip("0") != ""


def slim(records, horizon_seconds):
    """Predictions within the horizon of TfL's timestamp, trackable vehicles only, compact."""
    kept = []
    for r in records:
        if not valid_vehicle(r.get("vehicleId")) or not r.get("expectedArrival") or not r.get("timestamp"):
            continue
        lead = (parse_utc(r["expectedArrival"]) - parse_utc(r["timestamp"])).total_seconds()
        if lead <= horizon_seconds:
            kept.append({k: r.get(k) for k in FIELDS})
    return kept


def by_line(records):
    lines = {}
    for r in records:
        lines.setdefault(r["lineId"], []).append(r)
    return lines


def next_visits(records):
    """The earliest prediction per (vehicle, stop). A train can be predicted at the same stop
    twice (arriving at a terminus, then departing the other way); the earlier one is the visit
    being tracked now."""
    visits = {}
    for r in records:
        key = (r["vehicleId"], r["naptanId"])
        if key not in visits or r["expectedArrival"] < visits[key]["expectedArrival"]:
            visits[key] = r
    return visits


class LineTracker:
    """Tracks the visits of one line across its accepted snapshots."""

    def __init__(self, mode, line_id, config):
        self.mode = mode
        self.line_id = line_id
        self.window = config["arrival_window_seconds"]
        self.at_platform_max = config["at_platform_max_seconds"]
        self.checkpoints = sorted(config["checkpoint_minutes"], reverse=True)
        self.visits = {}          # (vehicle, stop) -> state
        self.snapshot = None      # time of the previous accepted snapshot

    def update(self, snapshot, records):
        """Process one accepted snapshot of this line; return arrival/dropped events."""
        now = parse_utc(snapshot)
        current = next_visits(records)
        vehicles = {vehicle for vehicle, _ in current}
        events = []

        for key, state in list(self.visits.items()):
            new = current.get(key)
            # Same visit still predicted, unless the prediction jumped well past the old one
            # (the train has called and is now predicted for its next visit to this stop).
            if new is not None and not self._jumped(state, new):
                continue
            events.append(self._close(state, vehicles))
            del self.visits[key]

        for key, r in current.items():
            state = self.visits.get(key)
            if state is None:
                state = self.visits[key] = {"first": r, "checkpoints": {}, "at_platform": None}
            self._observe(state, r)

        self.snapshot = now
        return events

    def _jumped(self, state, new):
        last = parse_utc(state["last"]["expectedArrival"])
        return (parse_utc(new["expectedArrival"]) - last).total_seconds() > 2 * self.window \
            and self._lead(state["last"]) <= self.window

    def _lead(self, r):
        return (parse_utc(r["expectedArrival"]) - parse_utc(r["timestamp"])).total_seconds()

    def _observe(self, state, r):
        previous_lead = self._lead(state["last"]) if "last" in state else None
        state["last"] = r
        lead = self._lead(r)
        for minutes in self.checkpoints:
            # Only a crossing we observed: a visit first seen 4 minutes out has no 10- or
            # 5-minute checkpoint (otherwise it would look perfectly predicted).
            crossed = previous_lead is not None and previous_lead > minutes * 60 >= lead
            if minutes not in state["checkpoints"] and crossed:
                state["checkpoints"][minutes] = {"horizon_minutes": minutes,
                                                 "predicted_at": r["timestamp"],
                                                 "expected_arrival": r["expectedArrival"]}
        at_platform = (r.get("currentLocation") or "").lower().startswith("at platform")
        if at_platform and lead <= self.at_platform_max and state["at_platform"] is None:
            state["at_platform"] = r["timestamp"]

    def _close(self, state, vehicles):
        r = state["last"]
        vehicle, stop = r["vehicleId"], r["naptanId"]
        base = {
            "mode": self.mode, "line_id": self.line_id, "vehicle_id": vehicle, "naptan_id": stop,
            "station_name": r.get("stationName"), "platform_name": r.get("platformName"),
            "direction": r.get("direction"), "destination_naptan_id": r.get("destinationNaptanId"),
            "last_snapshot_at": r["timestamp"], "last_expected_arrival": r["expectedArrival"],
        }
        if vehicle not in vehicles:
            return {**base, "event": "dropped", "reason": "vehicle_gone"}
        if self._lead(r) > self.window:
            return {**base, "event": "dropped", "reason": "replanned"}

        if state["at_platform"]:
            arrived_at, detection = state["at_platform"], "at_platform"
        else:
            arrived_at, detection = r["expectedArrival"], "disappeared"
        return {
            **base, "event": "arrival", "arrived_at": arrived_at, "detection": detection,
            "visit_key": f"{self.line_id}|{vehicle}|{stop}|{arrived_at}",
            "checkpoints": [state["checkpoints"][m] for m in self.checkpoints if m in state["checkpoints"]],
        }
