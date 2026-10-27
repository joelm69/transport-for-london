from datetime import datetime, timedelta

from src.collectors.bus_network import median_wait_seconds, route_rows, slim, vehicle_rows

AT = "2026-10-23T18:00:00Z"


def pred(vehicle, stop, seconds, line="38", direction="outbound", **extra):
    """A prediction as /Mode/bus/Arrivals gives it: expectedArrival, no timeToStation."""
    expected = datetime(2026, 10, 23, 18, 0) + timedelta(seconds=seconds)
    return {"vehicleId": vehicle, "lineId": line, "direction": direction, "naptanId": stop,
            "stationName": f"Stop {stop}", "destinationName": "Victoria",
            "expectedArrival": expected.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"), "bearing": "90", **extra}


def test_slim_keeps_only_the_analysis_fields():
    [row] = slim([pred("1", "A", 60, tripId="x", platformName="K")])
    assert set(row) == {"vehicleId", "lineId", "direction", "naptanId", "stationName",
                        "expectedArrival", "destinationName"}


def test_vehicle_next_stop_is_its_soonest_prediction():
    rows = vehicle_rows([pred("1", "B", 300), pred("1", "A", 60), pred("2", "C", 30, line="73")], AT)
    assert [(r["vehicle_id"], r["line_id"], r["next_naptan_id"], r["eta_seconds"]) for r in rows] == [
        ("1", "38", "A", 60), ("2", "73", "C", 30)]


def test_route_gaps_and_bunching():
    # Stop A: buses due at 1, 2 and 11 min -> gaps 60 s and 540 s; stop B: 4 and 13 min -> 540 s.
    data = [pred("1", "A", 60), pred("2", "A", 120), pred("3", "A", 660),
            pred("1", "B", 240), pred("3", "B", 780),
            pred("4", "A", 3000),                       # beyond 30 minutes: counted as running, not in gaps
            pred("9", "A", 90, direction="inbound")]    # other direction: its own row
    rows = {(r["line_id"], r["direction"]): r for r in route_rows(data, AT)}
    out = rows[("38", "outbound")]
    assert out["buses"] == 4 and out["gaps"] == 3
    assert out["median_gap_seconds"] == 540 and out["max_gap_seconds"] == 540
    assert out["bunched_gaps"] == 1                     # 60 s < half of 540 s
    # Waits for passengers arriving at random over gaps of 60, 540 and 540 s.
    assert out["gap_seconds_sum"] == 1140 and out["gap_seconds_sq_sum"] == 60**2 + 2 * 540**2
    assert round(out["gap_seconds_sq_sum"] / (2 * out["gap_seconds_sum"]), 1) == 257.4   # average wait
    assert out["median_wait_seconds"] == 255
    assert rows[("38", "inbound")]["gaps"] == 0 and rows[("38", "inbound")]["median_gap_seconds"] is None


def test_repeated_and_incomplete_predictions_are_ignored():
    data = [pred("1", "A", 60), pred("1", "A", 60), {"vehicleId": "2"}, "not a dict"]
    assert len(slim(data)) == 3
    [row] = route_rows([p for p in data if isinstance(p, dict)], AT)
    assert row["buses"] == 1 and row["gaps"] == 0


def test_median_wait():
    assert median_wait_seconds([600]) == 300                 # even service: half the gap
    assert median_wait_seconds([600, 600]) == 300
    # Bunched: 2 and 18 minutes, the same buses per hour as every 10. The long gap catches 90% of
    # arrivals, so half of passengers wait over 8 minutes instead of 5 - the cost of bunching.
    assert median_wait_seconds([120, 1080]) == 480
    assert median_wait_seconds([]) is None
