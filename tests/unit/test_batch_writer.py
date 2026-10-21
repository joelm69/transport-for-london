import gzip
import hashlib
import json

import pytest

from src.storage.batch_writer import BatchWriter
from src.storage.lake import LocalLake, ObjectExistsError


def record(time, value=1):
    return {"collected_at_utc": time, "value": value}


@pytest.fixture
def lake(tmp_path):
    return LocalLake(tmp_path / "lake")


@pytest.fixture
def writer(lake, tmp_path):
    return BatchWriter(lake, tmp_path / "tmp", "line_status", "raw/line_status",
                       "line_status", hour_partition=True)


def read_batch(lake, manifest):
    return [json.loads(line) for line in
            gzip.decompress(lake.read(manifest["object_path"])).decode().splitlines()]


def test_flush_publishes_gzip_batch_with_manifest(writer, lake):
    writer.append(record("2026-10-21T17:29:46+00:00", 1))
    writer.append(record("2026-10-21T17:30:16+00:00", 2))
    writer.flush()

    [manifest] = writer.published
    assert manifest["object_path"] == (
        "raw/line_status/date=2026-10-21/hour=17/line_status_20261021T172946Z.jsonl.gz"
    )
    assert manifest["record_count"] == 2
    assert [r["value"] for r in read_batch(lake, manifest)] == [1, 2]
    assert hashlib.sha256(lake.read(manifest["object_path"])).hexdigest() == manifest["sha256"]
    stored = json.loads(lake.read(manifest["object_path"] + ".manifest.json"))
    assert stored["sha256"] == manifest["sha256"]


def test_hour_change_starts_new_batch(writer):
    writer.append(record("2026-10-21T17:59:50+00:00"))
    writer.append(record("2026-10-21T18:00:20+00:00"))
    writer.flush()

    paths = [m["object_path"] for m in writer.published]
    assert len(paths) == 2
    assert "hour=17" in paths[0] and "hour=18" in paths[1]


def test_records_are_on_disk_before_flush(writer):
    """Crash safety: an appended record survives without flush()."""
    writer.append(record("2026-10-21T17:29:46+00:00"))
    tmp_files = list(writer.tmp_dir.glob("*.jsonl"))
    assert len(tmp_files) == 1
    assert "17:29:46" in tmp_files[0].read_text()


def test_recover_publishes_leftover_tmp(lake, tmp_path):
    crashed = BatchWriter(lake, tmp_path / "tmp", "line_status", "raw/line_status", "line_status")
    crashed.append(record("2026-10-21T17:29:46+00:00"))
    # Simulate a crash: no flush. A new run recovers the leftover file.
    fresh = BatchWriter(lake, tmp_path / "tmp", "line_status", "raw/line_status", "line_status")
    fresh.recover()

    assert len(fresh.published) == 1
    assert list(fresh.tmp_dir.glob("*.jsonl")) == []


def test_recover_after_publish_but_before_cleanup(lake, tmp_path, writer):
    writer.append(record("2026-10-21T17:29:46+00:00"))
    tmp_file = next(writer.tmp_dir.glob("*.jsonl"))
    saved = tmp_file.read_bytes()
    writer.flush()
    tmp_file.write_bytes(saved)  # crash happened before tmp deletion

    writer.recover()
    assert list(writer.tmp_dir.glob("*.jsonl")) == []


def test_lake_never_overwrites(lake):
    lake.write_new("a/b.txt", b"first")
    with pytest.raises(ObjectExistsError):
        lake.write_new("a/b.txt", b"second")
    assert lake.read("a/b.txt") == b"first"


def test_same_content_gives_same_checksum(lake, tmp_path):
    """gzip mtime=0 makes batches reproducible."""
    sums = []
    for name in ("one", "two"):
        w = BatchWriter(LocalLake(tmp_path / name), tmp_path / f"tmp_{name}", "d", "raw/d", "d")
        w.append(record("2026-10-21T17:29:46+00:00"))
        w.flush()
        sums.append(w.published[0]["sha256"])
    assert sums[0] == sums[1]


def test_empty_flush_publishes_nothing(writer):
    writer.flush()
    assert writer.published == []
