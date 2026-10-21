"""Collect live rail line status into raw, change and coverage batches.

Design: docs/endpoints/line_status.md. Each poll:
  1. fetch status via the shared TfL client
  2. append the raw envelope (always, even if invalid)
  3. validate (rules P1–P3, S1–S8)
  4. if valid: flatten, detect changes, append change records
  5. append one coverage record

Usage:
    python -m src.collectors.line_status --minutes 10 --window manual
    python -m src.collectors.line_status --polls 3 --lake gcs
    python -m src.collectors.line_status --forever --window continuous --lake gcs
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
from src.deduplication.line_status import detect_changes, flatten_statuses  # noqa: E402
from src.storage.batch_writer import BatchWriter  # noqa: E402
from src.storage.lake import GcsLake, LocalLake  # noqa: E402
from src.validation.line_status import ERROR, WARN, Issue, has_errors, validate_poll  # noqa: E402

logger = logging.getLogger("tfl.collector.line_status")

CONFIG_FILE = PROJECT_ROOT / "config" / "line_status.json"
BUCKET = "transport_for_london_bucket"
GCP_PROJECT = "transport-for-london-511318"


def load_config(path=CONFIG_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_severity_codes(client, config):
    """{mode: {code: description}} from TfL, falling back to config."""
    try:
        response = client.get_json("/Line/Meta/Severity")
        codes = {}
        for item in response.data:
            codes.setdefault(item["modeName"], {})[item["severityLevel"]] = item["description"]
        return codes
    except (TflError, KeyError, TypeError) as exc:
        logger.warning("Severity codes unavailable (%s); using config fallback", exc)
        fallback = {int(k): v for k, v in config["fallback_severity_codes"].items()}
        return {"default": fallback}


def make_writers(lake, tmp_dir, dataset):
    return {
        "raw": BatchWriter(lake, tmp_dir, dataset, f"raw/{dataset}", dataset,
                           hour_partition=True),
        "changes": BatchWriter(lake, tmp_dir, dataset, f"processed/{dataset}_changes",
                               f"{dataset}_changes"),
        "coverage": BatchWriter(lake, tmp_dir, dataset, f"coverage/{dataset}",
                                "coverage"),
    }


class LineStatusCollector:
    def __init__(self, client, config, writers, collection_window,
                 now=lambda: datetime.now(timezone.utc)):
        self.client = client
        self.config = config
        self.writers = writers
        self.window = collection_window
        self.now = now
        self.state = {}
        self.baseline_done = False
        self.severity_codes = load_severity_codes(client, config)
        self.good = set(config["severity_classes"]["good"])

    def poll_once(self):
        """Run one poll; return its coverage record."""
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

        # Raw is always kept, valid or not.
        self.writers["raw"].append({
            **poll,
            "endpoint": self.config["endpoint"],
            "params": self.config["params"],
            "http_status": http_status,
            "attempts": attempts,
            "latency_ms": latency_ms,
            "error": error,
            "response": data,
        })

        if data is None:
            issues = [Issue(ERROR, "P1", error or "No response")]
        else:
            issues = validate_poll(
                data,
                self.config["expected_line_ids"],
                self.severity_codes,
                self.good,
                collected,
                self.config["future_from_date_tolerance_seconds"],
            )

        changes = []
        valid = not has_errors(issues)
        if valid:
            statuses = flatten_statuses(
                data, poll, self.config["severity_classes"],
                [i for i in issues if i.level == WARN],
            )
            changes, self.state = detect_changes(
                self.state, statuses, is_baseline=not self.baseline_done
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
            "lines_returned": len(data) if isinstance(data, list) else 0,
            "statuses_returned": sum(
                len(line.get("lineStatuses") or [])
                for line in data if isinstance(line, dict)
            ) if isinstance(data, list) else 0,
            "changes_written": len(changes),
            "errors": sorted({i.rule for i in issues if i.level == ERROR}),
            "warnings": sorted({i.rule for i in issues if i.level == WARN}),
        }
        self.writers["coverage"].append(coverage)
        return coverage


def run(collector, writers, interval, max_polls=None, deadline=None,
        clock=time.monotonic, sleep=time.sleep, should_stop=lambda: False):
    """Poll on a fixed interval until max_polls, deadline or a stop request.
    Batches are always flushed on exit."""
    for writer in writers.values():
        writer.recover()

    polls = 0
    try:
        while not should_stop():
            started = clock()
            coverage = collector.poll_once()
            polls += 1
            logger.info(
                "poll %s valid=%s changes=%d warnings=%s errors=%s",
                coverage["poll_id"], coverage["valid"], coverage["changes_written"],
                coverage["warnings"] or "-", coverage["errors"] or "-",
            )

            if max_polls is not None and polls >= max_polls:
                break
            if deadline is not None and clock() >= deadline:
                break

            remaining = interval - (clock() - started)
            # Sleep in short steps so a stop request is noticed quickly.
            while remaining > 0 and not should_stop():
                step = min(1.0, remaining)
                sleep(step)
                remaining -= step
    finally:
        for writer in writers.values():
            writer.flush()

    return polls


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect live TfL rail line status.")
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--minutes", type=float, help="Stop after this many minutes")
    limit.add_argument("--polls", type=int, help="Stop after this many polls")
    limit.add_argument("--forever", action="store_true",
                       help="Run until stopped (SIGTERM/SIGINT), e.g. as a systemd service")
    parser.add_argument("--interval", type=float, help="Seconds between polls (default from config)")
    parser.add_argument("--window", default="manual",
                        help="collection_window label, e.g. fixed_evening_peak, visitor, manual")
    parser.add_argument("--lake", choices=["local", "gcs"], default="local")
    parser.add_argument("--lake-root", default=str(PROJECT_ROOT / "data" / "lake"))
    parser.add_argument("--tmp-dir", default=str(PROJECT_ROOT / "data" / "tmp"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config()
    lake = GcsLake(BUCKET, project=GCP_PROJECT) if args.lake == "gcs" else LocalLake(args.lake_root)
    writers = make_writers(lake, args.tmp_dir, config["dataset"])
    collector = LineStatusCollector(TflClient.from_environment(), config, writers, args.window)

    stop = {"requested": False}

    def request_stop(signum, _frame):
        logger.info("Received %s; finishing current poll and flushing", signal.Signals(signum).name)
        stop["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    interval = args.interval or config["poll_interval_seconds"]
    if args.forever or args.minutes:
        max_polls = None
    else:
        max_polls = args.polls or 10
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None

    logger.info("Collecting line status to %r every %ss (window=%s)", lake, interval, args.window)
    polls = run(collector, writers, interval, max_polls, deadline,
                should_stop=lambda: stop["requested"])

    published = [m for w in writers.values() for m in w.published]
    logger.info("Done: %d polls, %d batch files published", polls, len(published))
    for manifest in published:
        logger.info("  %s (%d records)", manifest["uri"], manifest["record_count"])


if __name__ == "__main__":
    main()
