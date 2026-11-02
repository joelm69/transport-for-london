"""Hourly gzip JSONL batches for large records, written and published without holding the
batch in memory.

BatchWriter keeps an uncompressed tmp file and compresses it in memory at publish time: fine
for small change records, not for an hour of arrival prediction snapshots (~150 MB of JSON).
StreamBatchWriter compresses each record as it arrives, appending one gzip member per record to
the tmp file (a concatenation of gzip members is a valid gzip file), fsyncs it, and publishes by
streaming the file from disk. Memory use is one record, whatever the batch size.

With compression="xz" the tmp file is plain JSONL instead (one fsynced line per record) and is
compressed with xz when the hour is published, streaming 1 MB at a time: across a whole hour xz
finds the repetition between snapshots, so the object is about 60% smaller than per-record gzip.
Preset 3 needs about 32 MB of memory.

Same contract as BatchWriter: create-only objects, a manifest with the SHA-256, read-back
verification before the tmp file is deleted, and recover() for batches left by a crash.
"""

import gzip
import hashlib
import json
import logging
import lzma
import os
from datetime import datetime, timezone
from pathlib import Path

from src.storage.batch_writer import BATCH_FORMAT, _hour, _parse_time
from src.storage.lake import CHUNK, ObjectExistsError

logger = logging.getLogger("tfl.stream_batch_writer")
XZ_PRESET = 3


