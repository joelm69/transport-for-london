"""Shared helpers for unit tests."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "line_status"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def line(data, line_id):
    return next(item for item in data if item["id"] == line_id)


def with_status(data, line_id, **changes):
    """Copy of data with the first status of one line modified."""
    data = copy.deepcopy(data)
    line(data, line_id)["lineStatuses"][0].update(changes)
    return data
