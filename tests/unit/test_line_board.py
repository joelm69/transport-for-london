import copy
from datetime import datetime, timezone

import pytest

from src.presentation.line_board import board_rows, format_duration, summary
from tests.unit.helpers import line

NOW = datetime(2026, 10, 21, 17, 35, 39, tzinfo=timezone.utc)


def rows_for(data, config, modes=None):
    return board_rows(data, NOW, config["severity_classes"], modes)


def test_worst_lines_come_first(config, second_poll):
    rows = rows_for(second_poll, config)
    levels = [r["level"] for r in rows]

    assert levels == sorted(levels, key=["stopped", "delays", "closed", "good"].index)
    assert [r["line_id"] for r in rows[:2]] == ["bakerloo", "tram"]  # Part Suspended
    assert rows[-1]["level"] == "good"


def test_disruption_duration_from_tfl_start_time(config, second_poll):
    elizabeth = next(r for r in rows_for(second_poll, config) if r["line_id"] == "elizabeth")
    assert elizabeth["status"] == "Severe Delays"
    assert elizabeth["since_utc"] == "2026-10-21T17:32:38+00:00"
    assert elizabeth["duration"] == "3 min"


def test_good_service_has_no_duration(config, second_poll):
    circle = next(r for r in rows_for(second_poll, config) if r["line_id"] == "circle")
    assert circle["level"] == "good" and circle["duration"] is None


def test_summary_counts_each_line_once(config, second_poll):
    data = copy.deepcopy(second_poll)
    extra = copy.deepcopy(line(data, "victoria")["lineStatuses"][0])
    extra["statusSeverity"], extra["statusSeverityDescription"] = 3, "Part Suspended"
    line(data, "victoria")["lineStatuses"].append(extra)

    counts = summary(rows_for(data, config))

    assert counts["lines"] == 20
    assert counts["stopped"] == 3  # bakerloo, tram, victoria (worst status)
    assert counts["delays"] == 4   # central, elizabeth, jubilee, mildmay
    assert counts["good"] == 13


def test_mode_filter(config, second_poll):
    rows = rows_for(second_poll, config, modes={"overground"})
    assert {r["mode"] for r in rows} == {"overground"}
    assert len(rows) == 6


def test_service_closed_is_not_shown_as_disruption(config, second_poll):
    data = copy.deepcopy(second_poll)
    status = line(data, "waterloo-city")["lineStatuses"][0]
    status["statusSeverity"], status["statusSeverityDescription"] = 20, "Service Closed"
    row = next(r for r in rows_for(data, config) if r["line_id"] == "waterloo-city")
    assert row["level"] == "closed"


@pytest.mark.parametrize("minutes, text", [
    (None, None), (0.4, "under 1 min"), (3, "3 min"), (60, "1 h"),
    (72, "1 h 12 min"), (61 * 24, "1 d 0 h"), (14 * 60 + 5, "14 h 5 min"),
])
def test_format_duration(minutes, text):
    assert format_duration(minutes) == text
