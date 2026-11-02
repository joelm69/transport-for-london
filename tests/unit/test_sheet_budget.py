import pytest

from src.exports.budget import blank_columns, collapse_older, fit, xlsx_bytes


def tab(days, rows_per_day, width=4):
    return [["day"] + [f"c{i}" for i in range(width - 1)]] + [
        [f"2026-10-{d:02d}"] + [f"value {d}-{r}-{i}" for i in range(width - 1)]
        for d in days for r in range(rows_per_day)]


def by_rows(tabs):   # a stand-in for the Excel size: 100 bytes a row
    return 100 * sum(len(values) - 1 for values in tabs.values())


def test_blank_keeps_the_column_but_empties_it():
    values = blank_columns([["day", "stop_name", "km"], ["2026-10-26", "Victoria", 4.2]], {"stop_name"})
    assert values == [["day", "stop_name", "km"], ["2026-10-26", "", 4.2]]


def test_trims_oldest_days_in_order_and_stops_at_the_budget():
    tabs = {"routes": tab(range(20, 30), 10), "vehicles": tab(range(20, 30), 10), "paths": tab([1], 50)}
    rules = {"vehicles": {"trim": {"order": 1, "min_days": 2}}, "routes": {"trim": {"order": 2, "min_days": 7}}}
    logs = []
    size = fit(tabs, rules, budget=14_000, measure=by_rows, log=logs.append)
    assert size <= 14_000
    assert sorted({r[0] for r in tabs["vehicles"][1:]}) == ["2026-10-28", "2026-10-29"]   # trimmed first, to its minimum
    assert len({r[0] for r in tabs["routes"][1:]}) == 7 and tabs["routes"][-1][0] == "2026-10-29"   # newest days kept
    assert len(tabs["paths"]) == 51   # no trim rule: untouched
    assert any("dropped 2026-10-20 from 'vehicles'" in line for line in logs)


def test_already_small_sheet_is_left_alone():
    tabs = {"routes": tab(range(20, 23), 2)}
    assert fit(tabs, {"routes": {"trim": {"order": 1, "min_days": 1}}}, budget=10_000, measure=by_rows,
               log=lambda _: None) == 600
    assert len(tabs["routes"]) == 7


def test_refuses_when_minimums_cannot_fit():
    tabs = {"routes": tab(range(20, 30), 10)}
    with pytest.raises(RuntimeError, match="even with every tab at its minimum"):
        fit(tabs, {"routes": {"trim": {"order": 1, "min_days": 5}}}, budget=1_000, measure=by_rows, log=lambda _: None)


def test_excel_size_is_measured():
    small, large = xlsx_bytes({"t": tab([1], 10)}), xlsx_bytes({"t": tab(range(1, 20), 200)})
    assert 0 < small < large


RULE = {"keep_days": 7, "keys": ["day", "route", "direction", "towards"], "column": "time_band",
        "label": "0 All day", "weight": "polls", "weighted": ["typical_gap_minutes", "bunching_rate", "average_wait_minutes"]}
HEADER = ["day", "route", "direction", "towards", "time_band", "polls", "typical_gap_minutes", "bunching_rate",
          "average_wait_minutes"]


def tableau(rows, column):   # the dashboard's formula: SUM(x * polls) / SUM(polls), over rows with a value
    pairs = [(r[HEADER.index(column)], r[HEADER.index("polls")]) for r in rows if r[HEADER.index(column)] != ""]
    return sum(v * p for v, p in pairs) / sum(p for _, p in pairs)


def test_older_bus_days_merge_bands_without_changing_the_dashboard_figures():
    old = [["2026-10-20", "29", "inbound", "Wood Green", "1 Night (00-05)", 8, 12.5, 0.1, 7.0],
           ["2026-10-20", "29", "inbound", "Wood Green", "3 Morning peak (07-10)", 6, 6.0, 0.3, 4.0],
           ["2026-10-20", "29", "inbound", "Wood Green", "4 Midday (10-16)", 12, 8.0, 0.2, ""]]
    recent = ["2026-11-01", "29", "inbound", "Wood Green", "1 Night (00-05)", 8, 12.0, 0.1, 6.5]
    merged = collapse_older([HEADER] + old + [recent], RULE, today="2026-11-02")
    assert len(merged) == 3 and merged[-1] == recent   # the last 7 days keep their bands
    [row] = [r for r in merged[1:] if r[0] == "2026-10-20"]
    assert row[4] == "0 All day" and row[5] == 26
    for column in ("typical_gap_minutes", "bunching_rate", "average_wait_minutes"):
        assert tableau([row], column) == pytest.approx(tableau(old, column), abs=1e-4)


def test_rounding_keeps_integers_and_skips_blanks():
    from src.exports.budget import round_columns
    values = round_columns([["lat", "km", "name"], [51.497771, 140.7, "x"], ["", 3.2, "y"]], {"lat": 4, "km": 0})
    assert values == [["lat", "km", "name"], [51.4978, 141, "x"], ["", 3, "y"]]
