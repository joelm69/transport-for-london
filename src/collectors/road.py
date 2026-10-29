"""Collect road disruptions and main road corridor status into raw, change and coverage batches.

Design: docs/endpoints/road.md. Both feeds are polled together every 5 minutes; each is
validated and compared on its own, so a failure in one never stops the other. Some disruption
is updated on most polls, so raw responses are kept only as:
  baseline    first poll of a session where both feeds are valid
  checkpoint  first poll of each hour (complete responses to rebuild from)
  error       a request failed
  invalid     a response failed an ERROR rule (kept for investigation)
The change records carry every disruption and corridor that changed; every poll writes a
coverage record with counts.

Usage:
    python -m src.collectors.road --polls 3
    python -m src.collectors.road --forever --window continuous --lake gcs
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
from src.collectors.runner import run  # noqa: E402
from src.deduplication.road import detect_changes, normalise_corridors, normalise_disruptions  # noqa: E402
from src.storage.batch_writer import BatchWriter  # noqa: E402
from src.storage.lake import GcsLake, LocalLake  # noqa: E402
from src.validation.road import ERROR, EXCLUDED, WARN, Issue, has_errors, validate_corridors, validate_disruptions  # noqa: E402

logger = logging.getLogger("tfl.collector.road")

CONFIG_FILE = PROJECT_ROOT / "config" / "road.json"
BUCKET = "transport_for_london_bucket"
GCP_PROJECT = "transport-for-london-511318"


def load_config(path=CONFIG_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_writers(lake, tmp_dir, config):
    """Raw, two change streams and coverage (zone layout in docs/requirements.md)."""
    dataset = config["dataset"]
    return {
        "raw": BatchWriter(lake, tmp_dir, dataset, f"raw/{dataset}", dataset, hour_partition=True),
        "disruptions": BatchWriter(lake, tmp_dir, dataset, f"processed/{config['disruption_changes_name']}",
                                   config["disruption_changes_name"]),
        "corridors": BatchWriter(lake, tmp_dir, dataset, f"processed/{config['corridor_changes_name']}",
                                 config["corridor_changes_name"]),
        "coverage": BatchWriter(lake, tmp_dir, dataset, f"coverage/{dataset}", "coverage"),
    }


class RoadCollector:
    def __init__(self, client, config, writers, collection_window,
                 now=lambda: datetime.now(timezone.utc)):
        self.client = client
        self.config = config
        self.writers = writers
        self.window = collection_window
        self.now = now
        self.state = {"disruptions": {}, "corridors": {}}
        self.baseline_done = {"disruptions": False, "corridors": False}
        self.last_stored_hour = None

    def _fetch(self, endpoint):
        """(data, http_status, attempts, latency_ms, error)."""
        try:
            response = self.client.get_json(endpoint)
            return response.data, response.status, response.attempts, response.latency_ms, None
        except TflError as exc:
            return None, getattr(exc, "status", None), getattr(exc, "attempts", 0), None, str(exc)

    def _raw_reason(self, fetched_ok, valid, collected):
        if not fetched_ok:
            return "error"
        if not valid:
            return "invalid"
        if self.last_stored_hour is None:
            return "baseline"
        if collected.strftime("%Y%m%d%H") != self.last_stored_hour:
            return "checkpoint"
        return None

    def _changes(self, feed, records, poll, key, new, gone):
        changes, self.state[feed] = detect_changes(
            self.state[feed], records, not self.baseline_done[feed], poll, key, new, gone)
        self.baseline_done[feed] = True
        for change in changes:
            self.writers[feed].append(change)
        return len(changes)

    def poll_once(self):
        collected = self.now()
        for writer in self.writers.values():
            writer.roll_over(collected)
        poll = {
            "poll_id": collected.strftime("%Y%m%dT%H%M%S%fZ"),
            "collected_at_utc": collected.isoformat(),
            "collection_window": self.window,
        }

        d_data, d_status, d_attempts, d_latency, d_error = self._fetch(self.config["disruption_endpoint"])
        c_data, c_status, c_attempts, c_latency, c_error = self._fetch(self.config["corridor_endpoint"])
        d_issues = ([Issue(ERROR, "P1", d_error or "No response")] if d_data is None
                    else validate_disruptions(d_data, self.config))
        c_issues = ([Issue(ERROR, "C1", c_error or "No response")] if c_data is None
                    else validate_corridors(c_data, self.config))
        d_valid, c_valid = not has_errors(d_issues), not has_errors(c_issues)

        disruptions = normalise_disruptions(d_data, poll, d_issues) if d_valid else []
        corridors = normalise_corridors(c_data, poll) if c_valid else []

        raw_reason = self._raw_reason(d_data is not None and c_data is not None, d_valid and c_valid, collected)
        if raw_reason:
            self.writers["raw"].append({
                **poll,
                "raw_reason": raw_reason,
                "disruption_http_status": d_status,
                "corridor_http_status": c_status,
                "error": d_error or c_error,
                "disruptions": d_data,
                "corridors": c_data,
            })
            if d_valid and c_valid:
                self.last_stored_hour = collected.strftime("%Y%m%d%H")

        d_changes = self._changes("disruptions", disruptions, poll, "disruption_id", "NEW", "CLEARED") if d_valid else 0
        c_changes = self._changes("corridors", corridors, poll, "corridor_id", "NEW", "REMOVED") if c_valid else 0

        issues = d_issues + c_issues
        coverage = {
            **poll,
            "disruption_http_status": d_status,
            "corridor_http_status": c_status,
            "attempts": (d_attempts or 0) + (c_attempts or 0),
            "latency_ms": (d_latency or 0) + (c_latency or 0),
            "disruptions_valid": d_valid,
            "corridors_valid": c_valid,
            "valid": d_valid and c_valid,
            "disruptions": len(disruptions),
            "incidents": sum(r["is_incident"] for r in disruptions),
            "corridors": len(corridors),
            "corridors_not_good": sum(r["status"] != "Good" for r in corridors),
            "records_excluded": sum(1 for i in issues if i.level == EXCLUDED),
            "raw_stored": raw_reason is not None,
            "raw_reason": raw_reason,
            "disruption_changes": d_changes,
            "corridor_changes": c_changes,
            "changes_written": d_changes + c_changes,
            "errors": sorted({i.rule for i in issues if i.level == ERROR}),
            "warnings": sorted({i.rule for i in issues if i.level in (WARN, EXCLUDED)}),
        }
        self.writers["coverage"].append(coverage)
        return coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect TfL road disruptions and corridor status.")
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
    writers = make_writers(lake, args.tmp_dir, config)
    collector = RoadCollector(TflClient.from_environment(), config, writers, args.window)

    stop = {"requested": False}

    def request_stop(signum, _frame):
        logger.info("Received %s; finishing current poll and flushing", signal.Signals(signum).name)
        stop["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    interval = args.interval or config["poll_interval_seconds"]
    max_polls = None if (args.forever or args.minutes) else (args.polls or 10)
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None

    logger.info("Collecting road disruptions to %r every %ss (window=%s)", lake, interval, args.window)
    polls = run(collector, writers, interval, max_polls, deadline,
                should_stop=lambda: stop["requested"])

    published = [m for w in writers.values() for m in w.published]
    logger.info("Done: %d polls, %d batch files published", polls, len(published))
    for manifest in published:
        logger.info("  %s (%d records)", manifest["uri"], manifest["record_count"])


if __name__ == "__main__":
    main()
