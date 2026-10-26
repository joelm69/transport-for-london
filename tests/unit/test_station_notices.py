import copy
import json

import pytest

from src.presentation.station_notices import (
    message_body, notice_items, notice_summary, short_station_name, station_cards,
)
from tests.unit.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "stop_disruptions"


@pytest.fixture
def pattern():
    return json.loads((ROOT / "config" / "stop_disruptions.json").read_text())["accessibility_pattern"]


@pytest.fixture
def data():
    return json.loads((FIXTURES / "stop_disruptions_20261021T205935Z.json").read_text())


def test_same_message_is_shown_once_per_station(data, pattern):
    """Real data: 63 records -> 55 distinct (code, message) pairs -> fewer display items,
    because one DLR message repeats with a trailing line break and interchanges such as
    Willesden Junction carry the same message under a rail and an Underground code."""
    items = notice_items(data, pattern)
    assert len(items) < 54
    willesden = [i for i in items if i["station"] == "Willesden Junction"]
    assert len(willesden) == 1
    assert willesden[0]["types"] == ["Information", "Interchange Message"]
    assert len(willesden[0]["atco_codes"]) == 2 and len(willesden[0]["modes"]) == 2


def test_message_repeated_with_a_different_ending_is_shown_once(pattern):
    text = "Hainault: No Step Free Access - No step free exit to street level due to a faulty lift."
    record = {"commonName": "Hainault Underground Station", "mode": "tube", "type": "Information",
              "appearance": "RealTime", "fromDate": "2026-10-23T06:00:00Z", "atcoCode": "940GZZLUHLT"}
    data = [{**record, "description": text[:-1]}, {**record, "description": "  " + text.replace(" - ", "  -  ")}]
    items = notice_items(data, pattern)
    assert len(items) == 1
    assert items[0]["description"].endswith("faulty lift.")


def _record(station, description, since="2026-10-23T06:00:00Z", mode="tube"):
    return {"commonName": station, "mode": mode, "type": "Information", "appearance": "RealTime",
            "fromDate": since, "atcoCode": station[:8], "description": description}


def test_heading_variants_of_one_message_are_shown_once(pattern):
    """Hounslow East, 2026-10-23: the same notice with two different TfL headings."""
    tail = "Step free access is not available to the westbound platform due to a faulty lift."
    data = [_record("Hounslow East Underground Station", "Hounslow East: No Step Free Access - " + tail),
            _record("Hounslow East Underground Station", "Hounslow East - " + tail, since="2026-10-23T05:00:00Z")]
    items = notice_items(data, pattern)
    assert len(items) == 1
    assert items[0]["since"].hour == 5          # the earlier start is kept


def test_different_messages_at_one_station_stay_separate(pattern):
    data = [_record("Canary Wharf", "Canary Wharf: No Step Free Access - Step free access is not available to the DLR platforms."),
            _record("Canary Wharf", "Canary Wharf: No Step Free Access - Step free access is not available to the Jubilee line.")]
    assert len(notice_items(data, pattern)) == 2


@pytest.mark.parametrize("description, station, body", [
    ("PIMLICO STATION: No down escalator.", "Pimlico", "No down escalator."),
    ("BURNHAM STATION: Please travel in the front 8 coaches.", "Burnham (Berks)", "Please travel in the front 8 coaches."),
    ("ELEPHANT & CASTLE UNDERGROUND STATION: Lifts out of order.", "Elephant & Castle", "Lifts out of order."),
    ("Hounslow East - step free access is not available.", "Hounslow East", "Step free access is not available."),
    ("Please note: the lift is closed.", "Bank", "Please note: the lift is closed."),   # not a station heading
])
def test_message_body_drops_only_the_station_heading(description, station, body):
    assert message_body(description, station) == body


