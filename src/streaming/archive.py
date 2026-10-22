"""Archive arrival events to the lake in hourly batches, and say which Kafka offsets are safe
to commit. No Kafka here, so the delivery guarantee is unit-testable.

Guarantee (at-least-once): an offset is reported as committable only after every event up to
it is in a published batch. Events written after the last publish sit in BatchWriter's fsynced
tmp file; after a crash, recover() publishes them and Kafka redelivers them too, so a few events
can be archived twice. Each record carries its Kafka partition and offset, which BigQuery uses
to drop those duplicates exactly.

Batches are cut by processing time (archived_at_utc), not event time: a late event must not
reopen an hour that was already published.
"""

from datetime import datetime, timezone

from src.storage.batch_writer import BatchWriter

ZONES = {
    "arrival": ("processed/arrival_events", "arrival_events"),
    "dropped": ("processed/arrival_drops", "arrival_drops"),
}


def make_archive_writers(lake, tmp_dir, dataset="arrivals"):
    return {
        event: BatchWriter(lake, tmp_dir, dataset, zone_dir, prefix, time_field="archived_at_utc")
        for event, (zone_dir, prefix) in ZONES.items()
    }


class EventArchive:
    def __init__(self, writers):
        self.writers = writers
        self._written = {}      # partition -> offset of the last event appended to a batch
        self._safe = {}         # partition -> offset safe to commit (next offset to read)

    def recover(self):
        for writer in self.writers.values():
            writer.recover()

    def _published(self):
        return sum(len(w.published) for w in self.writers.values())

    def tick(self, now=None):
        """Roll every writer over on the clock (call often, even with no events). Returns
        {partition: offset} to commit when a publish made more events safe, else {}."""
        now = now or datetime.now(timezone.utc)
        before = self._published()
        for writer in self.writers.values():
            writer.roll_over(now)
        return self._advance() if self._published() > before else {}

    def write(self, event, partition, offset, now=None):
        """Append one event. Call tick() first so a due publish happens before this event
        joins the new batch."""
        now = now or datetime.now(timezone.utc)
        record = {**event, "archived_at_utc": now.isoformat(),
                  "kafka_partition": partition, "kafka_offset": offset}
        self.writers[event["event"]].append(record)
        self._written[partition] = offset

    def close(self):
        """Publish everything; return the offsets now safe to commit."""
        for writer in self.writers.values():
            writer.flush()
        return self._advance()

    def _advance(self):
        # Everything appended so far is now in a published batch.
        newly = {p: o + 1 for p, o in self._written.items() if self._safe.get(p) != o + 1}
        self._safe.update(newly)
        return newly
