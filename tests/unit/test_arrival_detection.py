from src.streaming.arrival_detection import (
    LineTracker, coverage, is_newer, next_visits, slim, snapshot_time, valid_vehicle,
)

CONFIG = {"arrival_window_seconds": 90, "at_platform_max_seconds": 60, "checkpoint_minutes": [10, 5, 2]}


def at(hhmmss):
    return f"2026-10-22T07:{hhmmss}Z"


def pred(vehicle, stop, ts, expected, location="Between A and B", line="victoria"):
    return {"vehicleId": vehicle, "naptanId": stop, "stationName": f"Station {stop}", "lineId": line,
            "platformName": "Northbound", "direction": "inbound", "destinationNaptanId": "Z",
            "currentLocation": location, "timestamp": at(ts), "expectedArrival": at(expected)}


def test_newer_snapshots_only():
    assert is_newer(at("25:05"), None)
    assert is_newer(at("25:33"), at("25:05"))
    assert not is_newer(at("25:03"), at("25:05"))     # an older cache answered
    assert not is_newer(at("25:05"), at("25:05"))
    assert snapshot_time([pred("1", "S", "25:03", "26:00"), pred("2", "S", "25:05", "27:00")]) == at("25:05")


def test_slim_drops_untrackable_and_far_predictions():
    records = [pred("234", "S", "00:00", "10:00"), pred("", "S", "00:00", "01:00"),
               pred("000", "S", "00:00", "01:00"), pred("235", "S", "00:00", "45:00")]
    assert [r["vehicleId"] for r in slim(records, 1800)] == ["234"]
    assert not valid_vehicle("") and not valid_vehicle("000") and valid_vehicle("040")


def test_earliest_prediction_is_the_current_visit():
    visits = next_visits([pred("236", "EC", "23:00", "29:25"), pred("236", "EC", "23:00", "23:16")])
    assert visits[("236", "EC")]["expectedArrival"] == at("23:16")


def test_arrival_at_platform():
    t = LineTracker("tube", "victoria", CONFIG)
    assert t.update(at("19:00"), [pred("1", "S", "19:00", "30:00"), pred("1", "T", "19:00", "32:00")]) == []
    t.update(at("25:00"), [pred("1", "S", "25:00", "29:50"), pred("1", "T", "25:00", "31:50")])
    t.update(at("29:40"), [pred("1", "S", "29:40", "29:50", "At Platform"), pred("1", "T", "29:40", "31:50")])
    events = t.update(at("30:20"), [pred("1", "T", "30:20", "31:50")])
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "arrival" and e["detection"] == "at_platform" and e["arrived_at"] == at("29:40")
    assert [c["horizon_minutes"] for c in e["checkpoints"]] == [10, 5, 2]
    # The 10- and 5-minute checkpoints are both the 07:25 prediction (crossed between sightings).
    assert e["checkpoints"][0]["predicted_at"] == at("25:00") and e["checkpoints"][0]["expected_arrival"] == at("29:50")


def test_arrival_without_platform_uses_expected_time():
    t = LineTracker("tram", "tram", CONFIG)
    t.update(at("28:00"), [pred("2553", "R", "28:00", "29:00"), pred("2553", "Q", "28:00", "33:00")])
    events = t.update(at("28:30"), [pred("2553", "Q", "28:30", "33:00")])
    assert events[0]["event"] == "arrival" and events[0]["detection"] == "disappeared"
    assert events[0]["arrived_at"] == at("29:00")


def test_replanned_and_vehicle_gone_are_not_arrivals():
    t = LineTracker("tube", "hammersmith-city", CONFIG)
    t.update(at("20:00"), [pred("430", "ALD", "20:00", "29:25"), pred("430", "KX", "20:00", "21:00"),
                           pred("431", "BAR", "20:00", "22:00")])
    events = t.update(at("20:30"), [pred("430", "KX", "20:30", "21:00")])
    reasons = sorted((e["event"], e.get("reason"), e["vehicle_id"]) for e in events)
    assert reasons == [("dropped", "replanned", "430"), ("dropped", "vehicle_gone", "431")]


def test_terminus_reversal_counts_two_visits():
    t = LineTracker("tube", "bakerloo", CONFIG)
    t.update(at("22:50"), [pred("236", "EC", "22:50", "23:16", "At Platform"), pred("236", "X", "22:50", "35:00")])
    # Arrived; the same train is now predicted at the same stop for its departure north.
    events = t.update(at("23:30"), [pred("236", "EC", "23:30", "29:25"), pred("236", "X", "23:30", "35:00")])
    assert [e["event"] for e in events] == ["arrival"]
    assert events[0]["arrived_at"] == at("22:50")
    assert t.visits[("236", "EC")]["last"]["expectedArrival"] == at("29:25")


def test_checkpoints_need_an_observed_crossing():
    t = LineTracker("tube", "victoria", CONFIG)
    t.update(at("26:00"), [pred("1", "S", "26:00", "30:00"), pred("1", "T", "26:00", "35:00")])   # first seen 4 min out
    t.update(at("28:30"), [pred("1", "S", "28:30", "30:00"), pred("1", "T", "28:30", "35:00")])   # crosses 2 min
    events = t.update(at("30:30"), [pred("1", "T", "30:30", "35:00")])
    assert [c["horizon_minutes"] for c in events[0]["checkpoints"]] == [2]


def test_coverage_row_per_snapshot():
    t = LineTracker("tube", "victoria", CONFIG)
    t.update(at("28:00"), [pred("1", "S", "28:00", "29:00"), pred("1", "T", "28:00", "33:00"),
                           pred("2", "U", "28:00", "40:00")])
    records = [pred("1", "T", "28:40", "33:00")]
    events = t.update(at("28:40"), records)
    row = coverage(t, at("28:40"), len(records), events)
    assert row == {"event": "snapshot", "mode": "tube", "line_id": "victoria", "snapshot_at": at("28:40"),
                   "predictions": 1, "visits_tracked": 1, "arrivals": 1, "dropped": 1}
