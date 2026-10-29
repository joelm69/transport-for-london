"""Road history: validation, change detection and the two-feed collector."""

import copy
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.clients.tfl import TflHttpError, TflResponse
from src.collectors.road import RoadCollector, make_writers
from src.deduplication.road import detect_changes, normalise_corridors, normalise_disruptions
from src.storage.lake import LocalLake
from src.validation.road import ERROR, EXCLUDED, WARN, validate_corridors, validate_disruptions
from tests.unit.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "road"
AT = datetime(2026, 10, 29, 18, 18, 8, tzinfo=timezone.utc)
POLL = {"poll_id": "p", "collected_at_utc": AT.isoformat(), "collection_window": "test"}


@pytest.fixture
def cfg():
    return json.loads((ROOT / "config" / "road.json").read_text())


@pytest.fixture
def disruptions():
    return json.loads((FIXTURES / "road_disruption_20261029T181808Z.json").read_text())


@pytest.fixture
def corridors():
    return json.loads((FIXTURES / "road_status_20261029T181808Z.json").read_text())


def rules(issues, level):
    return sorted({i.rule for i in issues if i.level == level})


# --- Validation ----------------------------------------------------------------------

def test_real_responses_are_clean(cfg, disruptions, corridors):
    assert validate_disruptions(disruptions, cfg) == []
    assert validate_corridors(corridors, cfg) == []


def test_disruption_rules(cfg, disruptions):
    data = copy.deepcopy(disruptions)
    data[0]["severity"] = "Apocalyptic"
    data[1]["startDateTime"] = None
    data[2]["point"] = "[2.35,48.85]"   # Paris
    del data[3]["id"]
    issues = validate_disruptions(data, cfg)
    assert rules(issues, WARN) == ["R2", "R3", "R4"]
    assert rules(issues, EXCLUDED) == ["R1"]
    assert len(normalise_disruptions(data, POLL, issues)) == len(disruptions) - 1


def test_repeated_disruption_id_is_an_error(cfg, disruptions):
    assert "P3" in rules(validate_disruptions(disruptions + [disruptions[0]], cfg), ERROR)


def test_corridor_rules(cfg, corridors):
    assert rules(validate_corridors([], cfg), ERROR) == ["C1"]
    assert rules(validate_corridors(corridors + [corridors[0]], cfg), ERROR) == ["C2"]
    data = copy.deepcopy(corridors)
    data[0]["statusSeverity"] = "Gridlock"
    assert rules(validate_corridors(data, cfg), WARN) == ["C3"]


# --- Normalising and change detection ----------------------------------------------

def test_normalise_reads_closed_streets_and_location(disruptions):
    records = normalise_disruptions(disruptions, POLL)
    assert sum(bool(r["closed_streets"]) for r in records) == 7
    assert sum(r["is_incident"] for r in records) == 9
    assert all(r["lat"] and r["lon"] for r in records)


def test_update_timestamps_alone_are_not_a_change(disruptions):
    _, state = detect_changes({}, normalise_disruptions(disruptions, POLL), True, POLL, "disruption_id")
    data = copy.deepcopy(disruptions)
    for record in data:
        record["lastModifiedTime"] = record["currentUpdateDateTime"] = "2026-10-29T18:30:00Z"
    changes, _ = detect_changes(state, normalise_disruptions(data, POLL), False, POLL, "disruption_id")
    assert changes == []


def test_new_updated_and_cleared_disruptions(disruptions):
    _, state = detect_changes({}, normalise_disruptions(disruptions[1:], POLL), True, POLL, "disruption_id")
    data = copy.deepcopy(disruptions[:-1])
    data[1]["severity"] = "Severe"
    changes, _ = detect_changes(state, normalise_disruptions(data, POLL), False, POLL, "disruption_id")
    kinds = {c["disruption_id"]: c["change_type"] for c in changes}
    assert kinds == {disruptions[0]["id"]: "NEW", disruptions[1]["id"]: "UPDATE", disruptions[-1]["id"]: "CLEARED"}


