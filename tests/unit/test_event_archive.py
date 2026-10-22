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
    archive.write("arrival", arrival(1), "events", 0, 10, utc(8, 50))
    archive.write("dropped", {"event": "dropped", "reason": "replanned"}, "events", 0, 11, utc(8, 55))
    assert archive.tick(utc(8, 59)) == {}                 # same hour: nothing published yet
    assert records(lake_root, "processed/arrival_events") == []

    assert archive.tick(utc(9, 0)) == {("events", 0): 12}             # hour changed: both batches published
    published = records(lake_root, "processed/arrival_events")
    assert [r["kafka_offset"] for r in published] == [10]
    assert published[0]["archived_at_utc"].startswith("2026-10-22T08:50")
    assert [r["reason"] for r in records(lake_root, "processed/arrival_drops")] == ["replanned"]

    archive.write("arrival", arrival(2), "events", 0, 12, utc(9, 1))
    assert archive.tick(utc(9, 2)) == {}
    assert archive.close() == {("events", 0): 13}
    assert archive.close() == {}                          # nothing new to commit


def test_crash_leaves_events_recoverable(tmp_path):
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    archive.write("arrival", arrival(1), "events", 0, 10, utc(8, 50))
    # Crash: no close(), nothing committed. A new process recovers the fsynced tmp batch.
    restarted = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    restarted.recover()
    assert [r["kafka_offset"] for r in records(lake_root, "processed/arrival_events")] == [10]


def test_snapshot_events_go_to_coverage(tmp_path):
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    archive.write("snapshot", {"event": "snapshot", "line_id": "victoria", "snapshot_at": "2026-10-22T08:50:00Z"}, "events", 0, 5, utc(8, 50))
    archive.close()
    assert [r["line_id"] for r in records(lake_root, "coverage/arrivals")] == ["victoria"]


def test_predictions_and_events_commit_per_topic(tmp_path):
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    archive.write("arrival", arrival(1), "events", 0, 7, utc(8, 50))
    archive.write("predictions", {"line_id": "victoria", "records": [{"vehicleId": "1"}]}, "predictions", 0, 40, utc(8, 50))
    assert archive.tick(utc(9, 0)) == {("events", 0): 8, ("predictions", 0): 41}
    assert [r["line_id"] for r in records(lake_root, "raw/arrival_predictions")] == ["victoria"]


def test_hour_change_inside_write_never_commits_an_unpublished_record(tmp_path):
    """Regression (VM, 2026-10-22 11:00): a record whose write crossed the hour made its writer
    publish the old batch itself; the next tick then committed that record's offset although
    it sat in the new, unpublished batch."""
    lake_root = tmp_path / "lake"
    archive = EventArchive(make_archive_writers(LocalLake(lake_root), tmp_path / "tmp"))
    assert archive.write("predictions", {"line_id": "victoria"}, "predictions", 0, 707, utc(10, 59)) == {}
    # No tick between the hour change and this write.
    committed = archive.write("predictions", {"line_id": "central"}, "predictions", 0, 708, utc(11, 0))
    assert committed == {("predictions", 0): 708}        # only the published record
    assert archive.tick(utc(11, 1)) == {}                # 708 is still unpublished
    assert archive.close() == {("predictions", 0): 709}
