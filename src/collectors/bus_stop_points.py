import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
def load_project_env():
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


load_project_env()

BASE_URL = os.getenv(
    "TFL_API_BASE_URL",
    "https://api.tfl.gov.uk",
).rstrip("/")
API_KEY = os.getenv("TFL_API_KEY")
OUTPUT_FILE = PROJECT_ROOT / "data" / "raw" / "bus_stop_points.json"
PAGE_SIZE = 1000
DELAY_SECONDS = 0.3


def fetch_page(page_number):
    params = {
        "app_key": API_KEY,
        "page": page_number,
    }
    url = (
        f"{BASE_URL}/StopPoint/Mode/bus?"
        f"{urllib.parse.urlencode(params)}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
        },
        method="GET",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    if not API_KEY:
        raise SystemExit("TFL_API_KEY is missing from the project .env file.")

    all_stops = {}
    expected_total = None
    page_number = 1

    while True:
        print(f"Fetching page {page_number}...")

        try:
            payload = fetch_page(page_number)
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"TfL returned HTTP {exc.code} on page {page_number}. "
                "Collection stopped; no incomplete dataset was saved."
            ) from None
        except Exception as exc:
            raise SystemExit(
                f"Request failed on page {page_number}: "
                f"{type(exc).__name__}. Collection stopped."
            ) from None

        stops = payload.get("stopPoints", [])
        reported_total = payload.get("total")
        reported_page = payload.get("page")
        reported_page_size = payload.get("pageSize")

        if expected_total is None:
            expected_total = reported_total
            print(
                f"TfL reports {expected_total} stop points; "
                f"page size is {reported_page_size}."
            )

        if reported_page != page_number:
            raise SystemExit(
                f"Unexpected page number: requested {page_number}, "
                f"received {reported_page}."
            )

        if reported_total != expected_total:
            raise SystemExit(
                "TfL's reported total changed during collection. "
                "Stopped to avoid saving an inconsistent snapshot."
            )

        if not stops:
            break

        duplicates_this_page = 0

        for stop in stops:
            stop_id = stop.get("naptanId") or stop.get("id")
            if not stop_id:
                continue

            if stop_id in all_stops:
                duplicates_this_page += 1

            # Use the stable stop ID as the key, preventing duplicate rows.
            all_stops[stop_id] = stop

        print(
            f"  Received {len(stops)} records; "
            f"unique stops so far: {len(all_stops)}; "
            f"overlapping IDs: {duplicates_this_page}"
        )

        if len(stops) < PAGE_SIZE:
            break

        page_number += 1
        time.sleep(DELAY_SECONDS)

    if len(all_stops) != expected_total:
        raise SystemExit(
            f"Count mismatch: TfL reported {expected_total}, "
            f"but collected {len(all_stops)} unique stop IDs. "
            "No final dataset was saved."
        )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "source": "TfL Unified API",
        "endpoint": "/StopPoint/Mode/bus",
        "collected_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
        "reported_total": expected_total,
        "unique_stop_count": len(all_stops),
        "page_size": PAGE_SIZE,
        "pages_fetched": page_number,
        "stopPoints": list(all_stops.values()),
    }

    temporary_file = OUTPUT_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(json.dumps(output, indent=2))
    temporary_file.replace(OUTPUT_FILE)

    print("\nCollection complete.")
    print(f"Pages fetched: {page_number}")
    print(f"Unique stops: {len(all_stops)}")
    print(f"Saved to: {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
