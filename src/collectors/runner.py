"""Shared polling loop and batch writers for all collectors.

A collector provides poll_once() returning a coverage record with at least
poll_id, valid, changes_written, warnings and errors.
"""

import logging
import time

from src.storage.batch_writer import BatchWriter

logger = logging.getLogger("tfl.collector")


def make_writers(lake, tmp_dir, dataset, changes_name=None):
    """Raw, change and coverage writers for one dataset (zone layout in docs/requirements.md)."""
    changes_name = changes_name or f"{dataset}_changes"
    return {
        "raw": BatchWriter(lake, tmp_dir, dataset, f"raw/{dataset}", dataset,
                           hour_partition=True),
        "changes": BatchWriter(lake, tmp_dir, dataset, f"processed/{changes_name}",
                               changes_name),
        "coverage": BatchWriter(lake, tmp_dir, dataset, f"coverage/{dataset}",
                                "coverage"),
    }


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
