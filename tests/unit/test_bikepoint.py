"""BikePoint: validation, change detection and the collector (raw kept hourly, changes every poll)."""

import copy
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.clients.tfl import TflHttpError, TflResponse
from src.collectors.bikepoint import BikePointCollector
from src.collectors.runner import make_writers
from src.deduplication.bikepoint import detect_changes, normalise
from src.storage.lake import LocalLake
from src.validation.bikepoint import ERROR, EXCLUDED, WARN, validate_poll
from tests.unit.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "bikepoint"
FIRST_AT = datetime(2026, 10, 29, 16, 58, 32, tzinfo=timezone.utc)
SECOND_AT = datetime(2026, 10, 29, 17, 0, 36, tzinfo=timezone.utc)
POLL = {"poll_id": "p", "collected_at_utc": FIRST_AT.isoformat(), "collection_window": "test"}


@pytest.fixture
def cfg():
    # The fixtures hold 30 stations, not the ~800 of a full response.
    return {**json.loads((ROOT / "config" / "bikepoint.json").read_text()), "min_stations": 30}


@pytest.fixture
def first():
    return json.loads((FIXTURES / "bikepoint_20261029T165832Z.json").read_text())


@pytest.fixture
def second():
    return json.loads((FIXTURES / "bikepoint_20261029T170036Z.json").read_text())


def rules(issues, level):
    return sorted({i.rule for i in issues if i.level == level})


def set_prop(record, key, value):
    next(p for p in record["additionalProperties"] if p["key"] == key)["value"] = value


# --- Validation ----------------------------------------------------------------------

def test_real_polls_are_clean(cfg, first, second):
    assert validate_poll(first, cfg, FIRST_AT) == []
    assert validate_poll(second, cfg, SECOND_AT) == []


def test_not_a_list_is_an_error(cfg):
    assert rules(validate_poll({"message": "x"}, cfg, FIRST_AT), ERROR) == ["P1"]


def test_too_few_stations_warns(cfg, first):
    assert "P2" in rules(validate_poll(first[:5], cfg, FIRST_AT), WARN)


def test_repeated_station_is_an_error(cfg, first):
    assert "P3" in rules(validate_poll(first + [first[0]], cfg, FIRST_AT), ERROR)


def test_unreadable_count_excludes_only_that_station(cfg, first):
    data = copy.deepcopy(first)
    set_prop(data[0], "NbBikes", "")
    issues = validate_poll(data, cfg, FIRST_AT)
    assert rules(issues, EXCLUDED) == ["B1"] and rules(issues, ERROR) == []
    assert len(normalise(data, POLL, cfg, issues)) == len(first) - 1


@pytest.mark.parametrize("key, value, rule", [
    ("NbEBikes", "99", "B2"),
    ("NbEmptyDocks", "999", "B3"),
])
def test_count_warnings(cfg, first, key, value, rule):
    data = copy.deepcopy(first)
    set_prop(data[0], key, value)
    assert rule in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_location_outside_london_warns(cfg, first):
    data = copy.deepcopy(first)
    data[0]["lat"] = 0.0
    assert "B4" in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_future_modified_warns(cfg, first):
    data = copy.deepcopy(first)
    for prop in data[0]["additionalProperties"]:
        prop["modified"] = "2026-10-30T09:00:00Z"
    assert "B5" in rules(validate_poll(data, cfg, FIRST_AT), WARN)


# --- Normalising and change detection ----------------------------------------------

def test_normalise_reads_counts_and_out_of_use_docks(cfg, first):
    records = normalise(first, POLL, cfg)
    assert len(records) == 30
    for r in records:
        assert r["standard_bikes"] + r["e_bikes"] == r["bikes"]
        assert r["out_of_use_docks"] == r["docks"] - r["bikes"] - r["empty_docks"]


