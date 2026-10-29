"""Stop disruptions: validation, change detection and the store-on-change collector."""

import copy
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.clients.tfl import TflHttpError, TflResponse
from src.collectors.runner import make_writers
from src.collectors.stop_disruptions import StopDisruptionCollector
from src.deduplication.stop_disruptions import (
    content_hash, detect_changes, normalise, to_date_kind,
)
from src.storage.lake import LocalLake
from src.validation.stop_disruptions import ERROR, EXCLUDED, WARN, validate_poll
from tests.unit.helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "stop_disruptions"
FIRST_AT = datetime(2026, 10, 21, 20, 59, 35, tzinfo=timezone.utc)
SECOND_AT = datetime(2026, 10, 21, 21, 1, 53, tzinfo=timezone.utc)
POLL = {"poll_id": "p", "collected_at_utc": FIRST_AT.isoformat(), "collection_window": "test"}


@pytest.fixture
def cfg():
    return json.loads((ROOT / "config" / "stop_disruptions.json").read_text())


@pytest.fixture
def first():
    return json.loads((FIXTURES / "stop_disruptions_20261021T205935Z.json").read_text())


@pytest.fixture
def second():
    return json.loads((FIXTURES / "stop_disruptions_20261021T210153Z.json").read_text())


def rules(issues, level=None):
    return sorted({i.rule for i in issues if level is None or i.level == level})


def records(data, cfg, at=FIRST_AT):
    return normalise(data, POLL, cfg, at, validate_poll(data, cfg, at))


# --- Validation (step 4 rules) ------------------------------------------------------

def test_live_responses_pass_without_errors_or_exclusions(cfg, first, second):
    for data, at in ((first, FIRST_AT), (second, SECOND_AT)):
        issues = validate_poll(data, cfg, at)
        assert rules(issues, ERROR) == [] and rules(issues, EXCLUDED) == []


def test_p1_non_array(cfg):
    assert rules(validate_poll({"oops": 1}, cfg, FIRST_AT), ERROR) == ["P1"]


def test_p2_empty_is_only_a_warning(cfg):
    assert rules(validate_poll([], cfg, FIRST_AT)) == ["P2"]


def test_same_station_type_and_start_with_different_text_are_two_notices(cfg, first):
    """2026-10-24: the Circle and District closures at a station share station, type and start."""
    clash = copy.deepcopy(first[0])
    clash["description"] = "Different text, same station, type and start"
    data = first + [clash]
    issues = validate_poll(data, cfg, FIRST_AT)
    assert not rules(issues, ERROR) and not rules(issues, EXCLUDED)
    records = normalise(data, {}, cfg, FIRST_AT, issues)
    assert len(records) == len(first) + 1
    assert len({r["disruption_key"] for r in records}) == len(records)


def test_notices_without_a_clash_keep_their_key(cfg, first):
    clash = copy.deepcopy(first[0])
    clash["description"] = "Different text"
    before = {r["disruption_key"] for r in normalise(first[1:], {}, cfg, FIRST_AT)}
    after = {r["disruption_key"] for r in normalise(first + [clash], {}, cfg, FIRST_AT)}
    assert before <= after


def test_d8_exact_duplicate_is_kept_once(cfg, first):
    data = first + [copy.deepcopy(first[0])]
    issues = validate_poll(data, cfg, FIRST_AT)
    assert rules(issues, EXCLUDED) == ["D8"] and not rules(issues, ERROR)
    assert len(normalise(data, {}, cfg, FIRST_AT, issues)) == len(first)


def test_d1_unidentifiable_record_is_excluded_not_fatal(cfg, first):
    data = copy.deepcopy(first)
    data[0]["fromDate"] = "not a date"
    issues = validate_poll(data, cfg, FIRST_AT)
    assert rules(issues, ERROR) == []
    assert rules(issues, EXCLUDED) == ["D1"]
    assert len(normalise(data, POLL, cfg, FIRST_AT, issues)) == len(first) - 1


