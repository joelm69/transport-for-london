"""Validate one TfL bus-arrivals run. Dry-run by default."""

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from google.cloud import storage

PROJECT_ID = "transport-for-london-511318"
BUCKET_NAME = "transport_for_london_bucket"
PROJECT_ROOT = Path("/home/joelmathooko/tfl-pipeline")
RUNS_DIR = PROJECT_ROOT / "data/raw/bus_arrivals_runs"


def load_records(source):
    records = []
    with source.open(encoding="utf-8") as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Line {number} is not a JSON object")
            records.append(record)
    return records


def validate_run(records, source):
    if not records:
        raise ValueError("No records found")

    run_ids = {r.get("collection_run_id") for r in records}
    if len(run_ids) != 1 or None in run_ids or "" in run_ids:
        raise ValueError("Missing or mixed collection run IDs")

    run_id = next(iter(run_ids))
    if (
        len(run_id) != 22
        or run_id[8] != "T"
        or run_id[-1] != "Z"
        or not run_id[9:21].isdigit()
    ):
        raise ValueError("Invalid collection run ID format")

    try:
        run_date = date(
            int(run_id[:4]), int(run_id[4:6]), int(run_id[6:8])
        ).isoformat()
    except ValueError as exc:
        raise ValueError("Invalid date in collection run ID") from exc

    timestamps = set()
    dates = set()

    for index, record in enumerate(records, 1):
        timestamp = record.get("collected_at_utc")
        if not isinstance(timestamp, str):
            raise ValueError(f"Record {index} has no UTC timestamp")

        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp on record {index}") from exc

        if parsed_timestamp.utcoffset() != timezone.utc.utcoffset(parsed_timestamp):
            raise ValueError(f"Record {index} timestamp is not UTC")

        timestamps.add(parsed_timestamp.isoformat())
        dates.add(parsed_timestamp.date().isoformat())

    if dates != {run_date}:
        raise ValueError("Record dates do not match the run ID date")

    if len(timestamps) != 1:
        raise ValueError("Records have mixed collection timestamps")

    collection_timestamp = next(iter(timestamps))

    expected = RUNS_DIR / f"date={run_date}" / f"run_id={run_id}" / "bus_arrivals.jsonl"
    if source.resolve() != expected.resolve():
        raise ValueError(f"Source must be the matching per-run file: {expected}")

    return run_date, run_id, collection_timestamp



def build_manifest(source, records, run_date, run_id, collection_timestamp,
                   gcs_uri, generation, archived_at_utc):
    """Build a manifest describing one successfully archived data object."""
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    return {
        "collection_run_id": run_id,
        "collection_date": run_date,
        "collected_at_utc": collection_timestamp,
        "record_count": len(records),
        "sha256": checksum,
        "gcs_uri": gcs_uri,
        "gcs_generation": str(generation),
        "size_bytes": source.stat().st_size,
        "content_type": "application/x-ndjson",
        "archived_at_utc": archived_at_utc,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_file", type=Path)
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload validated file; otherwise perform a dry run",
    )
    args = parser.parse_args()
    source = args.source_file.expanduser().resolve()

    print(f"Mode: {'UPLOAD REQUESTED' if args.upload else 'DRY RUN'}")
    print(f"Source: {source}")

    if not source.is_file():
        print("STOP: Source file does not exist. No upload attempted.")
        return 1

    try:
        records = load_records(source)
        run_date, run_id, collection_timestamp = validate_run(records, source)
    except (OSError, ValueError) as exc:
        print(f"STOP: {exc}. No upload attempted.")
        return 1

    destination = (
        f"raw/bus_arrivals/date={run_date}/"
        f"run_id={run_id}/bus_arrivals.jsonl"
    )
    manifest_destination = (
        f"raw/bus_arrivals/date={run_date}/"
        f"run_id={run_id}/manifest.json"
    )
    gcs_uri = f"gs://{BUCKET_NAME}/{destination}"
    manifest_uri = f"gs://{BUCKET_NAME}/{manifest_destination}"

    print(f"Validated records: {len(records)}")
    print(f"Data destination: {gcs_uri}")
    print(f"Manifest destination: {manifest_uri}")

    try:
        bucket = storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME)
        blob = bucket.blob(destination)
        manifest_blob = bucket.blob(manifest_destination)

        if blob.exists():
            print("STOP: Data destination already exists. No upload attempted.")
            return 1

        if manifest_blob.exists():
            print("STOP: Manifest destination already exists. No upload attempted.")
            return 1

        if not args.upload:
            print("DRY RUN: Both destinations are absent. No upload attempted.")
            return 0

        blob.upload_from_filename(
            str(source),
            if_generation_match=0,
            content_type="application/x-ndjson",
        )
        blob.reload()

        manifest = build_manifest(
            source=source,
            records=records,
            run_date=run_date,
            run_id=run_id,
            collection_timestamp=collection_timestamp,
            gcs_uri=gcs_uri,
            generation=blob.generation,
            archived_at_utc=datetime.now(timezone.utc).isoformat(),
        )

        manifest_blob.upload_from_string(
            json.dumps(manifest, indent=2) + "\n",
            if_generation_match=0,
            content_type="application/json",
        )

        print("Data upload and manifest creation completed.")
        print(f"Data generation: {blob.generation}")
        print(f"Manifest: {manifest_uri}")
        return 0
    except Exception as exc:
        print(f"STOP: GCS operation failed ({type(exc).__name__}).")
        print("If the data upload succeeded, it has not been deleted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
