"""Roads recommendation: text from the figures."""

import copy

from src.presentation.road_recommendation import findings, period, recommendations, summary

# tfl_portfolio.road_recommendation on 2026-10-31 (data 29-30 October).
ROW = {
    "first_day": "2026-10-24", "last_day": "2026-10-30", "data_from": "2026-10-29", "days": 2,
    "delayed_share": 0.3517, "roads": 24, "roads_delayed": 10, "roads_mostly_delayed": 8,
    "mostly_delayed_with_works": 8, "mostly_delayed_works_only": 3,
    "mostly_delayed": [
        {"corridor_name": "A3", "delayed_share": 1.0, "incidents": 0, "works": 9},
        {"corridor_name": "South Circular (A205)", "delayed_share": 1.0, "incidents": 2, "works": 8},
        {"corridor_name": "North Circular (A406)", "delayed_share": 1.0, "incidents": 2, "works": 7},
        {"corridor_name": "A41", "delayed_share": 1.0, "incidents": 0, "works": 6},
    ],
    "most_works": [
        {"corridor_name": "Inner Ring", "works": 15, "delayed_share": 0.0},
        {"corridor_name": "A23", "works": 9, "delayed_share": 0.0},
        {"corridor_name": "A3", "works": 9, "delayed_share": 1.0},
    ],
    "incidents": 30, "incidents_on_main_roads": 16, "incidents_cleared": 29, "median_clear_minutes": 171.23,
    "types": [
        {"sub_category": "Vehicle breakdown", "cleared": 11, "median_minutes": 39.83},
        {"sub_category": "Vehicle collision", "cleared": 10, "median_minutes": 277.73},
        {"sub_category": "Traffic signal fault", "cleared": 2, "median_minutes": 1639.55},
    ],
    "works_ended": 40, "works_overran": 29, "works_overrun_share": 0.725, "works_overran_day": 0,
    "median_overrun_minutes": 18.92,
}


def test_summary():
    assert summary(ROW) == (
        "TfL rated 8 of its 24 main roads as seriously delayed at least half the time, and every one of "
        "them had roadworks on it. A typical incident was cleared in 2h 51m, but traffic signal faults "
        "took 27h 20m.")


def test_findings_as_on_the_page():
    (h1, t1), (h2, t2), (h3, t3) = findings(ROW)
    assert h1 == "Delayed main roads all had roadworks"
    assert t1 == ("8 of 24 main roads were rated Serious or worse at least half the time (35% of all main-road "
                  "time). All 8 had roadworks on them, and 3 had no incidents at all. Roadworks alone don't "
                  "explain it: Inner Ring had the most works (15) and was never delayed.")
    assert h2 == "Traffic signal faults take longest to clear"
    assert t2 == ("A typical incident was cleared in 2h 51m. Vehicle breakdowns took 40m, vehicle collisions "
                  "took 4h 38m and traffic signal faults took 27h 20m. 16 of the 30 incidents were on a main road.")
    assert h3 == "Roadworks end late, but mostly by minutes"
    assert t3 == ("29 of the 40 roadworks that ended (72%) were still listed after their planned end, by a "
                  "median of 19m; none overran by more than a day.")


def test_wording_follows_the_data():
    row = copy.deepcopy(ROW)
    row.update(mostly_delayed_with_works=6, works_overran_day=3, median_overrun_minutes=2000)
    (h1, t1), _, (h3, t3) = findings(row)
    assert h1 == "Some main roads stay delayed for days"
    assert "6 of them had roadworks on them" in t1
    assert h3 == "Roadworks overrun their planned end"
    assert t3.endswith("3 overran by more than a day.")
    assert recommendations(row)[-1][0] == "Follow up roadworks that overrun by more than a day"
    row["roads_mostly_delayed"] = 0
    assert findings(row)[0][0] == "Main roads were mostly free of serious delays"


def test_recommendations_name_this_weeks_roads():
    assert recommendations(ROW) == [
        ("Review the roadworks on the delayed main roads,",
         "starting with A3, South Circular (A205) and North Circular (A406), which have the most works among "
         "roads rated Serious."),
        ("Set a repair target for traffic signal faults:", "they took 27h 20m to clear, the longest of any type."),
        ("Don't chase short roadwork overruns:",
         "most end within minutes of plan; act on works that run more than a day late."),
    ]


def test_period():
    assert period(ROW) == "29–30 October 2026"
