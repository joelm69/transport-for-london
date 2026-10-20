import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.secrets import get_tfl_api_key

BASE_URL = os.getenv(
    "TFL_API_BASE_URL",
    "https://api.tfl.gov.uk",
).rstrip("/")

RUNS_DIR = PROJECT_ROOT / "data" / "raw" / "bus_line_status_runs"
REQUEST_TIMEOUT = 60


def fetch_line_status(api_key):
    """Fetch current status information for bus lines."""
    query = urllib.parse.urlencode({"app_key": api_key})
    url = f"{BASE_URL}/Line/Mode/bus/Status?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
        },
        method="GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Unexpected HTTP status: {response.status}"
            )
        payload = response.read().decode("utf-8")

    return json.loads(payload)


def transform_line_status(lines, collected_at, collection_run_id):
    """Create one record per line while preserving its status details."""
    records = []

    for line in lines:
        if not isinstance(line, dict) or not line.get("id"):
            continue

        records.append({
            "collection_run_id": collection_run_id,
            "collected_at_utc": collected_at,
            "line_id": line["id"],
            "line_name": line.get("name"),
            "mode_name": line.get("modeName"),
            "line_statuses": line.get("lineStatuses", []),
        })

    return records


def save_run(records, collected_at, collection_run_id):
    """Save this snapshot to a new file without overwriting prior runs."""
    if not records:
        raise ValueError("No valid bus-line records to save.")

    collection_date = collected_at[:10]
    output_dir = (
        RUNS_DIR
        / f"date={collection_date}"
        / f"run_id={collection_run_id}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "bus_line_status.jsonl"

    with output_file.open("x", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return output_file


def main():
    print("Starting one-time TfL bus line status collection.")

    try:
        api_key = get_tfl_api_key()
        if not api_key:
            raise ValueError("API key is empty.")

        lines = fetch_line_status(api_key)

        if not isinstance(lines, list):
            raise ValueError("TfL returned an unexpected response format.")

        collected_datetime = datetime.now(timezone.utc)
        collected_at = collected_datetime.isoformat()
        collection_run_id = collected_datetime.strftime("%Y%m%dT%H%M%S%fZ")

        records = transform_line_status(
            lines,
            collected_at,
            collection_run_id,
        )
        saved_file = save_run(
            records,
            collected_at,
            collection_run_id,
        )

    except urllib.error.HTTPError as exc:
        print(f"TfL request failed with HTTP {exc.code}. No records were saved.")
        raise SystemExit(1) from None

    except urllib.error.URLError as exc:
        print(f"Could not connect to TfL ({type(exc.reason).__name__}).")
        raise SystemExit(1) from None

    except Exception as exc:
        print(f"Collection failed ({type(exc).__name__}). Check configuration and logs.")
        raise SystemExit(1) from None

    print("\nCollection complete.")
    print(f"Collection run ID: {collection_run_id}")
    print(f"Bus lines received: {len(lines)}")
    print(f"Records saved: {len(records)}")
    print(f"Output file: {saved_file}")


if __name__ == "__main__":
    main()
