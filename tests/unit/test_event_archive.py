import gzip
import json
from datetime import datetime, timezone

from src.storage.lake import LocalLake
from src.streaming.archive import EventArchive, make_archive_writers


def utc(hh, mm):
    return datetime(2026, 10, 22, hh, mm, tzinfo=timezone.utc)


def arrival(n):
    return {"event": "arrival", "visit_key": f"victoria|{n}|S|t", "arrived_at": "2026-10-22T07:59:00Z"}


def records(lake_root, zone):
    out = []
    for path in sorted((lake_root / zone).rglob("*.jsonl.gz")):
        out += [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    return out


def test_offsets_commit_only_after_publish(tmp_path):
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))

    assert archive.tick(utc(8, 50)) == {}
    archive.write(arrival(1), 0, 10, utc(8, 50))
    archive.write({"event": "dropped", "reason": "replanned"}, 0, 11, utc(8, 55))
    assert archive.tick(utc(8, 59)) == {}                 # same hour: nothing published yet
    assert records(lake_root, "processed/arrival_events") == []

    assert archive.tick(utc(9, 0)) == {0: 12}             # hour changed: both batches published
    published = records(lake_root, "processed/arrival_events")
    assert [r["kafka_offset"] for r in published] == [10]
    assert published[0]["archived_at_utc"].startswith("2026-10-22T08:50")
    assert [r["reason"] for r in records(lake_root, "processed/arrival_drops")] == ["replanned"]

    archive.write(arrival(2), 0, 12, utc(9, 1))
    assert archive.tick(utc(9, 2)) == {}
    assert archive.close() == {0: 13}
    assert archive.close() == {}                          # nothing new to commit


def test_crash_leaves_events_recoverable(tmp_path):
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    archive.write(arrival(1), 0, 10, utc(8, 50))
    # Crash: no close(), nothing committed. A new process recovers the fsynced tmp batch.
    restarted = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    restarted.recover()
    assert [r["kafka_offset"] for r in records(lake_root, "processed/arrival_events")] == [10]
