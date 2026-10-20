"""Validate bus-arrival data and check a proposed GCS destination.

The main workflow is dry-run only. Uploads are not invoked.
The separate upload helper uses a create-only GCS precondition.
"""

import json
from collections import Counter
from pathlib import Path

from google.cloud import storage

PROJECT_ID = "transport-for-london-511318"
BUCKET_NAME = "transport_for_london_bucket"

SOURCE_FILE = Path(
    "/home/joelmathooko/tfl-pipeline/data/raw/bus_arrivals.jsonl"
)

COLLECTION_DATE = "2026-10-14"
COLLECTION_RUN_ID = "20261014T213308324569Z"

DESTINATION_OBJECT = (
    f"raw/bus_arrivals/date={COLLECTION_DATE}/"
    f"run_id={COLLECTION_RUN_ID}/bus_arrivals.jsonl"
)
def upload_new_object_only(blob, source_file):
    """Upload only if the destination object does not already exist."""
    blob.upload_from_filename(
        str(source_file),
        if_generation_match=0,
    )

def load_records():
    records = []

    with SOURCE_FILE.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number}"
                ) from exc

    return records


def main():
    print("BUS ARRIVALS ARCHIVE — DRY RUN")
    print("-" * 40)

    if not SOURCE_FILE.is_file():
        print(f"STOP: Source file not found: {SOURCE_FILE}")
        return

    try:
        records = load_records()
    except ValueError as exc:
        print(f"STOP: {exc}")
        return

    if not records:
        print("STOP: No records found.")
        return

    run_counts = Counter(
        record.get("collection_run_id") or "LEGACY"
        for record in records
    )

    print(f"Source records: {len(records)}")
    print(f"Collection groups: {len(run_counts)}")

    for run_id, count in sorted(run_counts.items()):
        print(f"  {run_id}: {count} records")

    if len(run_counts) != 1:
        print("STOP: Mixed collection history detected.")
        print("No GCS check or upload attempted.")
        return

    actual_run_id = next(iter(run_counts))

    if actual_run_id != COLLECTION_RUN_ID:
        print("STOP: Source run does not match the configured run ID.")
        print("No GCS check or upload attempted.")
        return

    print(f"\nProposed destination: gs://{BUCKET_NAME}/{DESTINATION_OBJECT}")

    try:
        client = storage.Client(project=PROJECT_ID)
        blob = client.bucket(BUCKET_NAME).blob(DESTINATION_OBJECT)

        if blob.exists():
            print("STOP: Destination already exists.")
            print("No upload attempted.")
        else:
            print("Destination does not exist.")
            print("DRY RUN ONLY: no upload attempted.")

    except Exception as exc:
        print(f"STOP: Could not check GCS destination ({type(exc).__name__}).")
        print("No upload attempted.")


if __name__ == "__main__":
    main()