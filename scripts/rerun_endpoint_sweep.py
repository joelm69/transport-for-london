"""Re-test every catalogued TfL endpoint with endpoint-appropriate inputs.

The first sweep (reports/tfl_api_test_results.json) reused generic sample
IDs such as "BikePoints_1" for every {id}, so many 404s were input errors.
This sweep resolves each path parameter from the endpoint's own domain and
looks up volatile IDs (vehicles, road disruptions) live before testing.

Read-only: GET requests only. The API key is never written to the report.

Usage:
    python scripts/rerun_endpoint_sweep.py            # sweep and write report
    python scripts/rerun_endpoint_sweep.py --update-catalogue
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASE_URL = os.getenv("TFL_API_BASE_URL", "https://api.tfl.gov.uk").rstrip("/")
CATALOGUE_FILE = PROJECT_ROOT / "config" / "tfl_api_catalogue.csv"
REPORT_DIR = PROJECT_ROOT / "reports"
TIMEOUT = 60
DELAY_SECONDS = 0.3

# Known-good reference IDs, taken from data the project already collects.
TUBE_LINE = "victoria"
TUBE_STOP = "940GZZLUVIC"        # Victoria Underground Station
TUBE_STOP_TO = "940GZZLUBXN"     # Brixton Underground Station
BUS_STOP = "490000200E"          # Russell Square, used by bus_arrivals.py
ELIZABETH_STOP = "910GLIVST"     # Liverpool Street (Elizabeth line)
ROAD = "a2"
BIKE_POINT = "BikePoints_1"
LAT, LON = "51.5074", "-0.1278"  # Trafalgar Square

TODAY = datetime.now(timezone.utc).date()
START_DATE = TODAY.isoformat()
END_DATE = (TODAY + timedelta(days=1)).isoformat()


def load_api_key():
    """Read TFL_API_KEY from the environment or the project .env file."""
    key = os.getenv("TFL_API_KEY")
    env_file = PROJECT_ROOT / ".env"

    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("TFL_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not key:
        raise SystemExit("TFL_API_KEY is missing from the environment and .env")

    return key


def get(path, params, api_key):
    """Perform one GET request. Returns (status, body_bytes, content_type)."""
    query = {**params, "app_key": api_key}
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return (
                response.status,
                response.read(),
                response.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def lookup_volatile_ids(api_key):
    """Fetch IDs that change over time so tests use values that exist now."""
    ids = {"vehicle": None, "road_disruption": None, "car_park": None}

    status, body, _ = get(f"/Line/{TUBE_LINE}/Arrivals", {}, api_key)
    if status == 200:
        arrivals = json.loads(body)
        vehicle_ids = [a.get("vehicleId") for a in arrivals if a.get("vehicleId")]
        ids["vehicle"] = vehicle_ids[0] if vehicle_ids else None

    status, body, _ = get("/Road/all/Disruption", {}, api_key)
    if status == 200:
        disruptions = json.loads(body)
        if disruptions:
            ids["road_disruption"] = disruptions[0].get("id")

    status, body, _ = get("/Occupancy/CarPark", {}, api_key)
    if status == 200:
        car_parks = json.loads(body)
        if car_parks:
            ids["car_park"] = car_parks[0].get("id")

    return ids


def path_values(path, volatile):
    """Choose path-parameter values that fit the endpoint's domain."""
    domain = path.split("/")[1].lower()

    by_domain_id = {
        "bikepoint": BIKE_POINT,
        "line": TUBE_LINE,
        "stoppoint": TUBE_STOP,
        "road": ROAD,
        "place": BIKE_POINT,
        "vehicle": volatile["vehicle"],
        "occupancy": volatile["car_park"],
    }

    values = {
        "id": by_domain_id.get(domain),
        "ids": by_domain_id.get(domain),
        "stopPointId": TUBE_STOP,
        "fromStopPointId": TUBE_STOP,
        "toStopPointId": TUBE_STOP_TO,
        "lineId": TUBE_LINE,
        "line": TUBE_LINE,
        "direction": "inbound",
        "mode": "tube",
        "modes": "tube",
        "severity": "10",
        "StartDate": START_DATE,
        "EndDate": END_DATE,
        "query": "victoria",
        "disruptionIds": volatile["road_disruption"],
        "types": "NaptanMetroStation" if domain == "stoppoint" else "BikePoint",
        "type": "BikePoint",
        "page": "1",
        "Postcode": "SW1A 1AA",
        "Lat": LAT,
        "Lon": LON,
        "z": "12",
        "width": "256",
        "height": "256",
        "from": TUBE_STOP,
        "to": TUBE_STOP_TO,
        "year": "2019",
    }

    # Endpoint-specific overrides where the generic choice is wrong.
    if path.startswith("/Occupancy/BikePoints"):
        values["ids"] = BIKE_POINT
    if path.startswith("/StopPoint/{id}/ArrivalDepartures"):
        values["id"] = ELIZABETH_STOP
    if path.startswith("/StopPoint/Sms"):
        values["id"] = "73241"  # Unverified SMS code; TfL docs example format.

    return values


