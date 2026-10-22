"""Consume arrival prediction snapshots from Kafka and publish arrival and dropped events.

One LineTracker per line holds the visits it is watching; state is rebuilt from the topic on
restart (retention covers far more than the 30-minute horizon). Events go to their own topic,
keyed by line_id, for the GCS archive writer and live consumers to read independently. Each
processed snapshot also emits a "snapshot" coverage event, the proof of observed time.

Usage:
    python -m src.streaming.detector            # KAFKA_BOOTSTRAP defaults to localhost:9092
"""

import json
import logging
import os
import signal
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from confluent_kafka import Consumer, Producer  # noqa: E402

from src.streaming.arrival_detection import LineTracker, coverage, parse_utc  # noqa: E402

logger = logging.getLogger("tfl.streaming.detector")
CONFIG_FILE = PROJECT_ROOT / "config" / "arrivals.json"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = json.loads(CONFIG_FILE.read_text())
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": "arrival-detector",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
        # Bound what the client pre-fetches into memory (librdkafka defaults to 64 MB per
        # partition): a few snapshots ahead is enough.
        "queued.max.messages.kbytes": 8192,
        "fetch.max.bytes": 4194304,
    })
    producer = Producer({"bootstrap.servers": bootstrap, "compression.type": "zstd",
                         "enable.idempotence": True, "linger.ms": 100,
                         "queue.buffering.max.kbytes": 8192})
    consumer.subscribe([config["topics"]["predictions"]])
    events_topic = config["topics"]["events"]

    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(requested=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(requested=True))

    trackers = {}
    counts = Counter()
    lag = []
    next_report = time.monotonic() + 60
    try:
        while not stop["requested"]:
            message = consumer.poll(1.0)
            if message is not None and message.error() is None:
                snapshot = json.loads(message.value())
                key = snapshot["line_id"]
                tracker = trackers.setdefault(key, LineTracker(snapshot["mode"], key, config))
                # Kafka keeps per-key order; this guards against a replayed older snapshot.
                if tracker.snapshot is not None and parse_utc(snapshot["snapshot"]) <= tracker.snapshot:
                    counts["out_of_order"] += 1
                    continue
                events = tracker.update(snapshot["snapshot"], snapshot["records"])
                events.append(coverage(tracker, snapshot["snapshot"], len(snapshot["records"]), events))
                for event in events:
                    counts[event["event"]] += 1
                    producer.produce(events_topic, key=key, value=json.dumps(event, separators=(",", ":")))
                producer.poll(0)
                lag.append(time.time() - parse_utc(snapshot["snapshot"]).timestamp())
            elif message is not None:
                logger.warning("consumer error: %s", message.error())

            if time.monotonic() >= next_report:
                visits = sum(len(t.visits) for t in trackers.values())
                logger.info("last 60s: %s | tracking %d visits | median lag %.0fs",
                            dict(counts), visits, sorted(lag)[len(lag) // 2] if lag else -1)
                counts.clear()
                lag.clear()
                next_report += 60
    finally:
        producer.flush(10)
        consumer.close()


if __name__ == "__main__":
    main()
