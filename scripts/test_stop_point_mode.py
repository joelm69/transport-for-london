
import os
import urllib.parse
import urllib.request
import urllib.error
import json
from pathlib import Path

for line in Path(".env").read_text().splitlines():
    if line.strip().startswith("TFL_API_KEY="):
        os.environ["TFL_API_KEY"] = (
            line.split("=", 1)[1].strip().strip('"').strip("'")
        )

key = os.environ.get("TFL_API_KEY")
if not key:
    raise SystemExit("TFL_API_KEY is missing from .env")

url = "https://api.tfl.gov.uk/StopPoint/Mode/bus?" + urllib.parse.urlencode(
    {"app_key": key, "page": 2}
)

request = urllib.request.Request(
    url,
    headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
    },
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print("HTTP status:", response.status)
        data = json.loads(response.read().decode("utf-8"))

        print("Top-level fields:", list(data.keys()))
        print("Total stop points on this page:", len(data.get("stopPoints", [])))
        if data.get("stopPoints"):
            print("First stop ID:", data["stopPoints"][0].get("naptanId"))

        for field, value in data.items():
            if field != "stopPoints":
                print(f"{field}: {value}")
except urllib.error.HTTPError as exc:
    print("HTTP status:", exc.code)
    print("Response body:")
    print(exc.read(1500).decode("utf-8", errors="replace"))
except Exception as exc:
    print("Test failed:", type(exc).__name__, str(exc))