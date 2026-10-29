"""Live bikes panel: station rows and network totals from a real BikePoint response."""

import copy
import json

from src.presentation.bike_live import station_state, stations, summary
from tests.unit.helpers import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "bikepoint" / "bikepoint_20261029T165832Z.json"


def load():
    return json.loads(FIXTURE.read_text())


def set_prop(record, key, value):
    next(p for p in record["additionalProperties"] if p["key"] == key)["value"] = value


def test_every_real_station_is_read():
    rows = stations(load())
    assert len(rows) == 30
    assert all(r["standard_bikes"] + r["e_bikes"] == r["bikes"] for r in rows)
    assert rows == sorted(rows, key=lambda r: r["name"])


def test_names_are_tidied():
    rows = stations(load())
    assert not any(" ," in r["name"] for r in rows)


def test_states():
    assert station_state(0, 5) == "empty"
    assert station_state(4, 0) == "full"
    assert station_state(4, 5) == "ok"
    # No bikes and no free docks (all docks out of use): nothing to take is what matters.
    assert station_state(0, 0) == "empty"


def test_unreadable_station_is_skipped():
    data = copy.deepcopy(load())
    set_prop(data[0], "NbDocks", "")
    data[1]["lat"] = None
    assert len(stations(data)) == 28


def test_summary_totals():
    rows = stations(load())
    s = summary(rows)
    assert s["stations"] == 30
    assert s["bikes"] == sum(r["bikes"] for r in rows)
    assert s["out_of_use_docks"] == sum(r["docks"] - r["bikes"] - r["empty_docks"] for r in rows)
    assert 0 <= s["out_of_use_share"] < 1


def test_bad_body_gives_no_rows():
    assert stations({"message": "error"}) == []
    assert summary([])["out_of_use_share"] == 0.0
