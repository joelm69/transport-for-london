import json
from datetime import datetime, timezone

import pytest

from tests.unit.helpers import ROOT, load_fixture


@pytest.fixture
def config():
    return json.loads((ROOT / "config" / "line_status.json").read_text(encoding="utf-8"))


@pytest.fixture
def first_poll():
    """Live response 2026-10-21 17:29:46Z: six disrupted lines."""
    return load_fixture("rail_status_20261021T172946Z.json")


@pytest.fixture
def second_poll():
    """Live response 2026-10-21 17:35:39Z: Elizabeth line newly disrupted."""
    return load_fixture("rail_status_20261021T173539Z.json")


@pytest.fixture
def severity_meta():
    return load_fixture("line_severity_meta_20261021.json")


@pytest.fixture
def severity_codes(severity_meta):
    codes = {}
    for item in severity_meta:
        codes.setdefault(item["modeName"], {})[item["severityLevel"]] = item["description"]
    return codes


@pytest.fixture
def collected_at():
    return datetime(2026, 10, 21, 17, 35, 39, tzinfo=timezone.utc)
