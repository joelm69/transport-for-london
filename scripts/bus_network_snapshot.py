"""Poll every bus on the network once and store the poll's summaries in GCS.

Run every 30 minutes by the tfl-bus-network Cloud Run Job (deploy/bus-network/). Writes, for
a poll taken at e.g. 2026-10-23T18:00:12Z:

  raw/bus_network/date=2026-10-23/180012.json.gz                      slim predictions (deleted after 30 days)
  processed/bus_network/vehicles/date=2026-10-23/180012.json.gz       one row per bus
  processed/bus_network/routes/date=2026-10-23/180012.json.gz         one row per route and direction

Newline-delimited JSON, gzip; BigQuery reads the processed files as external tables
(analytics/bus_network/). Uploads are create-only, so a retried run never overwrites a poll.
The TfL key comes from TFL_API_KEY (Secret Manager on Cloud Run, .env locally).

    python scripts/bus_network_snapshot.py             # poll and upload
    python scripts/bus_network_snapshot.py --dry-run   # poll and summarise only
"""

import argparse
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.clients.tfl import TflClient  # noqa: E402
from src.collectors.bus_network import route_rows, slim, vehicle_rows  # noqa: E402

BUCKET = "transport_for_london_bucket"
UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"


def ndjson_gz(rows):
    return gzip.compress("\n".join(json.dumps(r, separators=(",", ":")) for r in rows).encode())


def upload(session, name, body):
    response = session.post(
        UPLOAD.format(bucket=BUCKET),
        params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
        # Stored as a .gz file, not with Content-Encoding: gzip: GCS decompresses such a body on
        # arrival, so the .json.gz would hold plain JSON and BigQuery fails ("Invalid gzip file").
        headers={"Content-Type": "application/gzip"},
        data=body, timeout=60,
    )
    if response.status_code == 412:
        print(f"Already stored, kept: {name}", flush=True)
        return
    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="poll and summarise; don't upload")
    args = parser.parse_args()

    # 84 MB takes about a minute to arrive; allow for slow moments, retry once.
    response = TflClient.from_environment(timeout=240, max_attempts=2).get_json("/Mode/bus/Arrivals", {"count": -1})
    taken = datetime.fromisoformat(response.fetched_at_utc.replace("Z", "+00:00"))
    snapshot_at = taken.strftime("%Y-%m-%dT%H:%M:%SZ")
    predictions = response.data
    vehicles, routes = vehicle_rows(predictions, snapshot_at), route_rows(predictions, snapshot_at)
    print(f"{snapshot_at}: {len(predictions):,} predictions, {len(vehicles):,} buses, {len(routes):,} route "
          f"directions ({response.size_bytes / 1e6:.0f} MB in {response.latency_ms / 1000:.0f} s)", flush=True)
    if args.dry_run:
        return

    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    session = AuthorizedSession(credentials)
    stamp, day = taken.strftime("%H%M%S"), taken.strftime("%Y-%m-%d")
    files = {
        f"raw/bus_network/date={day}/{stamp}.json.gz": slim(predictions),
        f"processed/bus_network/vehicles/date={day}/{stamp}.json.gz": vehicles,
        f"processed/bus_network/routes/date={day}/{stamp}.json.gz": routes,
    }
    del predictions, response   # the parsed poll is the largest thing in memory
    for name, rows in files.items():
        body = ndjson_gz(rows)
        upload(session, name, body)
        print(f"Stored {quote(name)} ({len(body) / 1e6:.2f} MB)", flush=True)


if __name__ == "__main__":
    main()
