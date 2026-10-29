"""Live roads panel: disruption rows, corridor status and totals from real TfL responses."""

import copy
import json

from src.presentation.road_live import corridors, disruptions, summary
from tests.unit.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "road"


def load(kind):
    return json.loads((FIXTURES / f"road_{kind}_20261029T181808Z.json").read_text())


def test_every_real_disruption_is_located():
    rows = disruptions(load("disruption"))
    assert len(rows) == 29
    assert sum(r["is_incident"] for r in rows) == 9
    assert all(51.2 < r["lat"] < 51.8 and -0.6 < r["lon"] < 0.4 for r in rows)


def test_incidents_come_first_worst_first():
    rows = disruptions(load("disruption"))
    flags = [r["is_incident"] for r in rows]
    assert flags == sorted(flags, reverse=True)
    incident_weights = [r["weight"] for r in rows if r["is_incident"]]
    assert incident_weights == sorted(incident_weights, reverse=True)


def test_closed_streets_are_read_from_the_streets_not_the_flag():
    data = load("disruption")
    assert not any(r["hasClosures"] for r in data)
    assert sum(bool(r["closed_streets"]) for r in disruptions(data)) == 7


def test_record_without_a_point_is_skipped():
    data = copy.deepcopy(load("disruption"))
    data[0]["point"] = None
    data[1]["point"] = "not json"
    assert len(disruptions(data)) == 27


def test_corridors_worst_first():
    rows = corridors(load("status"))
    assert len(rows) == 24
    goods = [r["good"] for r in rows]
    assert goods == sorted(goods)


def test_summary():
    d, c = disruptions(load("disruption")), corridors(load("status"))
    s = summary(d, c)
    assert s["disruptions"] == 29 and s["incidents"] == 9 and s["works"] == 20
    assert s["corridors"] == 24
    assert s["corridors_delayed"] == sum(not r["good"] for r in c)


def test_bad_bodies_give_no_rows():
    assert disruptions({"message": "x"}) == [] and corridors(None) == []
