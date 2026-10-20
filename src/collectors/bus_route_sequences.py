
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.secrets import get_tfl_api_key
OUTPUT_ROOT = PROJECT_ROOT / "data/raw/bus_route_sequences_runs"

BASE_URL = os.getenv(
    "TFL_API_BASE_URL", "https://api.tfl.gov.uk"
).rstrip("/")

METADATA_FILE = (
    PROJECT_ROOT
    / "data/raw/bus_route_metadata_runs"
    / "date=2026-10-16"
    / "run_id=20261016T180428275770Z"
    / "bus_route_metadata.jsonl"
)

VALID_DIRECTIONS = {"inbound", "outbound"}
REQUEST_DELAY_SECONDS = 1.3

def load_expected_pairs():
    """Build unique line-direction pairs from existing route metadata."""
    if not METADATA_FILE.is_file():
        raise FileNotFoundError(
            f"Route metadata file not found: {METADATA_FILE}"
        )

    pairs = set()

    with METADATA_FILE.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue

            row = json.loads(line)
            line_id = str(row.get("line_id") or "").strip()

            if not line_id:
                raise ValueError(
                    f"Missing line_id on metadata line {line_number}"
                )

            route_sections = (
                row.get("response", {}).get("routeSections") or []
            )

            for section in route_sections:
                direction = (
                    section.get("direction") or ""
                ).lower()

                if direction in VALID_DIRECTIONS:
                    pairs.add((line_id, direction))

    if not pairs:
        raise ValueError("No valid line-direction pairs found in metadata")

    return sorted(pairs)


def fetch_sequence(line_id, direction, api_key):
    """Fetch the ordered stop sequence for one line direction."""
    encoded_line_id = urllib.parse.quote(line_id, safe="")

    query = urllib.parse.urlencode({
        "app_key": api_key,
        "serviceTypes": "Regular",
    })

    url = (
        f"{BASE_URL}/Line/{encoded_line_id}"
        f"/Route/Sequence/{direction}?{query}"
    )

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def describe_error(error):
    """Describe an error without printing a request URL or API key."""
    details = {"error_type": type(error).__name__}

    if isinstance(error, urllib.error.HTTPError):
        details["http_status"] = error.code
    elif isinstance(error, urllib.error.URLError):
        details["reason"] = str(error.reason)

    return details

def safe_print(*args, **kwargs):
    """Prevent broken console output from interrupting data collection."""
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        # Redirect further output so interpreter shutdown is also safe.
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass


def main():
    run_time = datetime.now(timezone.utc)
    run_id = run_time.strftime("%Y%m%dT%H%M%S%fZ")
    collected_at = run_time.isoformat()

    output_dir = (
        OUTPUT_ROOT
        / f"date={run_time:%Y-%m-%d}"
        / f"run_id={run_id}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    output_file = output_dir / "bus_route_sequences.jsonl"
    errors_file = output_dir / "collection_errors.json"

    expected = load_expected_pairs()
    successful = set()
    failures = []

    api_key = get_tfl_api_key()

    safe_print(f"Unique line-direction pairs: {len(expected)}")
    safe_print(f"Expected requests: {len(expected)}")
    safe_print(f"Collection run: {run_id}")

    with output_file.open("w", encoding="utf-8") as file:
        for index, (line_id, direction) in enumerate(expected):
            try:
                response = fetch_sequence(
                    line_id, direction, api_key
                )

                sequences = response.get("stopPointSequences") or []
                stop_count = sum(
                    len(sequence.get("stopPoint") or [])
                    for sequence in sequences
                )

                record = {
                    "collection_run_id": run_id,
                    "collected_at_utc": collected_at,
                    "source_endpoint": (
                        f"/Line/{line_id}/Route/Sequence/{direction}"
                    ),
                    "line_id": line_id,
                    "direction": direction,
                    "sequence_count": len(sequences),
                    "stop_count": stop_count,
                    "response": response,
                }

                file.write(
                    json.dumps(record, ensure_ascii=False) + "\n"
                )
                file.flush()
                successful.add((line_id, direction))

                safe_print(
                    f"SUCCESS: line {line_id} {direction} — "
                    f"{len(sequences)} sequences, {stop_count} stops"
                )

            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
            ) as error:
                failure = {
                    "line_id": line_id,
                    "direction": direction,
                    **describe_error(error),
                }
                failures.append(failure)
                safe_print(
                    f"FAILED: line {line_id} {direction} — "
                    f"{failure['error_type']}"
                )

            if index < len(expected) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)

    missing = [
        {"line_id": line_id, "direction": direction}
        for line_id, direction in expected
        if (line_id, direction) not in successful
    ]

    report = {
        "collection_run_id": run_id,
        "expected_requests": len(expected),
        "successful_requests": len(successful),
        "failed_requests": len(failures),
        "missing_requests": missing,
        "failures": failures,
    }

    if failures or missing:
        errors_file.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        safe_print(f"Partial run: {output_file}")
        safe_print(f"Failure report: {errors_file}")
        raise RuntimeError(
            "Collection incomplete. Do not archive this run."
        )

    safe_print(f"Successful requests: {len(successful)}")
    safe_print(f"Output file: {output_file}")
    safe_print("Collection complete. Archiving has not been performed.")


if __name__ == "__main__":
    main()
