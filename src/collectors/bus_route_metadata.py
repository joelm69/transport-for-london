
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.clients.secrets import get_tfl_api_key


PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATUS_FILE = (
    PROJECT_ROOT
    / "data/raw/bus_line_status_runs/date=2026-10-15"
    / "run_id=20261015T194811052221Z"
    / "bus_line_status.jsonl"
)

OUTPUT_ROOT = PROJECT_ROOT / "data/raw/bus_route_metadata_runs"

BASE_URL = os.getenv(
    "TFL_API_BASE_URL", "https://api.tfl.gov.uk"
).rstrip("/")

BATCH_SIZE = 20
REQUEST_DELAY_SECONDS = 1.3


def get_line_ids():
    """Read the unique bus line IDs from our existing status collection."""
    line_ids = set()

    with STATUS_FILE.open(encoding="utf-8") as file:
        for row in file:
            if row.strip():
                record = json.loads(row)
                line_id = record.get("line_id")

                if line_id is not None:
                    line_ids.add(str(line_id))

    return sorted(line_ids, key=lambda value: (not value.isdigit(), value))


def fetch_routes(line_ids, api_key):
    """Fetch route metadata for a batch of bus lines."""
    encoded_ids = ",".join(
        urllib.parse.quote(line_id, safe="")
        for line_id in line_ids
    )

    query = urllib.parse.urlencode({
        "app_key": api_key,
        "serviceTypes": "Regular",
    })

    url = f"{BASE_URL}/Line/{encoded_ids}/Route?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    line_ids = get_line_ids()

    if not line_ids:
        raise RuntimeError("No line IDs found in the existing status file.")

    run_time = datetime.now(timezone.utc)
    run_id = run_time.strftime("%Y%m%dT%H%M%S%fZ")
    collected_at = run_time.isoformat()

    output_dir = (
        OUTPUT_ROOT
        / f"date={run_time:%Y-%m-%d}"
        / f"run_id={run_id}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    output_file = output_dir / "bus_route_metadata.jsonl"

    api_key = get_tfl_api_key()
    seen_line_ids = set()
    route_count = 0
    batch_count = 0

    print(f"Lines to collect: {len(line_ids)}")
    print(f"Collection run: {run_id}")

    with output_file.open("w", encoding="utf-8") as file:
        for start in range(0, len(line_ids), BATCH_SIZE):
            batch = line_ids[start:start + BATCH_SIZE]
            results = fetch_routes(batch, api_key)
            batch_count += 1

            for line in results:
                line_id = str(line.get("id", ""))

                if not line_id or line_id in seen_line_ids:
                    continue

                seen_line_ids.add(line_id)
                routes = line.get("routeSections") or []
                route_count += len(routes)

                record = {
                    "collection_run_id": run_id,
                    "collected_at_utc": collected_at,
                    "source_endpoint": "/Line/{ids}/Route",
                    "line_id": line_id,
                    "route_count": len(routes),
                    "response": line,
                }

                file.write(json.dumps(record, ensure_ascii=False) + "\n")

            print(
                f"Batch {batch_count}: "
                f"{len(batch)} requested, "
                f"{len(seen_line_ids)} unique lines collected"
            )

            if start + BATCH_SIZE < len(line_ids):
                time.sleep(REQUEST_DELAY_SECONDS)

    missing_ids = sorted(set(line_ids) - seen_line_ids)

    if missing_ids:
        print(f"WARNING: {len(missing_ids)} lines were not returned.")
        print("Missing line IDs:", ", ".join(missing_ids))

    print(f"Unique lines collected: {len(seen_line_ids)}")
    print(f"Route entries collected: {route_count}")
    print(f"Output file: {output_file}")
    print("Collection finished. Archiving has not been performed.")


if __name__ == "__main__":
    main()