def test_station_cards_one_per_station_and_shared_messages_combined(pattern):
    coaches = "Please travel in the front 8 coaches if alighting at this station."
    data = [_record("Burnham (Berks)", "BURNHAM STATION: " + coaches, mode="elizabeth-line"),
            _record("Iver", coaches, mode="elizabeth-line"),
            _record("Oval", "Oval: no down escalator due to a fault."),
            _record("Oval", "Until mid December, Oval will have an up escalator only.")]
    cards = station_cards(notice_items(data, pattern))
    titles = sorted(card["title"] for card in cards)
    assert titles == ["Burnham (Berks) · Iver", "Oval"]
    oval = next(card for card in cards if card["title"] == "Oval")
    assert len(oval["messages"]) == 2
    burnham = next(card for card in cards if card["title"] != "Oval")
    assert [m["text"] for m in burnham["messages"]] == [coaches]


def test_a_closure_posted_at_many_stations_gets_a_short_title(pattern):
    """Suffragette line, 2026-10-23: one closure notice posted at 9 stations."""
    text = ("Suffragette Line: No service between South Tottenham and Barking Riverside due to an "
            "operational incident. GOOD SERVICE on the rest of the line.")
    stations = ["Barking", "Barking Riverside", "Blackhorse Road", "South Tottenham", "Wanstead Park"]
    data = [_record(name, text, mode="overground") for name in stations]
    [card] = station_cards(notice_items(data, pattern))
    assert card["title"] == "Suffragette line · 5 stations"
    assert card["station_list"] == ", ".join(stations)
    few = station_cards(notice_items([_record("Bank", "Escalator closed."), _record("Monument", "Escalator closed.")], pattern))
    assert few[0]["title"] == "Bank · Monument" and few[0]["station_list"] is None


def test_many_stations_without_a_line_name_are_counted(pattern):
    data = [_record(f"Station {n}", "Please travel in the front 7 coaches.") for n in "ABCD"]
    assert station_cards(notice_items(data, pattern))[0]["title"] == "4 stations"


def test_live_feed_cards_never_repeat_a_station(data, pattern):
    items = notice_items(data, pattern)
    for section in ([i for i in items if i["categories"]["closure"]],
                    [i for i in items if not i["categories"]["closure"]]):
        stations = [s.lower() for card in station_cards(section) for s in card["stations"]]
        assert len(stations) == len(set(stations))


def test_whole_station_closure_counts_as_a_closure(pattern):
    """2026-10-26: TfL's first 'Closure' (a whole station) next to the usual 'Part Closure'."""
    data = [{**_record("Shadwell", "Shadwell: station closed all day."), "type": "Closure",
             "appearance": "PlannedWork"}]
    [item] = notice_items(data, pattern)
    assert item["categories"]["closure"]


def test_closures_come_first(data, pattern):
    items = notice_items(data, pattern)
    closures = [i for i in items if i["categories"]["closure"]]
    assert closures and items[: len(closures)] == closures


def test_summary_counts(data, pattern):
    items = notice_items(data, pattern)
    counts = notice_summary(items)
    assert counts["notices"] == len(items)
    assert counts["stations"] <= counts["notices"]
    assert counts["closure"] >= 1 and counts["accessibility"] > counts["closure"]


def test_planned_until_only_for_planned_work(data, pattern):
    for item in notice_items(data, pattern):
        if item["planned_until"] is not None:
            assert item["categories"]["planned"]


def test_mode_filter(data, pattern):
    items = notice_items(data, pattern, modes={"dlr"})
    assert items and all(i["modes"] == ["dlr"] for i in items)


def test_skips_records_without_description(data, pattern):
    broken = copy.deepcopy(data)
    broken[0]["description"] = ""
    assert len(notice_items(broken, pattern)) <= len(notice_items(data, pattern))


@pytest.mark.parametrize("name, short", [
    ("Barons Court Underground Station", "Barons Court"),
    ("Willesden Junction Rail Station", "Willesden Junction"),
    ("Deptford Bridge DLR Station", "Deptford Bridge"),
    ("Abbey Wood", "Abbey Wood"),
])
def test_short_station_name(name, short):
    assert short_station_name(name) == short