@pytest.mark.parametrize("field, value, rule", [
    ("atcoCode", "bad code", "D2"),
    ("mode", "river-bus", "D3"),
    ("type", "Something New", "D4"),
])
def test_record_warnings(cfg, first, field, value, rule):
    data = copy.deepcopy(first)
    data[0][field] = value
    assert rule in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_d5_planned_work_must_end_after_it_starts(cfg, first):
    data = copy.deepcopy(first)
    planned = next(d for d in data if d["appearance"] == "PlannedWork")
    planned["toDate"] = planned["fromDate"]
    assert "D5" in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_d6_future_start(cfg, first):
    data = copy.deepcopy(first)
    live = next(d for d in data if d["appearance"] != "PlannedWork")
    live["fromDate"] = "2026-10-23T09:00:00Z"
    assert "D6" in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_d6_allows_planned_work_announced_ahead(cfg, first):
    data = copy.deepcopy(first)
    for record in data:
        if record["appearance"] != "PlannedWork":
            record["fromDate"] = "2026-10-01T09:00:00Z"
    planned = next(d for d in data if d["appearance"] == "PlannedWork")
    planned["fromDate"] = "2026-10-23T09:00:00Z"
    planned["toDate"] = "2026-11-05T09:00:00Z"
    assert "D6" not in rules(validate_poll(data, cfg, FIRST_AT), WARN)


def test_d4_names_the_unknown_label(cfg, first):
    data = copy.deepcopy(first)
    data[0]["appearance"] = "SomethingNew"
    messages = [i.message for i in validate_poll(data, cfg, FIRST_AT) if i.rule == "D4"]
    assert messages and "'SomethingNew'" in messages[0]


# --- Identity, hashing, change detection (step 2) -----------------------------------

def test_disruption_keys_are_unique_in_real_data(cfg, first):
    keys = [r["disruption_key"] for r in records(first, cfg)]
    assert len(keys) == len(set(keys)) == 63


def test_real_calls_have_equal_content_hash(cfg, first, second):
    """Real finding: the second call only shuffled order and moved rolling toDates,
    so nothing should be stored or recorded as changed."""
    assert content_hash(records(first, cfg)) == content_hash(records(second, cfg, SECOND_AT))


def test_hash_ignores_order(cfg, first):
    assert content_hash(records(first, cfg)) == content_hash(records(list(reversed(first)), cfg))


def test_hash_changes_when_a_description_changes(cfg, first):
    data = copy.deepcopy(first)
    data[0]["description"] += " Updated."
    assert content_hash(records(first, cfg)) != content_hash(records(data, cfg))


def test_to_date_kinds(cfg):
    assert to_date_kind("PlannedWork", "2026-11-30T01:29:00Z", FIRST_AT, cfg) == "planned"
    assert to_date_kind("RealTime", "2026-10-21T23:58:00Z", FIRST_AT, cfg) == "rolling"
    assert to_date_kind("RealTime", "2026-10-22T00:29:00Z", FIRST_AT, cfg) == "end_of_day"
    assert to_date_kind("RealTime", "2026-12-15T09:00:00Z", FIRST_AT, cfg) == "dated"
    assert to_date_kind("RealTime", None, FIRST_AT, cfg) == "missing"


def test_accessibility_flag(cfg, first):
    flagged = [r for r in records(first, cfg) if r["accessibility_flag"]]
    assert flagged, "real data contains lift / step-free notices"
    assert all(any(w in r["description"].lower() for w in ("lift", "step", "escalator", "wheelchair", "ramp", "accessib"))
               for r in flagged)


def test_baseline_then_no_changes_between_real_calls(cfg, first, second):
    changes, state = detect_changes({}, records(first, cfg), True, POLL)
    assert len(changes) == 63 and {c["change_type"] for c in changes} == {"BASELINE"}
    changes, _ = detect_changes(state, records(second, cfg, SECOND_AT), False, POLL)
    assert changes == []


