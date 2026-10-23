from datetime import datetime, timezone

from src.presentation.bus_live import bus_status_items, buses_on_route, route_stops, stop_countdown

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)


def at(mmss):
    return f"2026-10-22T18:{mmss}Z"


def pred(vehicle, stop, eta, direction="outbound"):
    return {"vehicleId": vehicle, "naptanId": stop, "direction": direction, "expectedArrival": at(eta),
            "lineName": "38"}


STOPS = [("A", "Clapton Pond"), ("B", "Hackney"), ("C", "Angel"), ("D", "Holborn"), ("E", "Victoria")]


def test_status_lists_only_problems_in_route_order():
    data = [
        {"id": "n29", "name": "N29", "lineStatuses": [{"statusSeverity": 0, "statusSeverityDescription": "Special Service", "reason": "Diverted "}]},
        {"id": "149", "name": "149", "lineStatuses": [{"statusSeverity": 0, "statusSeverityDescription": "Special Service", "reason": "Roadworks"}]},
        {"id": "38", "name": "38", "lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]},
        {"id": "12", "name": "12", "lineStatuses": [{"statusSeverity": 0, "statusSeverityDescription": "Special Service"}]},
    ]
    assert [i["route"] for i in bus_status_items(data)] == ["12", "149", "N29"]
    assert bus_status_items(data)[2]["reason"] == "Diverted"


def test_route_stops_uses_the_longest_branch():
    sequence = {"stopPointSequences": [
        {"stopPoint": [{"id": "A", "name": "Clapton Pond"}, {"id": "B", "name": "Hackney"}]},
        {"stopPoint": [{"id": s, "name": n} for s, n in STOPS]},
    ]}
    assert route_stops(sequence) == STOPS


def test_positions_gaps_and_bunching():
    arrivals = [
        # Bus 1 leading: next stop D in 1 min, then E in 4 min.
        pred("1", "D", "01:00"), pred("1", "E", "04:00"),
        # Bus 2 close behind: next stop C in 1 min, D in 2 min -> 1 min behind bus 1 at D.
        pred("2", "C", "01:00"), pred("2", "D", "02:00"), pred("2", "E", "05:00"),
        # Bus 3 far behind: next stop A in 1 min, C in 10 min -> 9 min behind bus 2 at C.
        pred("3", "A", "01:00"), pred("3", "B", "05:00"), pred("3", "C", "10:00"),
        # A bus going the other way, and a prediction for a stop off the route, are ignored.
        pred("9", "B", "01:00", direction="inbound"), pred("4", "Z", "01:00"),
    ]
    rows = buses_on_route(arrivals, {"outbound": STOPS}, NOW)
    assert [r["vehicle_id"] for r in rows] == ["1", "2", "3"]          # leading bus first
    gaps = {r["vehicle_id"]: r["gap_seconds"] for r in rows}
    assert gaps == {"1": None, "2": 60.0, "3": 540.0}
    # Typical gap = median(60, 540) = 300 s; bus 2's 60 s gap is under half of it.
    assert {r["vehicle_id"]: r["bunched"] for r in rows} == {"1": False, "2": True, "3": False}
    bus1 = rows[0]
    assert bus1["next_stop"] == "Holborn" and bus1["eta_seconds"] == 60 and 2 < bus1["position"] < 3
    assert bus1["route"] == "38"
    assert bus1["expected_arrival"] == datetime(2026, 10, 22, 18, 1, tzinfo=timezone.utc)


def test_stop_countdown_soonest_first():
    arrivals = [
        {"lineName": "56", "destinationName": "Smithfield", "expectedArrival": at("02:30")},
        {"lineName": "38", "destinationName": "Victoria", "expectedArrival": at("00:40")},
        {"lineName": "30", "destinationName": "Marble Arch", "expectedArrival": at("11:00")},
        {"lineName": "38", "destinationName": "Victoria", "expectedArrival": "2026-10-22T17:58:00Z"},  # gone
    ]
    assert [(r["route"], r["minutes"]) for r in stop_countdown(arrivals, NOW)] == [("38", 0), ("56", 2), ("30", 11)]


def test_overtaking_counts_as_zero_gap_and_contradictions_are_ignored():
    arrivals = [
        pred("1", "D", "02:00"), pred("1", "E", "05:00"),
        pred("2", "C", "00:30"), pred("2", "D", "01:30"),          # 30 s "ahead" of bus 1 at D: alongside
        pred("3", "B", "15:00"), pred("3", "C", "20:00"),          # far behind: normal gap at C
        # Bus 4 is behind bus 3 on the route but predicted at B 14 minutes before it: contradiction.
        pred("4", "A", "00:30"), pred("4", "B", "01:00"),
    ]
    rows = {r["vehicle_id"]: r for r in buses_on_route(arrivals, {"outbound": STOPS}, NOW)}
    assert rows["2"]["gap_seconds"] == 0.0 and rows["2"]["bunched"]
    assert rows["3"]["gap_seconds"] == 1170.0
    assert rows["4"]["gap_seconds"] is None and not rows["4"]["bunched"]
