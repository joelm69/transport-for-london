"""Bus recommendation: text from the figures, and the export views kept in step with the exports."""

import copy
import importlib.util

from src.presentation.bus_recommendation import findings, period, summary
from tests.unit.helpers import ROOT

# tfl_portfolio.bus_recommendation on 2026-10-30 (data 26-29 October).
ROW = {
    "first_day": "2026-10-23", "last_day": "2026-10-29", "data_from": "2026-10-26", "days": 4,
    "bunching_rate": 0.0736, "bunching_min": 0.0727, "bunching_max": 0.0760,
    "wait_minutes": 7.04, "gap_minutes": 12.48, "even_wait_minutes": 6.24,
    "extra_wait_minutes": 0.80, "extra_wait_share": 0.128, "passenger_hours_per_day": 67293,
    "network_kmh": 8.84,
    "most_bunched": [
        {"route": "29", "bunching_rate": 0.2452, "gap_minutes": 4.86, "wait_ratio": 1.657},
        {"route": "141", "bunching_rate": 0.234, "gap_minutes": 5.50, "wait_ratio": 1.731},
        {"route": "18", "bunching_rate": 0.227, "gap_minutes": 4.92, "wait_ratio": 1.649},
        {"route": "38", "bunching_rate": 0.222, "gap_minutes": 4.87, "wait_ratio": 1.681},
        {"route": "55", "bunching_rate": 0.206, "gap_minutes": 6.86, "wait_ratio": 1.524},
    ],
    "slowest": [
        {"route": "189", "kmh": 4.38, "buses": 41}, {"route": "19", "kmh": 4.61, "buses": 63},
        {"route": "98", "kmh": 4.68, "buses": 31}, {"route": "W7", "kmh": 4.94, "buses": 15},
        {"route": "390", "kmh": 5.08, "buses": 35},
    ],
}


def test_summary():
    assert summary(ROW) == (
        "Uneven bus spacing adds about 0.8 minutes to the average wait, 13% more than an evenly spaced "
        "service. That is up to 67,000 passenger-hours a day across about 5 million daily journeys "
        "(TfL: 1.84 billion journeys in 2024/25).")


def test_findings_as_on_the_page():
    (h1, t1), (h2, t2), (h3, t3) = findings(ROW)
    assert h1 == "Bunching is steady"
    assert t1 == ("7.4% of gaps were bunched (7.3–7.6% on each of the 4 days measured). Buses came every "
                  "12 minutes, but passengers waited about 7 minutes, not 6.")
    assert t2 == ("on routes 29, 141, 18, 38 and 55, 21–25% of gaps are bunched. On route 29, passengers "
                  "wait about half as long again as even spacing would give.")
    assert h3 == "The slowest routes need speed, not buses"
    assert t3 == ("189, 19, 98, W7 and 390 average 4.4–5.1 km/h, about half the London average of about "
                  "9 km/h. Raising route 189 to 6 km/h would need up to 27% fewer buses for the same service.")


def test_wording_follows_the_data():
    row = copy.deepcopy(ROW)
    row.update(bunching_min=0.05, bunching_max=0.09, network_kmh=7.0)
    row["most_bunched"][0]["wait_ratio"] = 1.8
    (h1, _), (_, t2), (_, t3) = findings(row)
    assert h1 == "Bunching varies day to day"
    assert "nearly twice as long as" in t2
    assert "well below the London average of about 7 km/h" in t3
    row["extra_wait_minutes"] = 0.0
    assert "almost nothing" in summary(row)


def test_period_starts_at_the_first_day_with_data():
    assert period(ROW) == "26–29 October 2026"


def test_export_views_match_the_exports():
    spec = importlib.util.spec_from_file_location("bus_export_views", ROOT / "scripts" / "bus_export_views.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = (ROOT / "analytics" / "bus_network" / "02_export_views.sql").read_text()
    assert generated == module.render(), "an export changed: run python scripts/bus_export_views.py"
