"""Safely create a manifest for an already archived TfL bus-arrivals run."""

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage

from archive_bus_arrivals import (
    BUCKET_NAME,
    PROJECT_ID,
    build_manifest,
    load_records,
    validate_run,
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(
        description="Create a manifest for an existing, verified GCS data object."
    )
    parser.add_argument("source_file", type=Path)
    parser.add_argument("--expected-generation", required=True)
    parser.add_argument(
        "--create", action="store_true",
        help="Create the manifest in GCS (default: verify and preview only).",
    )
    args = parser.parse_args()

    source = args.source_file.expanduser().resolve()
    if not source.is_file():
        raise SystemExit("STOP: Local source file does not exist.")

    try:
        records = load_records(source)
        run_date, run_id, collection_timestamp = validate_run(records, source)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"STOP: Local run validation failed: {exc}")

    object_name = (
        f"raw/bus_arrivals/date={run_date}/"
        f"run_id={run_id}/bus_arrivals.jsonl"
    )
    manifest_name = (
        f"raw/bus_arrivals/date={run_date}/"
        f"run_id={run_id}/manifest.json"
    )
    gcs_uri = f"gs://{BUCKET_NAME}/{object_name}"

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    data_blob = bucket.blob(object_name)
    manifest_blob = bucket.blob(manifest_name)

    if manifest_blob.exists():
        raise SystemExit("STOP: Manifest already exists. Nothing changed.")

    try:
        data_blob.reload()
    except Exception as exc:
        raise SystemExit(
            f"STOP: Could not retrieve GCS object metadata ({type(exc).__name__})."
        )

    if str(data_blob.generation) != args.expected_generation:
        raise SystemExit("STOP: GCS generation differs from the expected generation.")

    if data_blob.size != source.stat().st_size:
        raise SystemExit("STOP: Local and GCS object sizes differ.")

    try:
        with tempfile.TemporaryDirectory(prefix="tfl_manifest_check_") as temp_dir:
            cloud_copy = Path(temp_dir) / "bus_arrivals.jsonl"
            data_blob.download_to_filename(
                str(cloud_copy),
                if_generation_match=int(args.expected_generation),
            )
            local_hash = sha256_file(source)
            cloud_hash = sha256_file(cloud_copy)
    except Exception as exc:
        raise SystemExit(
            f"STOP: Could not verify the GCS data copy ({type(exc).__name__})."
        )

    if local_hash != cloud_hash:
        raise SystemExit("STOP: SHA-256 mismatch. Manifest not created.")

    manifest = build_manifest(
        source=source,
        records=records,
        run_date=run_date,
        run_id=run_id,
        collection_timestamp=collection_timestamp,
        gcs_uri=gcs_uri,
        generation=data_blob.generation,
        archived_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    manifest["sha256"] = cloud_hash

    if not args.create:
        print("DRY RUN: Verification passed. No GCS objects were changed.")
        print(f"Run ID: {run_id}")
        print(f"Verified records: {len(records)}")
        print(f"Verified SHA-256: {cloud_hash}")
        print(f"Data generation: {data_blob.generation}")
        print(f"Would create: gs://{BUCKET_NAME}/{manifest_name}")
        print("Use --create only when you explicitly want to write the manifest.")
        print(json.dumps(manifest, indent=2))
        return

    try:
        manifest_blob.upload_from_string(
            json.dumps(manifest, indent=2) + "\n",
            if_generation_match=0,
            content_type="application/json",
        )
    except Exception as exc:
        raise SystemExit(
            f"STOP: Manifest creation failed ({type(exc).__name__}). "
            "The existing data object was not modified."
        )

    print("Manifest created successfully.")
    print(f"Run ID: {run_id}")
    print(f"Verified records: {len(records)}")
    print(f"Verified SHA-256: {cloud_hash}")
    print(f"Data generation: {data_blob.generation}")
    print(f"Manifest: gs://{BUCKET_NAME}/{manifest_name}")


if __name__ == "__main__":
    main()
