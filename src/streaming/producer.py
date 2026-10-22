"""Publish TfL arrival predictions to Kafka: one message per line per accepted snapshot.

Design: docs/endpoints/arrivals.md. The producer does the cheap filtering (newer snapshots
only, 30-minute horizon, trackable vehicles, 10 fields) so Kafka carries about a third of the
raw volume. Messages are keyed by line_id: Kafka keeps the order of messages with the same key,
so each line's snapshots reach the detector in order.

Usage:
    python -m src.streaming.producer            # KAFKA_BOOTSTRAP defaults to localhost:9092
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from confluent_kafka import Producer  # noqa: E402

from src.clients.tfl import TflClient, TflError  # noqa: E402
from src.streaming.arrival_detection import by_line, is_newer, slim, snapshot_time  # noqa: E402

logger = logging.getLogger("tfl.streaming.producer")
CONFIG_FILE = PROJECT_ROOT / "config" / "arrivals.json"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = json.loads(CONFIG_FILE.read_text())
    topic = config["topics"]["predictions"]
    client = TflClient.from_environment()
    producer = Producer({
        "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
        "compression.type": "zstd",
        "enable.idempotence": True,     # no duplicates if a send is retried
        "linger.ms": 100,               # batch a snapshot's line messages together
    })

    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(requested=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(requested=True))

    last = {}
    counts = {"accepted": 0, "stale": 0, "errors": 0, "messages": 0}
    while not stop["requested"]:
        started = time.monotonic()
        for mode in config["modes"]:
            try:
                records = client.get_json(config["endpoint"].format(mode=mode), config["params"]).data
            except TflError as error:
                counts["errors"] += 1
                logger.warning("%s: %s", mode, error)
                continue
            snapshot = snapshot_time(records)
            if not is_newer(snapshot, last.get(mode)):
                counts["stale"] += 1
                continue
            last[mode] = snapshot
            counts["accepted"] += 1
            for line_id, line_records in by_line(slim(records, config["horizon_seconds"])).items():
                value = json.dumps({"mode": mode, "line_id": line_id, "snapshot": snapshot,
                                    "records": line_records}, separators=(",", ":"))
                producer.produce(topic, key=line_id, value=value)
                counts["messages"] += 1
            producer.poll(0)
        producer.flush(10)
        logger.info("accepted=%(accepted)d stale=%(stale)d errors=%(errors)d messages=%(messages)d", counts)

        remaining = config["poll_interval_seconds"] - (time.monotonic() - started)
        while remaining > 0 and not stop["requested"]:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0
    producer.flush(10)


if __name__ == "__main__":
    main()
