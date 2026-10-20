import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------
# 1. Project configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.secrets import get_tfl_api_key


BASE_URL = os.getenv(
    "TFL_API_BASE_URL",
    "https://api.tfl.gov.uk",
).rstrip("/")

STOP_ID = "490000200E"  # Russell Square
OUTPUT_FILE = PROJECT_ROOT / "data" / "raw" / "bus_arrivals.jsonl"
RUNS_DIR = PROJECT_ROOT / "data" / "raw" / "bus_arrivals_runs"

REQUEST_TIMEOUT = 60


# --------------------------------------------------
# 2. Fetch live arrivals securely
# --------------------------------------------------

def fetch_arrivals(api_key):
    """Fetch one snapshot of live arrivals for the configured stop."""

    url = (
        f"{BASE_URL}/StopPoint/{STOP_ID}/Arrivals?"
        f"{urllib.parse.urlencode({'app_key': api_key})}"
    )

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


# --------------------------------------------------
# 3. Validate and deduplicate one API response
# --------------------------------------------------

def transform_arrivals(arrivals, collected_at, collection_run_id):
    """
    Convert API results into consistent records.

    Duplicate prediction IDs are removed within this snapshot only.
    Later snapshots are preserved for time-series analysis.
    """

    records_by_id = {}

    fields = {
        "line_id": "lineId",
        "line_name": "lineName",
        "platform_name": "platformName",
        "direction": "direction",
        "destination_name": "destinationName",
        "mode_name": "modeName",
        "station_name": "stationName",
        "time_to_station_seconds": "timeToStation",
        "expected_arrival": "expectedArrival",
        "time_to_live": "timeToLive",
        "towards": "towards",
        "bearing": "bearing",
        "vehicle_id": "vehicleId",
        "source_system": "sourceSystem",
        "timing": "timing",
    }

    skipped_records = 0

    for arrival in arrivals:
        if not isinstance(arrival, dict):
            skipped_records += 1
            continue

        prediction_id = arrival.get("id")

        if prediction_id is None or str(prediction_id).strip() == "":
            skipped_records += 1
            continue

        prediction_id = str(prediction_id)

        record = {
            "collection_run_id": collection_run_id,
            "stop_id": STOP_ID,
            "collected_at_utc": collected_at,
            "prediction_id": prediction_id,
        }

        for output_field, api_field in fields.items():
            record[output_field] = arrival.get(api_field)

        record["naptan_id"] = arrival.get("naptanId")

        # Keep one occurrence of each prediction ID per snapshot.
        records_by_id.setdefault(prediction_id, record)

    return list(records_by_id.values()), skipped_records


# --------------------------------------------------
# 4. Append valid records to JSON Lines storage
# --------------------------------------------------
def save_arrivals(records):
    """
    Save one collection run to its own JSONL file.

    Existing files are never overwritten.
    Historical observations in OUTPUT_FILE remain untouched.
    """

    if not records:
        print("No valid arrival records to save.")
        return None

    # Confirm this file contains records from exactly one run.
    run_ids = {
        record.get("collection_run_id")
        for record in records
    }

    if len(run_ids) != 1 or None in run_ids:
        raise ValueError(
            "Records must belong to exactly one collection run."
        )

    run_id = next(iter(run_ids))

    # Derive the collection date from the UTC timestamp.
    collection_dates = {
        record.get("collected_at_utc", "")[:10]
        for record in records
    }

    if len(collection_dates) != 1 or "" in collection_dates:
        raise ValueError(
            "Records must have valid timestamps from one UTC date."
        )

    collection_date = next(iter(collection_dates))

    destination = (
        RUNS_DIR
        / f"date={collection_date}"
        / f"run_id={run_id}"
        / "bus_arrivals.jsonl"
    )

    output_text = "".join(
        json.dumps(record, ensure_ascii=False) + "\n"
        for record in records
    )

    destination.parent.mkdir(parents=True, exist_ok=True)

    # Exclusive creation: fail rather than overwrite an existing file.
    with destination.open("x", encoding="utf-8") as output_file:
        output_file.write(output_text)

    print(f"Saved {len(records)} records to: {destination}")
    return destination


# --------------------------------------------------
# 5. Run one collection
# --------------------------------------------------

def main():
    print("Starting one-time TfL arrivals collection.")
    print(f"Stop ID: {STOP_ID}")

    try:
        # Retrieve the API key from Secret Manager unless an
        # environment variable is already configured.
        api_key = get_tfl_api_key()

        if not api_key:
            raise ValueError("API key is empty.")

        arrivals = fetch_arrivals(api_key)

        if not isinstance(arrivals, list):
            raise ValueError("TfL returned an unexpected response format.")

        collected_datetime = datetime.now(timezone.utc)
        collected_at = collected_datetime.isoformat()
        collection_run_id = collected_datetime.strftime("%Y%m%dT%H%M%S%fZ")

        records, skipped = transform_arrivals(
            arrivals,
            collected_at,
            collection_run_id,
        )

        saved_file = save_arrivals(records)

    except urllib.error.HTTPError as exc:
        print(
            f"TfL request failed with HTTP {exc.code}. "
            "No records were saved."
        )
        raise SystemExit(1) from None

    except urllib.error.URLError as exc:
        print(
            f"Could not connect to TfL ({type(exc.reason).__name__}). "
            "No records were saved."
        )
        raise SystemExit(1) from None

    except Exception as exc:
        # Avoid printing request URLs or credentials.
        print(
            f"Collection failed ({type(exc).__name__}). "
            "Check the configuration and logs."
        )
        raise SystemExit(1) from None

    print("\nCollection complete.")
    print(f"Collection run ID: {collection_run_id}")
    print(f"API predictions received: {len(arrivals)}")
    print(f"Unique valid predictions: {len(records)}")
    print(f"Invalid records skipped: {skipped}")
    print(f"Output file: {saved_file or 'No file created'}")


if __name__ == "__main__":
    main()