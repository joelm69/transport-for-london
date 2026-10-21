import copy
import json

import pytest

from src.presentation.station_notices import notice_items, notice_summary, short_station_name
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


def test_part_closures_come_first(data, pattern):
    items = notice_items(data, pattern)
    closures = [i for i in items if i["categories"]["part_closure"]]
    assert closures and items[: len(closures)] == closures


def test_summary_counts(data, pattern):
    items = notice_items(data, pattern)
    counts = notice_summary(items)
    assert counts["notices"] == len(items)
    assert counts["stations"] <= counts["notices"]
    assert counts["part_closure"] >= 1 and counts["accessibility"] > counts["part_closure"]


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
