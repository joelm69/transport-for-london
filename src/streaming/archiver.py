"""Archive arrival and dropped events from Kafka to the lake (GCS) in hourly batches.

Its own consumer group ("arrivals-archiver"), so it reads the events topic independently of any
other consumer. Auto-commit is off: offsets are committed only after the batch holding those
events is published and read back (see src/streaming/archive.py). Kafka retention (2 hours) must
stay longer than the batch period (1 hour), or a restart could find events already deleted.

Usage:
    python -m src.streaming.archiver --lake local      # data/lake, for development
    python -m src.streaming.archiver --lake gcs        # transport_for_london_bucket
"""

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from confluent_kafka import Consumer, TopicPartition  # noqa: E402

from src.storage.lake import GcsLake, LocalLake  # noqa: E402
from src.streaming.archive import EventArchive, make_archive_writers  # noqa: E402

logger = logging.getLogger("tfl.streaming.archiver")
CONFIG_FILE = PROJECT_ROOT / "config" / "arrivals.json"
BUCKET = "transport_for_london_bucket"
GCP_PROJECT = "transport-for-london-511318"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Archive arrival events from Kafka to the lake.")
    parser.add_argument("--lake", choices=["local", "gcs"], default="local")
    parser.add_argument("--lake-root", default=str(PROJECT_ROOT / "data" / "lake"))
    parser.add_argument("--tmp-dir", default=str(PROJECT_ROOT / "data" / "tmp"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = json.loads(CONFIG_FILE.read_text())
    topic = config["topics"]["events"]
    lake = GcsLake(BUCKET, project=GCP_PROJECT) if args.lake == "gcs" else LocalLake(args.lake_root)
    archive = EventArchive(make_archive_writers(lake, args.tmp_dir))
    archive.recover()

    consumer = Consumer({
        "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
        "group.id": "arrivals-archiver",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "queued.max.messages.kbytes": 4096,
    })
    consumer.subscribe([topic])

    def commit(offsets):
        if offsets:
            consumer.commit(offsets=[TopicPartition(topic, p, o) for p, o in offsets.items()],
                            asynchronous=False)
            logger.info("Committed offsets %s", offsets)

    stop = {"requested": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(requested=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(requested=True))

    logger.info("Archiving %s to %r", topic, lake)
    try:
        while not stop["requested"]:
            commit(archive.tick())
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                logger.warning("consumer error: %s", message.error())
                continue
            archive.write(json.loads(message.value()), message.partition(), message.offset())
    finally:
        commit(archive.close())
        consumer.close()


if __name__ == "__main__":
    main()
