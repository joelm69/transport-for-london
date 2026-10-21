"""End-to-end collector tests with a fake TfL client and a local lake."""

import gzip
import json
from datetime import datetime, timedelta, timezone

from src.clients.tfl import TflHttpError, TflResponse
from src.collectors.line_status import LineStatusCollector, make_writers, run
from src.storage.lake import LocalLake


class FakeClient:
    def __init__(self, severity_meta, *poll_outcomes):
        self.severity_meta = severity_meta
        self.outcomes = list(poll_outcomes)

    def get_json(self, path, params=None):
        if path == "/Line/Meta/Severity":
            return self._response(path, self.severity_meta)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return self._response(path, outcome)

    @staticmethod
    def _response(path, data):
        return TflResponse(path=path, params={}, status=200, data=data,
                           fetched_at_utc="", latency_ms=5, attempts=1, size_bytes=1)


def clock_from(start, step_seconds=30):
    times = iter(start + timedelta(seconds=step_seconds * i) for i in range(100))
    return lambda: next(times)


def read_zone(lake, writers, zone):
    rows = []
    for manifest in writers[zone].published:
        rows += [json.loads(l) for l in gzip.decompress(lake.read(manifest["object_path"])).decode().splitlines()]
    return rows


def build(tmp_path, config, client):
    lake = LocalLake(tmp_path / "lake")
    writers = make_writers(lake, tmp_path / "tmp", "line_status")
    now = clock_from(datetime(2026, 10, 21, 17, 29, 46, tzinfo=timezone.utc))
    collector = LineStatusCollector(client, config, writers, "test", now=now)
    return lake, writers, collector


def run_polls(collector, writers, count):
    return run(collector, writers, interval=0, max_polls=count, sleep=lambda s: None)


def test_three_polls_write_raw_changes_and_coverage(tmp_path, config, severity_meta, first_poll, second_poll):
    client = FakeClient(severity_meta, first_poll, first_poll, second_poll)
    lake, writers, collector = build(tmp_path, config, client)

    assert run_polls(collector, writers, 3) == 3

    raw = read_zone(lake, writers, "raw")
    changes = read_zone(lake, writers, "changes")
    coverage = read_zone(lake, writers, "coverage")

    assert len(raw) == 3 and raw[0]["response"] == first_poll
    assert [c["changes_written"] for c in coverage] == [20, 0, 1]   # baseline, no change, Elizabeth
    assert all(c["valid"] for c in coverage)
    assert sorted({c["change_type"] for c in changes}) == ["BASELINE", "NEW_INCIDENT"]


def test_failed_poll_keeps_raw_and_marks_coverage(tmp_path, config, severity_meta, first_poll):
    client = FakeClient(severity_meta,
                        first_poll, TflHttpError(503, "/x", 4), first_poll)
    lake, writers, collector = build(tmp_path, config, client)

    run_polls(collector, writers, 3)

    raw = read_zone(lake, writers, "raw")
    coverage = read_zone(lake, writers, "coverage")
    assert len(raw) == 3 and raw[1]["http_status"] == 503 and raw[1]["response"] is None
    assert [c["valid"] for c in coverage] == [True, False, True]
    assert coverage[1]["errors"] == ["P1"]
    # The failure does not reset state: the third poll matches the first.
    assert coverage[2]["changes_written"] == 0


def test_invalid_poll_writes_no_changes(tmp_path, config, severity_meta, first_poll):
    broken = [line for line in first_poll if line["id"] != "central"]
    client = FakeClient(severity_meta, first_poll, broken)
    lake, writers, collector = build(tmp_path, config, client)

    run_polls(collector, writers, 2)

    coverage = read_zone(lake, writers, "coverage")
    assert coverage[1]["valid"] is False and coverage[1]["errors"] == ["P2"]
    assert coverage[1]["changes_written"] == 0


def test_stop_request_flushes_batches(tmp_path, config, severity_meta, first_poll):
    client = FakeClient(severity_meta, *[first_poll] * 5)
    lake, writers, collector = build(tmp_path, config, client)
    calls = {"n": 0}

    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    polls = run(collector, writers, interval=0, sleep=lambda s: None, should_stop=stop_after_two)

    assert polls == 2
    assert len(read_zone(lake, writers, "coverage")) == 2


def test_quiet_hour_still_publishes_change_batch(tmp_path, config, severity_meta, first_poll):
    """Bug found in production: the change writer only rolled over when a new
    change arrived, so a quiet hour left the previous batch unpublished."""
    lake = LocalLake(tmp_path / "lake")
    writers = make_writers(lake, tmp_path / "tmp", "line_status")
    times = iter([
        datetime(2026, 10, 21, 19, 59, 46, tzinfo=timezone.utc),   # baseline at 19:59
        datetime(2026, 10, 21, 20, 0, 16, tzinfo=timezone.utc),    # 20:00, nothing changed
    ])
    client = FakeClient(severity_meta, first_poll, first_poll)
    collector = LineStatusCollector(client, config, writers, "test", now=lambda: next(times))

    collector.poll_once()
    collector.poll_once()

    # Without calling flush(): the 19:xx change batch must already be published.
    assert len(writers["changes"].published) == 1
    assert "line_status_changes_20261021T195946Z" in writers["changes"].published[0]["object_path"]