# Query parameters the endpoint needs to return data. Optional parameters
# are omitted so each test exercises the endpoint's default behaviour.
QUERY_PARAMS = {
    "/Cabwise/search": {"lat": LAT, "lon": LON},
    "/Place": {"lat": LAT, "lon": LON, "radius": "200"},
    "/Place/Search": {"name": "Victoria"},
    "/Place/Address/Streets/{Postcode}": {"postcode": "SW1A 1AA"},
    "/Place/{type}/At/{Lat}/{Lon}": {"lat": LAT, "lon": LON},
    "/Place/{type}/overlay/{z}/{Lat}/{Lon}/{width}/{height}": {
        "lat": LAT, "lon": LON,
    },
    "/StopPoint": {
        "stopTypes": "NaptanMetroStation",
        "lat": LAT, "lon": LON, "radius": "1000",
    },
    "/StopPoint/{id}/ArrivalDepartures": {"lineIds": "elizabeth"},
    "/StopPoint/{id}/placeTypes": {"placeTypes": "TaxiRank"},
    "/StopPoint/ServiceTypes": {"id": TUBE_STOP, "lineIds": TUBE_LINE},
    "/BikePoint/Search": {"query": "Waterloo"},
    "/Search": {"query": "victoria"},
    "/Search/BusSchedules": {"query": "24"},
    "/StopPoint/Search": {"query": "victoria"},
    "/Line/{ids}/Status/{StartDate}/to/{EndDate}": {
        "startDate": START_DATE, "endDate": END_DATE,
    },
    "/Road/all/Street/Disruption": {
        "startDate": START_DATE, "endDate": END_DATE,
    },
    "/Mode/{mode}/Arrivals": {"count": "5"},
}


def classify(status):
    if 200 <= status < 300:
        return "PASS"
    if status == 300:
        return "AMBIGUOUS_INPUT"
    if status in (401, 403):
        return "AUTH_ERROR"
    if status == 404:
        return "NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if status in (400, 422):
        return "INVALID_INPUT"
    return "HTTP_ERROR"


def describe_payload(body, content_type):
    """Summarise the response shape without storing the response itself."""
    if "json" not in content_type:
        return {"shape": content_type or "unknown"}

    try:
        payload = json.loads(body)
    except ValueError:
        return {"shape": "invalid_json"}

    if isinstance(payload, list):
        return {"shape": "array", "records": len(payload)}
    if isinstance(payload, dict):
        return {"shape": "object", "top_level_fields": sorted(payload)[:15]}
    return {"shape": type(payload).__name__}


def run_sweep(api_key):
    with CATALOGUE_FILE.open(newline="", encoding="utf-8") as file:
        catalogue = list(csv.DictReader(file))

    print("Looking up volatile IDs (vehicle, road disruption, car park)...")
    volatile = lookup_volatile_ids(api_key)
    print(f"  {volatile}\n")

    results = []

    for index, row in enumerate(catalogue, 1):
        path_template = row["path"]
        result = {
            "path": path_template,
            "previous_catalogue_status": row["status"],
            "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        values = path_values(path_template, volatile)
        placeholders = re.findall(r"\{([^{}]+)\}", path_template)
        missing = [name for name in placeholders if not values.get(name)]

        if missing:
            result.update({
                "result": "NO_SAMPLE_VALUE",
                "detail": "No value available for: " + ", ".join(missing),
            })
            results.append(result)
            print(f"[{index}/{len(catalogue)}] NO SAMPLE  {path_template}")
            continue

        path = path_template
        for name in placeholders:
            path = path.replace(
                "{" + name + "}", urllib.parse.quote(values[name], safe=",")
            )

        params = QUERY_PARAMS.get(path_template, {})
        start = time.monotonic()

        try:
            status, body, content_type = get(path, params, api_key)
            result.update({
                "tested_path": path,
                "query_params": params,
                "http_status": status,
                "result": classify(status),
                "response_bytes": len(body),
                **describe_payload(body, content_type),
            })
        except Exception as exc:
            result.update({
                "tested_path": path,
                "query_params": params,
                "result": "ERROR",
                "detail": type(exc).__name__,
            })

        result["duration_ms"] = round((time.monotonic() - start) * 1000)
        results.append(result)

        print(
            f"[{index}/{len(catalogue)}] {result['result']:16} "
            f"{str(result.get('http_status', '-')):>4} {path_template}"
        )
        time.sleep(DELAY_SECONDS)

    return results


def update_catalogue(results):
    """Write each endpoint's sweep result into the catalogue status column.

    Endpoints already marked RETIRED keep that status.
    """
    with CATALOGUE_FILE.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        rows = list(reader)

    by_path = {result["path"]: result["result"] for result in results}
    changed = 0

    for row in rows:
        if row["status"] == "RETIRED":
            continue
        new_status = by_path.get(row["path"])
        if new_status and new_status != row["status"]:
            row["status"] = new_status
            changed += 1

    temporary_file = CATALOGUE_FILE.with_suffix(".csv.tmp")
    with temporary_file.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_file.replace(CATALOGUE_FILE)

    print(f"Catalogue rows updated: {changed}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update-catalogue",
        action="store_true",
        help="Write results into config/tfl_api_catalogue.csv",
    )
    args = parser.parse_args()

    results = run_sweep(load_api_key())

    counts = {}
    for result in results:
        counts[result["result"]] = counts.get(result["result"], 0) + 1

    report_file = REPORT_DIR / f"tfl_api_sweep_{TODAY:%Y%m%d}.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "operation_count": len(results),
        "results_summary": counts,
        "operations": results,
    }, indent=2))

    print("\n--- SUMMARY ---")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"Report: {report_file.relative_to(PROJECT_ROOT)}")

    if args.update_catalogue:
        update_catalogue(results)


if __name__ == "__main__":
    main()