class StreamBatchWriter:
    def __init__(self, lake, tmp_dir, dataset, zone_dir, file_prefix,
                 time_field="collected_at_utc", hour_partition=False, compression="gzip"):
        self.lake = lake
        self.dataset = dataset
        self.zone_dir = zone_dir.strip("/")
        self.file_prefix = file_prefix
        self.time_field = time_field
        self.hour_partition = hour_partition
        self.compression = compression
        self.tmp_dir = Path(tmp_dir) / dataset / file_prefix
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_path = None
        self._batch_start = None
        self._stats = None
        self.published = []

    def roll_over(self, at):
        if isinstance(at, str):
            at = _parse_time(at)
        if self._batch_start and _hour(at) != _hour(self._batch_start):
            self.flush()

    def append(self, record):
        at = _parse_time(record[self.time_field])
        self.roll_over(at)
        if self._tmp_path is None:
            self._batch_start = at
            suffix = ".jsonl" if self.compression == "xz" else ".jsonl.gz"
            self._tmp_path = self.tmp_dir / f"{self.file_prefix}_{at.strftime(BATCH_FORMAT)}{suffix}"
            self._stats = {"count": 0, "first": None, "last": None}

        line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with self._tmp_path.open("ab") as file:
            file.write(line if self.compression == "xz" else gzip.compress(line, mtime=0))
            file.flush()
            os.fsync(file.fileno())
        _count(self._stats, record[self.time_field])

    def flush(self):
        if self._tmp_path is not None:
            self._publish(self._tmp_path, self._batch_start, self._stats)
        self._tmp_path = None
        self._batch_start = None
        self._stats = None

    def recover(self):
        """Publish tmp files left behind by a crashed run. A record cut off mid-write by the
        crash is dropped: the file is truncated to its last complete gzip member."""
        for pattern, scan in ((".jsonl.gz", _scan), (".jsonl", _scan_lines)):   # gzip from before xz too
            for path in sorted(self.tmp_dir.glob(f"{self.file_prefix}_*{pattern}")):
                stamp = path.name[len(self.file_prefix) + 1:-len(pattern)]
                start = datetime.strptime(stamp, BATCH_FORMAT).replace(tzinfo=timezone.utc)
                logger.warning("Recovering unpublished batch %s", path.name)
                self._publish(path, start, scan(path, self.time_field))

    def _object_dir(self, start):
        parts = [self.zone_dir, f"date={start:%Y-%m-%d}"]
        if self.hour_partition:
            parts.append(f"hour={start:%H}")
        return "/".join(parts)

    def _publish(self, tmp_path, start, stats):
        if not stats or stats["count"] == 0:
            tmp_path.unlink(missing_ok=True)
            return None

        plain = None
        if tmp_path.name.endswith(".jsonl"):   # xz: compress the hour now
            plain, tmp_path = tmp_path, tmp_path.with_name(tmp_path.name + ".xz")
            _xz_file(plain, tmp_path)
        encoding = "xz" if plain else "gzip"
        checksum, size = _file_sha256(tmp_path)
        name = f"{self.file_prefix}_{start.strftime(BATCH_FORMAT)}.jsonl.{'xz' if plain else 'gz'}"
        object_path = f"{self._object_dir(start)}/{name}"
        manifest = {
            "dataset": self.dataset,
            "object_path": object_path,
            "uri": self.lake.uri(object_path),
            "record_count": stats["count"],
            "sha256": checksum,
            "size_bytes": size,
            "content_encoding": encoding,
            "format": "jsonl",
            "first_record_utc": stats["first"],
            "last_record_utc": stats["last"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self.lake.write_new_file(object_path, tmp_path, content_type=f"application/{'x-xz' if plain else 'gzip'}")
        except ObjectExistsError:
            if self.lake.sha256(object_path) != checksum:
                raise RuntimeError(f"{object_path} exists with different content; tmp kept") from None
            logger.warning("Batch %s already published; cleaning up tmp", object_path)

        try:
            self.lake.write_new(f"{object_path}.manifest.json",
                                (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
                                content_type="application/json")
        except ObjectExistsError:
            pass

        if self.lake.sha256(object_path) != checksum:
            raise RuntimeError(f"Read-back checksum mismatch for {object_path}; tmp kept")

        tmp_path.unlink()
        if plain:
            plain.unlink()
        self.published.append(manifest)
        logger.info("Published %s (%d records, %.1f MB)", manifest["uri"], stats["count"], size / 1e6)
        return manifest


def _count(stats, at):
    stats["count"] += 1
    stats["first"] = at if stats["first"] is None or at < stats["first"] else stats["first"]
    stats["last"] = at if stats["last"] is None or at > stats["last"] else stats["last"]


def _file_sha256(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _xz_file(source, target):
    """Compress a file with xz, 1 MB at a time. The output is the same for the same input, so a
    batch recompressed after a crash matches what was already uploaded."""
    compressor = lzma.LZMACompressor(preset=XZ_PRESET)
    with open(source, "rb") as src, open(target, "wb") as dst:
        for chunk in iter(lambda: src.read(CHUNK), b""):
            dst.write(compressor.compress(chunk))
        dst.write(compressor.flush())
        dst.flush()
        os.fsync(dst.fileno())


def _scan_lines(path, time_field):
    """Count complete lines of a plain JSONL tmp file (one in memory at a time) and truncate a
    final line torn by a crash."""
    stats = {"count": 0, "first": None, "last": None}
    good = 0
    with open(path, "rb") as file:
        for line in file:
            if not line.endswith(b"\n"):
                break
            try:
                record = json.loads(line)
            except ValueError:
                break
            _count(stats, record[time_field])
            good += len(line)
    if good < os.path.getsize(path):
        logger.warning("Truncating torn record at byte %d of %s", good, path.name)
        with open(path, "r+b") as file:
            file.truncate(good)
    return stats


def _scan(path, time_field):
    """Count complete records member by member (one record in memory at a time), and truncate
    a final member torn by a crash."""
    stats = {"count": 0, "first": None, "last": None}
    size = os.path.getsize(path)
    offset = 0
    while offset < size:
        end = _member_end(path, offset)
        if end is None:
            break
        with open(path, "rb") as file:
            file.seek(offset)
            record = json.loads(gzip.decompress(file.read(end - offset)))
        _count(stats, record[time_field])
        offset = end
    if offset < size:
        logger.warning("Truncating torn record at byte %d of %s", offset, path.name)
        with open(path, "r+b") as file:
            file.truncate(offset)
    return stats


def _member_end(path, start):
    """Offset just after the gzip member starting at `start`, or None if it is incomplete."""
    import zlib

    with open(path, "rb") as file:
        file.seek(start)
        header = file.read(10)
        if len(header) < 10 or header[:2] != b"\x1f\x8b":
            return None
        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
        offset = start + 10
        flags = header[3]
        if flags & 0x04:                 # FEXTRA
            xlen = int.from_bytes(file.read(2), "little")
            file.read(xlen)
            offset += 2 + xlen
        for flag in (0x08, 0x10):        # FNAME, FCOMMENT: zero-terminated
            if flags & flag:
                while True:
                    byte = file.read(1)
                    offset += 1
                    if byte in (b"\x00", b""):
                        break
        if flags & 0x02:                 # FHCRC
            file.read(2)
            offset += 2
        file.seek(offset)
        while not decompressor.eof:
            chunk = file.read(CHUNK)
            if not chunk:
                return None
            decompressor.decompress(chunk)
            if decompressor.eof:
                offset += len(chunk) - len(decompressor.unused_data)
                break
            offset += len(chunk)
        return offset + 8 if os.path.getsize(path) >= offset + 8 else None
