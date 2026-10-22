import gzip
import json

import pytest

from src.storage.lake import LocalLake
from src.storage.stream_batch_writer import StreamBatchWriter


def record(time, size=1):
    return {"collected_at_utc": time, "payload": "x" * size}


@pytest.fixture
def lake(tmp_path):
    return LocalLake(tmp_path / "lake")


def writer(lake, tmp_path):
    return StreamBatchWriter(lake, tmp_path / "tmp", "arrivals", "raw/arrival_predictions",
                             "arrival_predictions", hour_partition=True)


def read(lake, manifest):
    return [json.loads(line) for line in gzip.decompress(lake.read(manifest["object_path"])).splitlines()]


def test_publish_streams_a_valid_gzip_with_manifest(lake, tmp_path):
    w = writer(lake, tmp_path)
    w.append(record("2026-10-22T08:10:00+00:00", 50000))
    w.append(record("2026-10-22T08:20:00+00:00"))
    w.roll_over("2026-10-22T09:00:00+00:00")
    [manifest] = w.published
    assert manifest["object_path"].startswith("raw/arrival_predictions/date=2026-10-22/hour=08/")
    assert manifest["record_count"] == 2 and manifest["last_record_utc"] == "2026-10-22T08:20:00+00:00"
    assert [len(r["payload"]) for r in read(lake, manifest)] == [50000, 1]
    assert lake.sha256(manifest["object_path"]) == manifest["sha256"]
    assert not list((tmp_path / "tmp").rglob("*.gz"))


def test_recover_publishes_and_drops_a_torn_record(lake, tmp_path):
    w = writer(lake, tmp_path)
    w.append(record("2026-10-22T08:10:00+00:00"))
    w.append(record("2026-10-22T08:11:00+00:00"))
    [tmp] = list((tmp_path / "tmp").rglob("*.jsonl.gz"))
    with tmp.open("ab") as file:                      # a crash mid-write
        file.write(gzip.compress(b'{"collected_at_utc": "2026-10-22T08:12:00+00:00"}\n')[:15])

    restarted = writer(lake, tmp_path)
    restarted.recover()
    [manifest] = restarted.published
    assert manifest["record_count"] == 2
    assert [r["collected_at_utc"][11:16] for r in read(lake, manifest)] == ["08:10", "08:11"]


def test_recover_after_publish_before_cleanup(lake, tmp_path):
    w = writer(lake, tmp_path)
    w.append(record("2026-10-22T08:10:00+00:00"))
    [tmp] = list((tmp_path / "tmp").rglob("*.jsonl.gz"))
    copy = tmp.read_bytes()
    w.flush()
    tmp.write_bytes(copy)                              # tmp left behind by a crash after upload
    restarted = writer(lake, tmp_path)
    restarted.recover()
    assert restarted.published[0]["record_count"] == 1
    assert not tmp.exists()