def test_new_update_and_resolved(cfg, first):
    _, state = detect_changes({}, records(first, cfg), True, POLL)
    data = copy.deepcopy(first)
    gone = data.pop(0)                                   # resolved
    data[0]["description"] += " Updated."                # update
    new = copy.deepcopy(data[1])
    new["fromDate"] = "2026-10-21T20:50:00Z"             # new incident
    data.append(new)

    later = {**POLL, "poll_id": "p2"}
    changes, _ = detect_changes(state, records(data, cfg), False, later)
    kinds = sorted(c["change_type"] for c in changes)
    assert kinds == ["NEW_INCIDENT", "RESOLVED", "UPDATE"]
    resolved = next(c for c in changes if c["change_type"] == "RESOLVED")
    assert resolved["atco_code"] == gone["atcoCode"] and resolved["poll_id"] == "p2"


def test_change_detection_is_deterministic(cfg, first):
    a = detect_changes({}, records(first, cfg), True, POLL)
    b = detect_changes({}, records(first, cfg), True, POLL)
    assert a == b


# --- Collector: store raw on change + hourly checkpoint (step 3) ---------------------

class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def get_json(self, path, params=None):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return TflResponse(path=path, params={}, status=200, data=outcome,
                           fetched_at_utc="", latency_ms=5, attempts=1, size_bytes=1)


def collect(tmp_path, cfg, outcomes, times):
    lake = LocalLake(tmp_path / "lake")
    writers = make_writers(lake, tmp_path / "tmp", cfg["dataset"], cfg["changes_name"])
    clock = iter(times)
    collector = StopDisruptionCollector(FakeClient(*outcomes), cfg, writers, "test", now=lambda: next(clock))
    coverage = [collector.poll_once() for _ in outcomes]
    for writer in writers.values():
        writer.flush()
    raw = [json.loads(line) for m in writers["raw"].published
           for line in gzip.decompress(lake.read(m["object_path"])).decode().splitlines()]
    return coverage, raw, writers


def test_unchanged_polls_store_no_raw(tmp_path, cfg, first, second):
    # Mid-hour, so no hourly checkpoint falls inside the test.
    t0 = datetime(2026, 10, 21, 20, 10, 0, tzinfo=timezone.utc)
    coverage, raw, _ = collect(tmp_path, cfg, [first, second, first],
                               [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)])
    assert [c["raw_reason"] for c in coverage] == ["baseline", None, None]
    assert [r["raw_reason"] for r in raw] == ["baseline"]
    assert len({c["content_hash"] for c in coverage}) == 1


def test_change_and_hourly_checkpoint_store_raw(tmp_path, cfg, first):
    changed = copy.deepcopy(first)
    changed[0]["description"] += " Updated."
    t0 = datetime(2026, 10, 21, 20, 58, 0, tzinfo=timezone.utc)
    coverage, raw, writers = collect(
        tmp_path, cfg, [first, changed, changed, changed],
        [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2), t0 + timedelta(minutes=3)],
    )
    # 20:58 baseline, 20:59 change, 21:00 new hour -> checkpoint, 21:01 nothing
    assert [c["raw_reason"] for c in coverage] == ["baseline", "change", "checkpoint", None]
    assert [c["changes_written"] for c in coverage] == [63, 1, 0, 0]
    assert len(raw) == 3


def test_failed_poll_stores_error_and_keeps_state(tmp_path, cfg, first):
    t0 = datetime(2026, 10, 21, 20, 10, 0, tzinfo=timezone.utc)
    coverage, raw, _ = collect(tmp_path, cfg, [first, TflHttpError(503, "/x", 4), first],
                               [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)])
    assert [c["valid"] for c in coverage] == [True, False, True]
    assert [c["raw_reason"] for c in coverage] == ["baseline", "error", None]
    assert coverage[2]["changes_written"] == 0  # the failure did not reset state


def test_invalid_poll_is_kept_for_investigation(tmp_path, cfg, first):
    t0 = datetime(2026, 10, 21, 20, 10, 0, tzinfo=timezone.utc)
    coverage, raw, _ = collect(tmp_path, cfg, [first, {"not": "an array"}], [t0, t0 + timedelta(minutes=1)])
    assert coverage[1]["errors"] == ["P1"] and coverage[1]["raw_reason"] == "invalid"
    assert coverage[1]["changes_written"] == 0
