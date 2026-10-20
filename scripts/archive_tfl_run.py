"""Safely archive one TfL endpoint run to GCS with a manifest."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage

PROJECT_ID = "transport-for-london-511318"
BUCKET_NAME = "transport_for_london_bucket"
PROJECT_ROOT = Path("/home/joelmathooko/tfl-pipeline")

RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{12}Z$")


def load_records(source):
    """Read a JSONL file and require every nonblank line to be an object."""
    records = []

    with source.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Line {line_number} is not a JSON object"
                )

            records.append(record)

    return records


def validate_run(endpoint, source, records):
    """Validate the shared metadata and expected endpoint file layout."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", endpoint):
        raise ValueError("Endpoint must use lowercase letters, digits, underscores")

    if not records:
        raise ValueError("No records found")

    run_ids = {record.get("collection_run_id") for record in records}

    if len(run_ids) != 1 or None in run_ids or "" in run_ids:
        raise ValueError("Missing or mixed collection run IDs")

    run_id = next(iter(run_ids))

    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid collection run ID format")

    try:
        run_datetime = datetime.strptime(run_id, "%Y%m%dT%H%M%S%fZ")
    except ValueError as exc:
        raise ValueError("Invalid date or time in run ID") from exc

    run_date = run_datetime.date().isoformat()
    timestamps = set()

    for index, record in enumerate(records, 1):
        timestamp = record.get("collected_at_utc")

        if not isinstance(timestamp, str):
            raise ValueError(
                f"Record {index} has no collected_at_utc timestamp"
            )

        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid timestamp on record {index}"
            ) from exc

        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError(f"Record {index} timestamp is not UTC")

        if parsed.date().isoformat() != run_date:
            raise ValueError(
                f"Record {index} date does not match the run ID"
            )

        timestamps.add(parsed)

    if len(timestamps) != 1:
        raise ValueError("Records have mixed collection timestamps")

    expected_source = (
        PROJECT_ROOT
        / "data"
        / "raw"
        / f"{endpoint}_runs"
        / f"date={run_date}"
        / f"run_id={run_id}"
        / f"{endpoint}.jsonl"
    )

    if source.resolve() != expected_source.resolve():
        raise ValueError(
            f"Source path does not match the endpoint and run ID. "
            f"Expected: {expected_source}"
        )

    return run_date, run_id, next(iter(timestamps)).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Validate and archive one TfL endpoint run."
    )
    parser.add_argument("endpoint", help="Endpoint name, e.g. bus_arrivals")
    parser.add_argument("source_file", type=Path)
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload data and manifest; default is dry-run only",
    )
    args = parser.parse_args()

    source = args.source_file.expanduser().resolve()

    print(f"Mode: {'UPLOAD REQUESTED' if args.upload else 'DRY RUN'}")
    print(f"Endpoint: {args.endpoint}")
    print(f"Source: {source}")

    if not source.is_file():
        raise SystemExit("STOP: Source file does not exist.")

    try:
        records = load_records(source)
        run_date, run_id, collected_at = validate_run(
            args.endpoint, source, records
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"STOP: Validation failed: {exc}")

    object_name = (
        f"raw/{args.endpoint}/date={run_date}/"
        f"run_id={run_id}/{args.endpoint}.jsonl"
    )
    manifest_name = (
        f"raw/{args.endpoint}/date={run_date}/"
        f"run_id={run_id}/manifest.json"
    )
    gcs_uri = f"gs://{BUCKET_NAME}/{object_name}"
    manifest_uri = f"gs://{BUCKET_NAME}/{manifest_name}"
    checksum = sha256_file(source)
    size_bytes = source.stat().st_size

    print(f"Validated records: {len(records)}")
    print(f"SHA-256: {checksum}")
    print(f"Data destination: {gcs_uri}")
    print(f"Manifest destination: {manifest_uri}")

    try:
        bucket = storage.Client(project=PROJECT_ID).bucket(BUCKET_NAME)
        data_blob = bucket.blob(object_name)
        manifest_blob = bucket.blob(manifest_name)

        if data_blob.exists():
            raise SystemExit(
                "STOP: Data destination already exists. Nothing uploaded."
            )

        if manifest_blob.exists():
            raise SystemExit(
                "STOP: Manifest destination already exists. Nothing uploaded."
            )

        if not args.upload:
            print("DRY RUN: Destinations are absent. No GCS objects changed.")
            return

        data_blob.upload_from_filename(
            str(source),
            if_generation_match=0,
            content_type="application/x-ndjson",
        )
        data_blob.reload()

        manifest = {
            "endpoint_name": args.endpoint,
            "collection_run_id": run_id,
            "collection_date": run_date,
            "collected_at_utc": collected_at,
            "record_count": len(records),
            "sha256": checksum,
            "gcs_uri": gcs_uri,
            "gcs_generation": str(data_blob.generation),
            "size_bytes": size_bytes,
            "content_type": "application/x-ndjson",
            "archived_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        manifest_blob.upload_from_string(
            json.dumps(manifest, indent=2) + "\n",
            if_generation_match=0,
            content_type="application/json",
        )

        print("Data upload and manifest creation completed.")
        print(f"Data generation: {data_blob.generation}")
        print(f"Manifest: {manifest_uri}")

    except SystemExit:
        raise
    except Exception as exc:
        print(f"STOP: GCS operation failed ({type(exc).__name__}).")
        print("If data upload succeeded, it has not been deleted.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
