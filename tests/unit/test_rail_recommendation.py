"""Rail recommendation text: figures from the row, headline wording chosen by the data."""

import copy

from src.presentation.rail_recommendation import findings, period, summary

# tfl_portfolio.rail_recommendation on 2026-10-30 (23-29 October).
ROW = {
    "first_day": "2026-10-23", "last_day": "2026-10-29",
    "disrupted_hours": 1047.08, "unplanned_hours": 563.58, "planned_hours": 483.50,
    "good_service": 0.6523, "good_service_without_planned": 0.8129,
    "planned_share": 0.4618, "unplanned_share": 0.5382, "unplanned_service_share": 0.1871,
    "train_share": 0.4456, "infrastructure_share": 0.3022, "top_lines_share": 0.4142,
    "top_lines": [
        {"line_name": "Central", "unplanned_share": 0.5713, "main_cause": "Train cancellations", "main_cause_share": 0.90},
        {"line_name": "Suffragette", "unplanned_share": 0.5251, "main_cause": "Operational Incident", "main_cause_share": 0.89},
        {"line_name": "Metropolitan", "unplanned_share": 0.4364, "main_cause": "Train cancellations", "main_cause_share": 0.52},
    ],
}


def test_summary_uses_the_figures():
    text = summary(ROW)
    assert "65.2% of the time in the 7 days to 29 October" in text
    assert "Planned works caused 46%" in text
    assert "unplanned 54% cost 18.7% of service time" in text
    assert "45% of it came from train cancellations and faults" in text


def test_findings_as_on_the_page():
    (h1, t1), (h2, t2), (h3, t3) = findings(ROW)
    assert h1 == "Most disruption is unplanned"
    assert t1 == "of 1,047 disrupted line-hours, 564 were unplanned. Without planned works, good service rises to 81.3%."
    assert h2 == "Trains, not track" and "caused 45% of unplanned disruption" in t2 and "caused 30%" in t2
    assert h3 == "Three lines, 41%"
    assert t3 == ("Central (57%, cancellations), Suffragette (53%, an operational incident) and "
                  "Metropolitan (44%, cancellations).")


def test_headlines_follow_the_data():
    row = copy.deepcopy(ROW)
    row.update(unplanned_share=0.4, train_share=0.2, infrastructure_share=0.5)
    (h1, _), (h2, t2), _ = findings(row)
    assert h1 == "Most disruption is planned works"
    assert h2 == "Track, not trains" and t2.startswith("signal, track, points and power failures caused 50%")
    assert "from signal, track, points and power failures" in summary(row)


def test_fewer_lines_and_unknown_cause():
    row = copy.deepcopy(ROW)
    row["top_lines"] = [{"line_name": "Jubilee", "unplanned_share": 0.2, "main_cause": "Faulty Platform Edge Doors"}]
    _, _, (h3, t3) = findings(row)
    assert h3.startswith("One line") and t3 == "Jubilee (20%, faulty platform edge doors)."
    row["top_lines"] = []
    assert findings(row)[2][1] == "no unplanned disruption this week."


def test_period():
    assert period(ROW) == "23–29 October 2026"
    assert period({"first_day": "2026-10-28", "last_day": "2026-11-03"}) == "28 October–3 November 2026"
