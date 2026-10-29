"""Collect Santander Cycles docking stations into raw, change and coverage batches.

Design: docs/endpoints/bikepoint.md. Some station changes on almost every poll, so a
"store raw on change" rule would keep every 2.2 MB response. Raw is kept only as:
  baseline    first valid poll of a session
  checkpoint  first poll of each hour (a complete response to rebuild from)
  error       the request failed (the error envelope is small and worth keeping)
  invalid     the response failed an ERROR rule (kept for investigation)
The change records carry every station whose counts changed; every poll writes a coverage
record with network totals.

Usage:
    python -m src.collectors.bikepoint --polls 3
    python -m src.collectors.bikepoint --forever --window continuous --lake gcs
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.tfl import TflClient, TflError  # noqa: E402
from src.collectors.runner import make_writers, run  # noqa: E402
from src.deduplication.bikepoint import detect_changes, normalise  # noqa: E402
from src.storage.lake import GcsLake, LocalLake  # noqa: E402
from src.validation.bikepoint import ERROR, EXCLUDED, WARN, Issue, has_errors, validate_poll  # noqa: E402

logger = logging.getLogger("tfl.collector.bikepoint")

CONFIG_FILE = PROJECT_ROOT / "config" / "bikepoint.json"
BUCKET = "transport_for_london_bucket"
GCP_PROJECT = "transport-for-london-511318"


def load_config(path=CONFIG_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class BikePointCollector:
    def __init__(self, client, config, writers, collection_window,
                 now=lambda: datetime.now(timezone.utc)):
        self.client = client
        self.config = config
        self.writers = writers
        self.window = collection_window
        self.now = now
        self.state = {}
        self.baseline_done = False
        self.last_stored_hour = None

    def _raw_reason(self, data, valid, collected):
        if data is None:
            return "error"
        if not valid:
            return "invalid"  # always worth keeping for investigation
        if self.last_stored_hour is None:
            return "baseline"
        if collected.strftime("%Y%m%d%H") != self.last_stored_hour:
            return "checkpoint"
        return None

    def poll_once(self):
        collected = self.now()
        for writer in self.writers.values():
            writer.roll_over(collected)

        poll = {
            "poll_id": collected.strftime("%Y%m%dT%H%M%S%fZ"),
            "collected_at_utc": collected.isoformat(),
            "collection_window": self.window,
        }

        data, http_status, attempts, latency_ms, error = None, None, 0, None, None
        try:
            response = self.client.get_json(self.config["endpoint"], self.config["params"])
            data, http_status = response.data, response.status
            attempts, latency_ms = response.attempts, response.latency_ms
        except TflError as exc:
            http_status = getattr(exc, "status", None)
            attempts = getattr(exc, "attempts", 0)
            error = str(exc)

        if data is None:
            issues = [Issue(ERROR, "P1", error or "No response")]
        else:
            issues = validate_poll(data, self.config, collected)

        records = []
        if isinstance(data, list) and not any(i.rule == "P1" for i in issues):
            records = normalise(data, poll, self.config, issues)
        valid = not has_errors(issues)

        raw_reason = self._raw_reason(data, valid, collected)
        if raw_reason:
            self.writers["raw"].append({
                **poll,
                "raw_reason": raw_reason,
                "endpoint": self.config["endpoint"],
                "http_status": http_status,
                "attempts": attempts,
                "latency_ms": latency_ms,
                "error": error,
                "response": data,
            })
            if valid:
                self.last_stored_hour = collected.strftime("%Y%m%d%H")

        changes = []
        if valid:
            changes, self.state = detect_changes(
                self.state, records, is_baseline=not self.baseline_done, poll=poll
            )
            self.baseline_done = True
            for change in changes:
                self.writers["changes"].append(change)

        coverage = {
            **poll,
            "http_status": http_status,
            "attempts": attempts,
            "latency_ms": latency_ms,
            "valid": valid,
            "records_returned": len(data) if isinstance(data, list) else 0,
            "records_kept": len(records),
            "records_excluded": sum(1 for i in issues if i.level == EXCLUDED),
            "stations": len(records),
            "bikes": sum(r["bikes"] for r in records),
            "e_bikes": sum(r["e_bikes"] for r in records),
            "empty_docks": sum(r["empty_docks"] for r in records),
            "docks": sum(r["docks"] for r in records),
            "empty_stations": sum(r["bikes"] == 0 for r in records),
            "full_stations": sum(r["empty_docks"] == 0 for r in records),
            "raw_stored": raw_reason is not None,
            "raw_reason": raw_reason,
            "changes_written": len(changes),
            "errors": sorted({i.rule for i in issues if i.level == ERROR}),
            "warnings": sorted({i.rule for i in issues if i.level in (WARN, EXCLUDED)}),
        }
        self.writers["coverage"].append(coverage)
        return coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect Santander Cycles docking stations.")
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--minutes", type=float, help="Stop after this many minutes")
    limit.add_argument("--polls", type=int, help="Stop after this many polls")
    limit.add_argument("--forever", action="store_true",
                       help="Run until stopped (SIGTERM/SIGINT), e.g. as a systemd service")
    parser.add_argument("--interval", type=float, help="Seconds between polls (default from config)")
    parser.add_argument("--window", default="manual",
                        help="collection_window label, e.g. continuous, manual")
    parser.add_argument("--lake", choices=["local", "gcs"], default="local")
    parser.add_argument("--lake-root", default=str(PROJECT_ROOT / "data" / "lake"))
    parser.add_argument("--tmp-dir", default=str(PROJECT_ROOT / "data" / "tmp"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config()
    lake = GcsLake(BUCKET, project=GCP_PROJECT) if args.lake == "gcs" else LocalLake(args.lake_root)
    writers = make_writers(lake, args.tmp_dir, config["dataset"], config["changes_name"])
    collector = BikePointCollector(TflClient.from_environment(), config, writers, args.window)

    stop = {"requested": False}

    def request_stop(signum, _frame):
        logger.info("Received %s; finishing current poll and flushing", signal.Signals(signum).name)
        stop["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    interval = args.interval or config["poll_interval_seconds"]
    max_polls = None if (args.forever or args.minutes) else (args.polls or 10)
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None

    logger.info("Collecting bike points to %r every %ss (window=%s)", lake, interval, args.window)
    polls = run(collector, writers, interval, max_polls, deadline,
                should_stop=lambda: stop["requested"])

    published = [m for w in writers.values() for m in w.published]
    logger.info("Done: %d polls, %d batch files published", polls, len(published))
    for manifest in published:
        logger.info("  %s (%d records)", manifest["uri"], manifest["record_count"])


if __name__ == "__main__":
    main()