def test_first_poll_is_a_baseline(cfg, first):
    changes, state = detect_changes({}, normalise(first, POLL, cfg), True, POLL)
    assert len(changes) == len(state) == 30
    assert {c["change_type"] for c in changes} == {"BASELINE"}


def test_only_changed_stations_are_written(cfg, first, second):
    _, state = detect_changes({}, normalise(first, POLL, cfg), True, POLL)
    changes, _ = detect_changes(state, normalise(second, POLL, cfg), False, POLL)
    # Five of the 30 stations changed counts in the two minutes between the real polls.
    assert len(changes) == 5
    assert {c["change_type"] for c in changes} == {"UPDATE"}


def test_new_and_removed_stations(cfg, first):
    _, state = detect_changes({}, normalise(first[1:], POLL, cfg), True, POLL)
    changes, _ = detect_changes(state, normalise(first[:-1], POLL, cfg), False, POLL)
    kinds = {c["station_id"]: c["change_type"] for c in changes}
    assert kinds == {first[0]["id"]: "NEW_STATION", first[-1]["id"]: "REMOVED"}


def test_same_poll_twice_has_no_changes(cfg, first):
    _, state = detect_changes({}, normalise(first, POLL, cfg), True, POLL)
    shuffled = list(reversed(first))
    assert detect_changes(state, normalise(shuffled, POLL, cfg), False, POLL)[0] == []


# --- Collector ---------------------------------------------------------------------

class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def get_json(self, path, params=None):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return TflResponse(data=item, status=200, path=path, params=params or {}, attempts=1,
                           latency_ms=5, size_bytes=1, headers={}, cache_max_age=30,
                           fetched_at_utc=FIRST_AT.isoformat())


def read_zone(root, zone):
    rows = []
    for path in sorted((root / zone).rglob("*.jsonl.gz")):
        with gzip.open(path, "rt") as handle:
            rows.extend(json.loads(line) for line in handle)
    return rows


def run_polls(tmp_path, cfg, responses, times):
    lake = LocalLake(tmp_path / "lake")
    writers = make_writers(lake, tmp_path / "tmp", cfg["dataset"], cfg["changes_name"])
    clock = iter(times)
    collector = BikePointCollector(FakeClient(responses), cfg, writers, "test", now=lambda: next(clock))
    coverage = [collector.poll_once() for _ in times]
    for writer in writers.values():
        writer.flush()
    return tmp_path / "lake", coverage


def test_raw_is_kept_at_baseline_and_each_new_hour_only(tmp_path, cfg, first, second):
    times = [FIRST_AT, SECOND_AT, SECOND_AT + timedelta(minutes=2), FIRST_AT.replace(hour=17, minute=58)]
    lake, coverage = run_polls(tmp_path, cfg, [first, second, second, first], times)
    assert [c["raw_reason"] for c in coverage] == ["baseline", "checkpoint", None, None]
    # The second poll is the first of hour 17, so it is the checkpoint; the rest of the hour stores none.
    assert len(read_zone(lake, "raw/bikepoint")) == 2
    assert [c["changes_written"] for c in coverage] == [30, 5, 0, 5]


def test_coverage_carries_network_totals(tmp_path, cfg, first):
    _, coverage = run_polls(tmp_path, cfg, [first], [FIRST_AT])
    records = normalise(first, POLL, cfg)
    assert coverage[0]["stations"] == 30
    assert coverage[0]["bikes"] == sum(r["bikes"] for r in records)
    assert coverage[0]["empty_stations"] == sum(r["bikes"] == 0 for r in records)


def test_failed_request_keeps_an_error_envelope(tmp_path, cfg, first):
    lake, coverage = run_polls(tmp_path, cfg, [TflHttpError(503, "/BikePoint", 3), first],
                               [FIRST_AT, SECOND_AT])
    assert coverage[0]["valid"] is False and coverage[0]["raw_reason"] == "error"
    # The next good poll is still the session's baseline.
    assert coverage[1]["raw_reason"] == "baseline" and coverage[1]["changes_written"] == 30
