"""Measure how often each working TfL endpoint's data actually changes.

Polls every endpoint that passed the latest sweep several times and records:
- TfL's Cache-Control max-age (how long TfL says a response stays fresh)
- whether the response content changed between consecutive polls, after
  removing fields that change on every request regardless of the data

Read-only: GET requests only. Responses are hashed, not stored.

Usage:
    python scripts/measure_endpoint_freshness.py [--rounds 4] [--gap 60]
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rerun_endpoint_sweep as sweep  # noqa: E402

PROJECT_ROOT = SCRIPTS_DIR.parent
REPORT_DIR = PROJECT_ROOT / "reports"
TIMEOUT = 90

# Fields that reflect the request time rather than a change in the data.
REQUEST_TIME_KEYS = re.compile(
    r"^(\$type|timestamp|timeToLive|searchCriteria|journeyVector|"
    r"recommendedMaxAgeMinutes|dateTime|lastUpdate)$",
    re.IGNORECASE,
)


def get(path, params, api_key):
    """GET returning (status, body, Cache-Control header)."""
    query = {**params, "app_key": api_key}
    url = f"{sweep.BASE_URL}{path}?{urllib.parse.urlencode(query)}"
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
                response.headers.get("Cache-Control", ""),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, b"", exc.headers.get("Cache-Control", "")


def strip_request_time_fields(value):
    if isinstance(value, dict):
        return {
            key: strip_request_time_fields(item)
            for key, item in value.items()
            if not REQUEST_TIME_KEYS.match(key)
        }
    if isinstance(value, list):
        return [strip_request_time_fields(item) for item in value]
    return value


def content_hash(body):
    """Hash the response with request-time fields removed and keys sorted."""
    try:
        payload = strip_request_time_fields(json.loads(body))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except ValueError:
        canonical = body.decode("utf-8", errors="replace")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def max_age(cache_control):
    match = re.search(r"max-age=(\d+)", cache_control or "")
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument(
        "--gap", type=int, default=60,
        help="Minimum seconds between the starts of consecutive rounds",
    )
    args = parser.parse_args()

    sweep_reports = sorted(REPORT_DIR.glob("tfl_api_sweep_*.json"))
    if not sweep_reports:
        raise SystemExit("Run scripts/rerun_endpoint_sweep.py first.")

    latest = json.loads(sweep_reports[-1].read_text())
    endpoints = [
        op for op in latest["operations"] if op["result"] == "PASS"
    ]

    api_key = sweep.load_api_key()
    print(f"Endpoints to measure: {len(endpoints)} from {sweep_reports[-1].name}")

    observations = {op["path"]: [] for op in endpoints}

    for round_number in range(1, args.rounds + 1):
        round_start = time.monotonic()
        print(f"\nRound {round_number}/{args.rounds}")

        for op in endpoints:
            polled_at = datetime.now(timezone.utc).isoformat()
            try:
                status, body, cache_control = get(
                    op["tested_path"], op.get("query_params", {}), api_key
                )
                observations[op["path"]].append({
                    "polled_at_utc": polled_at,
                    "status": status,
                    "hash": content_hash(body) if status == 200 else None,
                    "max_age": max_age(cache_control),
                })
            except Exception as exc:
                observations[op["path"]].append({
                    "polled_at_utc": polled_at,
                    "status": None,
                    "error": type(exc).__name__,
                })
            time.sleep(sweep.DELAY_SECONDS)

        elapsed = time.monotonic() - round_start
        print(f"  round took {elapsed:.0f} s")

        if round_number < args.rounds and elapsed < args.gap:
            time.sleep(args.gap - elapsed)

    summary = []
    for path, polls in observations.items():
        hashes = [poll.get("hash") for poll in polls]
        comparisons = [
            (a, b) for a, b in zip(hashes, hashes[1:]) if a and b
        ]
        changes = sum(1 for a, b in comparisons if a != b)
        ages = [poll["max_age"] for poll in polls if poll.get("max_age") is not None]

        summary.append({
            "path": path,
            "polls": len(polls),
            "comparisons": len(comparisons),
            "changes": changes,
            "change_rate": (
                round(changes / len(comparisons), 2) if comparisons else None
            ),
            "cache_max_age_seconds": min(ages) if ages else None,
            "first_poll_utc": polls[0]["polled_at_utc"],
            "last_poll_utc": polls[-1]["polled_at_utc"],
        })

    summary.sort(key=lambda row: (
        -(row["change_rate"] or 0),
        row["cache_max_age_seconds"] if row["cache_max_age_seconds"] is not None else 10**9,
    ))

    report_file = REPORT_DIR / (
        f"tfl_endpoint_freshness_{datetime.now(timezone.utc):%Y%m%dT%H%M}Z.json"
    )
    report_file.write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rounds": args.rounds,
        "minimum_gap_seconds": args.gap,
        "endpoints": summary,
        "observations": observations,
    }, indent=2))

    print("\n--- RANKED BY CHANGE RATE, THEN CACHE AGE ---")
    for row in summary:
        print(
            f"{row['changes']}/{row['comparisons']} changed  "
            f"max-age={row['cache_max_age_seconds']!s:>6}  {row['path']}"
        )
    print(f"\nReport: {report_file.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
