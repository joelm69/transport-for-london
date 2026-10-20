import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

STOP_ID = "490000200E"


def main():
    if not API_KEY:
        raise SystemExit("TFL_API_KEY is missing from .env.")

    params = {"app_key": API_KEY}
    url = (
        f"{BASE_URL}/StopPoint/"
        f"{urllib.parse.quote(STOP_ID, safe='')}/Arrivals?"
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

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            print("HTTP status:", response.status)
    except urllib.error.HTTPError as exc:
        print("HTTP status:", exc.code)
        print("The arrivals request failed. Check the endpoint and API access.")
        return
    except Exception as exc:
        print("Request failed:", type(exc).__name__)
        return

    if not isinstance(payload, list):
        print("Unexpected response type:", type(payload).__name__)
        print(json.dumps(payload, indent=2)[:2000])
        return

    print("Arrival predictions returned:", len(payload))

    if payload:
        first = payload[0]
        fields = [
            "id",
            "stationName",
            "platformName",
            "lineName",
            "destinationName",
            "expectedArrival",
            "timeToStation",
        ]

        print("\nFirst arrival prediction:")
        print(json.dumps(
            {field: first.get(field) for field in fields},
            indent=2,
        ))
    else:
        print("No arrival predictions were returned for this stop right now.")


if __name__ == "__main__":
    main()