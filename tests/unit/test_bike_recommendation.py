"""Bikes recommendation: text from the figures."""

import copy

from src.presentation.bike_recommendation import findings, period, recommendations, summary

# tfl_portfolio.bike_recommendation on 2026-10-30 (data 29 October from 17:10).
ROW = {
    "first_day": "2026-10-23", "last_day": "2026-10-29", "data_from": "2026-10-29", "days": 1,
    "empty_share": 0.1097, "full_share": 0.0490, "peak_empty_share": 0.0628, "peak_full_share": 0.0285,
    "worst_hour": 23, "worst_hour_empty_share": 0.1756, "weekday_8am_empty_share": None,
    "stations": 799, "docks": 21006, "repeat_stations": 100, "top_tenth_stations": 80,
    "top_tenth_share": 0.4295, "out_of_use_docks": 1866.13, "out_of_use_share": 0.0888,
    "quarter_out_stations": 36,
    "most_empty": [
        {"station_name": "Exhibition Road Museums 1, South Kensington", "share": 0.9511},
        {"station_name": "Tallis Street, Temple", "share": 0.8973},
        {"station_name": "Mudchute DLR, Cubitt Town", "share": 0.8924},
    ],
    "most_full": [
        {"station_name": "St John's Crescent, Brixton", "share": 0.9462},
        {"station_name": "East India DLR, Blackwall", "share": 0.9364},
        {"station_name": "Waterloo Station 1, Waterloo", "share": 0.8289},
    ],
    "most_out_of_use": [
        {"station_name": "Pindar Street, Liverpool Street", "out_of_use_docks": 11.70, "docks": 24},
        {"station_name": "Crosswall, Tower", "out_of_use_docks": 10.89, "docks": 34},
        {"station_name": "Portman Square, Marylebone", "out_of_use_docks": 9.21, "docks": 17},
    ],
}


def test_summary():
    assert summary(ROW) == (
        "In the weekday peaks about 6% of stations were empty and 3% full. 100 stations were empty or "
        "full at least half the time, and about 1 in 11 docks (9%) couldn't be used.")


def test_findings_as_on_the_page():
    (h1, t1), (h2, t2), (h3, t3) = findings(ROW)
    assert h1 == "Peaks are covered; nights are not"
    assert t1 == ("About 6% of stations were empty in the weekday peaks. The worst hour was 23:00, when 18% "
                  "(about 1 in 6) were empty.")
    assert h2 == "A small group of stations fails again and again"
    assert t2 == (
        "100 stations were empty or full at least half the time. The worst 10% (80 stations) account for "
        "43% of all empty or full time. Most often empty: Exhibition Road Museums 1 (South Kensington, "
        "95%), Tallis Street (Temple, 90%) and Mudchute DLR (Cubitt Town, 89%). Most often full: "
        "St John's Crescent (Brixton, 95%), East India DLR (Blackwall, 94%) and Waterloo Station 1 "
        "(Waterloo, 83%).")
    assert h3 == "1 in 11 docks can't be used"
    assert t3 == (
        "About 1,866 docks were out of use on average: 8.9% of the network's 21,006. At 36 stations a "
        "quarter or more of the docks were out of use, including Pindar Street (Liverpool Street, 12 of "
        "24), Crosswall (Tower, 11 of 34) and Portman Square (Marylebone, 9 of 17).")


def test_wording_follows_the_data():
    row = copy.deepcopy(ROW)
    row.update(days=7, weekday_8am_empty_share=0.061, top_tenth_share=0.2)
    (_, t1), (h2, t2), _ = findings(row)
    assert t1.endswith("; by 08:00 on weekdays it was back to 6%.")
    assert h2 == "Failures are spread across the network"
    assert "at least half the time over the 7 days" in t2
    row.update(worst_hour=8, worst_hour_empty_share=0.09)
    assert findings(row)[0][0] == "Stations run empty in the busy hours"


def test_recommendations_name_this_weeks_stations():
    assert recommendations(ROW) == [
        ("Target rebalancing at the repeat-failure stations:",
         "refill Exhibition Road Museums 1 (South Kensington), Tallis Street (Temple) and Mudchute DLR "
         "(Cubitt Town); clear bikes from St John's Crescent (Brixton), East India DLR (Blackwall) and "
         "Waterloo Station 1 (Waterloo)."),
        ("Prioritise repairs at the 36 stations with a quarter or more docks out of use,",
         "starting with Pindar Street (Liverpool Street), Crosswall (Tower) and Portman Square (Marylebone)."),
        ("Don't add bikes or stations network-wide yet:", "the peaks are mostly covered, and the problems are local."),
    ]
    row = copy.deepcopy(ROW)
    row["most_empty"], row["most_full"], row["most_out_of_use"] = [], [], []
    assert [b[0] for b in recommendations(row)] == ["Don't add bikes or stations network-wide yet:"]


def test_period():
    assert period(ROW) == "29 October 2026"
    assert period({**ROW, "last_day": "2026-11-04"}) == "29 October–4 November 2026"
