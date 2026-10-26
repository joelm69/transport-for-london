"""Replay stored station notice polls through the collector's current rules.

Polls rejected by validation are kept whole in raw/stop_disruptions/ (raw_reason "invalid").
After a rule is fixed, this feeds those polls, in order and at their original times, through
StopDisruptionCollector, so the change and coverage records they should have produced are
written as if the fix had been live. First use: the P3 key clash that rejected every poll from
2026-10-24 00:00 to 2026-10-26 14:42 UTC (3,762 polls).

Writes to a local lake (upload it afterwards, create-only):
  processed/stop_disruption_changes/date=.../stop_disruption_changes_replay_<start>.jsonl.gz
  coverage/stop_disruptions/date=.../coverage_replay_<start>.jsonl.gz
The "_replay" prefix keeps every name apart from the live collector's files for the same
hours. Raw responses are not written again (they are already stored). The first replayed poll
is a BASELINE, so the replay is its own collection session in the BigQuery views.

    python scripts/replay_stop_disruptions.py RAW_DIR OUT_DIR \\
        --start 2026-10-24T00:00:00Z --end 2026-10-26T14:43:00Z
"""

import argparse
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.clients.tfl import TflResponse  # noqa: E402
from src.collectors.stop_disruptions import StopDisruptionCollector, load_config  # noqa: E402
from src.storage.batch_writer import BatchWriter  # noqa: E402
from src.storage.lake import LocalLake  # noqa: E402


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stored_polls(raw_dir, start, end):
    """Rejected polls with a response, in [start, end), oldest first."""
    polls = []
    for path in sorted(Path(raw_dir).rglob("*.jsonl.gz")):
        for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
            record = json.loads(line)
            at = utc(record["collected_at_utc"])
            if record.get("raw_reason") == "invalid" and isinstance(record.get("response"), list) \
                    and start <= at < end:
                polls.append((at, record))
    polls.sort(key=lambda p: p[0])
    return polls


class StoredClient:
    """Returns the stored responses one by one, as the live client returned them."""

    def __init__(self, polls):
        self._polls = iter(polls)
        self.current = None

    def get_json(self, path, params=None):
        at, record = self.current
        return TflResponse(path=path, params=dict(params or {}), status=record.get("http_status") or 200,
                           data=record["response"], fetched_at_utc=record["collected_at_utc"],
                           latency_ms=record.get("latency_ms") or 0, attempts=record.get("attempts") or 1,
                           size_bytes=0)


class NoRaw:
    """The raw writer's interface, writing nothing: the responses are already stored."""
    published = []

    def roll_over(self, at):
        pass

    def append(self, record):
        pass

    def flush(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("raw_dir", help="local copy of raw/stop_disruptions/ for the period")
    parser.add_argument("out_dir", help="local lake root for the replayed change and coverage files")
    parser.add_argument("--start", required=True, help="first poll time to replay (UTC, inclusive)")
    parser.add_argument("--end", required=True, help="replay polls before this time (UTC)")
    parser.add_argument("--window", default="continuous", help="collection_window of the replayed polls")
    args = parser.parse_args()

    config = load_config()
    polls = stored_polls(args.raw_dir, utc(args.start), utc(args.end))
    if not polls:
        sys.exit("No stored polls in that period.")

    lake, tmp = LocalLake(args.out_dir), Path(args.out_dir) / "_tmp"
    writers = {
        "raw": NoRaw(),
        "changes": BatchWriter(lake, tmp, config["dataset"], f"processed/{config['changes_name']}",
                               f"{config['changes_name']}_replay"),
        "coverage": BatchWriter(lake, tmp, config["dataset"], f"coverage/{config['dataset']}",
                                "coverage_replay"),
    }
    client = StoredClient(polls)
    collector = StopDisruptionCollector(client, config, writers, args.window, now=lambda: client.current[0])

    valid = changes = 0
    for poll in polls:
        client.current = poll
        coverage = collector.poll_once()
        valid += coverage["valid"]
        changes += coverage["changes_written"]
    for writer in writers.values():
        writer.flush()

    files = [m["object_path"] for name in ("changes", "coverage") for m in writers[name].published]
    print(f"Replayed {len(polls):,} polls ({polls[0][0]:%d %b %H:%M} to {polls[-1][0]:%d %b %H:%M} UTC): "
          f"{valid:,} valid, {changes:,} change records, {len(files)} files in {args.out_dir}")


if __name__ == "__main__":
    main()