def test_corridor_status_change(corridors):
    _, state = detect_changes({}, normalise_corridors(corridors, POLL), True, POLL, "corridor_id")
    data = copy.deepcopy(corridors)
    data[0]["statusSeverity"] = "Severe" if data[0]["statusSeverity"] != "Severe" else "Good"
    changes, _ = detect_changes(state, normalise_corridors(data, POLL), False, POLL, "corridor_id")
    assert [(c["corridor_id"], c["change_type"]) for c in changes] == [(corridors[0]["id"], "UPDATE")]


# --- Collector ---------------------------------------------------------------------

class FakeClient:
    def __init__(self, responses):
        self.responses = responses   # {endpoint: [data or exception, ...]}

    def get_json(self, path, params=None):
        item = self.responses[path].pop(0)
        if isinstance(item, Exception):
            raise item
        return TflResponse(data=item, status=200, path=path, params=params or {}, attempts=1,
                           latency_ms=5, size_bytes=1, headers={}, cache_max_age=150,
                           fetched_at_utc=AT.isoformat())


def read_zone(root, zone):
    rows = []
    for path in sorted((root / zone).rglob("*.jsonl.gz")):
        with gzip.open(path, "rt") as handle:
            rows.extend(json.loads(line) for line in handle)
    return rows


def run_polls(tmp_path, cfg, disruption_responses, corridor_responses, times):
    writers = make_writers(LocalLake(tmp_path / "lake"), tmp_path / "tmp", cfg)
    clock = iter(times)
    client = FakeClient({cfg["disruption_endpoint"]: list(disruption_responses),
                         cfg["corridor_endpoint"]: list(corridor_responses)})
    collector = RoadCollector(client, cfg, writers, "test", now=lambda: next(clock))
    coverage = [collector.poll_once() for _ in times]
    for writer in writers.values():
        writer.flush()
    return tmp_path / "lake", coverage


def test_collector_writes_both_change_streams(tmp_path, cfg, disruptions, corridors):
    later = copy.deepcopy(disruptions[:-1])
    times = [AT, AT + timedelta(minutes=5), AT + timedelta(minutes=10)]
    lake, coverage = run_polls(tmp_path, cfg, [disruptions, disruptions, later],
                               [corridors, corridors, corridors], times)
    assert [c["raw_reason"] for c in coverage] == ["baseline", None, None]
    assert [c["disruption_changes"] for c in coverage] == [29, 0, 1]
    assert [c["corridor_changes"] for c in coverage] == [24, 0, 0]
    assert read_zone(lake, "processed/road_disruption_changes")[-1]["change_type"] == "CLEARED"
    assert len(read_zone(lake, "processed/road_corridor_changes")) == 24
    assert coverage[0]["incidents"] == 9 and coverage[0]["corridors"] == 24


def test_one_feed_failing_does_not_stop_the_other(tmp_path, cfg, disruptions, corridors):
    lake, coverage = run_polls(tmp_path, cfg, [TflHttpError(503, "/Road/all/Disruption", 3)],
                               [corridors], [AT])
    c = coverage[0]
    assert c["disruptions_valid"] is False and c["corridors_valid"] is True
    assert c["raw_reason"] == "error" and c["corridor_changes"] == 24
    assert read_zone(lake, "processed/road_disruption_changes") == []


def test_checkpoint_each_new_hour(tmp_path, cfg, disruptions, corridors):
    times = [AT, AT + timedelta(minutes=5), AT.replace(hour=19, minute=0)]
    lake, coverage = run_polls(tmp_path, cfg, [disruptions] * 3, [corridors] * 3, times)
    assert [c["raw_reason"] for c in coverage] == ["baseline", None, "checkpoint"]
    assert len(read_zone(lake, "raw/road")) == 2
