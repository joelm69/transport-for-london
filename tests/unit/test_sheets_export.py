from datetime import date, datetime
from decimal import Decimal

from src.exports.sheets import cell, cells_after_replace, file_cells, to_values


def _sheet(title, rows, columns):
    return {"properties": {"title": title, "sheetId": 1,
                           "gridProperties": {"rowCount": rows, "columnCount": columns}}}


def test_values_keep_numbers_dates_and_blanks():
    rows = [{"day": date(2026, 10, 22), "line": "Central", "share": 0.2716, "incidents": 2, "cause": None}]
    assert to_values(["day", "line", "share", "incidents", "cause"], rows) == [
        ["day", "line", "share", "incidents", "cause"],
        ["2026-10-22", "Central", 0.2716, 2, ""],
    ]


def test_text_that_looks_like_a_formula_stays_text():
    assert cell("=IMPORTXML(...)") == "'=IMPORTXML(...)"
    assert cell("-3 min") == "'-3 min"
    assert cell("Signal failure") == "Signal failure"


def test_other_types():
    assert cell(Decimal("1.5")) == 1.5
    assert cell(datetime(2026, 10, 22, 7, 5)) == "2026-10-22 07:05:00"
    assert cell(True) is True


def test_cell_count_counts_the_replaced_tab_at_its_new_size():
    spreadsheet = {"sheets": [_sheet("Sheet1", 1000, 26), _sheet("line_reliability_daily", 50, 11)]}
    assert file_cells(spreadsheet) == 26_000 + 550
    values = [["h"] * 11] * 41          # header + 40 rows
    assert cells_after_replace(spreadsheet, "line_reliability_daily", values) == 26_000 + 41 * 11
    assert cells_after_replace(spreadsheet, "new_tab", values) == 26_550 + 41 * 11


def test_large_tabs_are_written_in_chunks(monkeypatch):
    import src.exports.sheets as sheets

    calls = []

    class Response:
        status_code = 200
        def json(self):
            return {"sheets": [_sheet("tab", 10, 2)]}

    class Session:
        def request(self, method, url, timeout=None, **kwargs):
            calls.append((method, url.split("/values/")[-1] if "/values/" in url else url, kwargs))
            return Response()

    monkeypatch.setattr(sheets, "CHUNK_ROWS", 4)
    client = sheets.SheetsClient(Session(), "sheet-id")
    values = [["h1", "h2"]] + [[i, i] for i in range(9)]          # header + 9 rows = 10 rows
    client.replace({"sheets": [_sheet("tab", 10, 2)]}, "tab", values)
    puts = [(target, len(kw["json"]["values"])) for method, target, kw in calls if method == "PUT"]
    assert puts == [("'tab'!A1", 4), ("'tab'!A5", 4), ("'tab'!A9", 2)]


def test_rate_limited_writes_wait_and_retry():
    import src.exports.sheets as sheets

    statuses, waits = [429, 429, 200], []

    class Response:
        def __init__(self, status):
            self.status_code = status
        def json(self):
            return {}

    class Session:
        def request(self, method, url, timeout=None, **kwargs):
            return Response(statuses.pop(0))

    client = sheets.SheetsClient(Session(), "sheet-id", sleep=waits.append)
    client._call("PUT", "/values/x")
    assert waits == [5, 10] and statuses == []
