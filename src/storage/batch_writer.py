"""Hourly gzip JSONL batches with manifests, written crash-safely.

Each record is appended to a local tmp file and fsynced immediately, so a
crash loses at most the record being written. When the hour changes (or
on flush), the tmp file is compressed, verified, published to the lake
with a manifest, read back and checked, and only then deleted (rule C7).
Leftover tmp files from a crashed run are published by recover().
"""

import gzip
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from src.storage.lake import ObjectExistsError

logger = logging.getLogger("tfl.batch_writer")

BATCH_FORMAT = "%Y%m%dT%H%M%SZ"


class BatchWriter:
    def __init__(self, lake, tmp_dir, dataset, zone_dir, file_prefix,
                 time_field="collected_at_utc", hour_partition=False):
        """
        zone_dir: e.g. "raw/line_status" → files land in
            {zone_dir}/date=YYYY-MM-DD[/hour=HH]/{file_prefix}_{start}.jsonl.gz
        time_field: record field holding an ISO UTC timestamp.
        """
        self.lake = lake
        self.dataset = dataset
        self.zone_dir = zone_dir.strip("/")
        self.file_prefix = file_prefix
        self.time_field = time_field
        self.hour_partition = hour_partition
        self.tmp_dir = Path(tmp_dir) / dataset / file_prefix
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_path = None
        self._batch_start = None
        self.published = []

    def append(self, record):
        at = _parse_time(record[self.time_field])

        if self._batch_start and _hour(at) != _hour(self._batch_start):
            self.flush()

        if self._tmp_path is None:
            self._batch_start = at
            self._tmp_path = self.tmp_dir / (
                f"{self.file_prefix}_{at.strftime(BATCH_FORMAT)}.jsonl"
            )

        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._tmp_path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.flush()
            os.fsync(file.fileno())

    def flush(self):
        """Publish the current batch, if any."""
        if self._tmp_path is not None:
            self._publish(self._tmp_path, self._batch_start)
        self._tmp_path = None
        self._batch_start = None

    def recover(self):
        """Publish tmp files left behind by a crashed run."""
        for path in sorted(self.tmp_dir.glob(f"{self.file_prefix}_*.jsonl")):
            stamp = path.stem.rsplit("_", 1)[-1]
            start = datetime.strptime(stamp, BATCH_FORMAT).replace(tzinfo=timezone.utc)
            logger.warning("Recovering unpublished batch %s", path.name)
            self._publish(path, start)

    def _object_dir(self, start):
        parts = [self.zone_dir, f"date={start:%Y-%m-%d}"]
        if self.hour_partition:
            parts.append(f"hour={start:%H}")
        return "/".join(parts)

    def _publish(self, tmp_path, start):
        raw = tmp_path.read_bytes()
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not lines:
            tmp_path.unlink()
            return None

        records = [json.loads(line) for line in lines]
        times = [record[self.time_field] for record in records]

        # mtime=0 makes the gzip bytes depend only on the content, so the
        # same batch always produces the same checksum.
        data = gzip.compress(raw, mtime=0)
        if gzip.decompress(data) != raw:
            raise RuntimeError(f"Compression check failed for {tmp_path}")

        name = f"{self.file_prefix}_{start.strftime(BATCH_FORMAT)}.jsonl.gz"
        object_path = f"{self._object_dir(start)}/{name}"
        checksum = hashlib.sha256(data).hexdigest()

        manifest = {
            "dataset": self.dataset,
            "object_path": object_path,
            "uri": self.lake.uri(object_path),
            "record_count": len(records),
            "sha256": checksum,
            "size_bytes": len(data),
            "content_encoding": "gzip",
            "format": "jsonl",
            "first_record_utc": min(times),
            "last_record_utc": max(times),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.lake.write_new(object_path, data, content_type="application/gzip")
        except ObjectExistsError:
            # A previous run published this batch but crashed before deleting
            # the tmp file. Identical content means the work is already done.
            if hashlib.sha256(self.lake.read(object_path)).hexdigest() != checksum:
                raise RuntimeError(
                    f"{object_path} exists with different content; tmp kept"
                ) from None
            logger.warning("Batch %s already published; cleaning up tmp", object_path)

        try:
            self.lake.write_new(
                f"{object_path}.manifest.json",
                (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
                content_type="application/json",
            )
        except ObjectExistsError:
            pass  # Manifest from the earlier, identical publish.

        stored = self.lake.read(object_path)
        if hashlib.sha256(stored).hexdigest() != checksum:
            raise RuntimeError(f"Read-back checksum mismatch for {object_path}; tmp kept")

        tmp_path.unlink()
        self.published.append(manifest)
        logger.info("Published %s (%d records)", manifest["uri"], len(records))
        return manifest


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _hour(at):
    return at.strftime("%Y%m%d%H")
