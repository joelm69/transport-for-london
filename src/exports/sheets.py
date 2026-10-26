"""Write BigQuery results to a tab of the project's Google Sheet (the Tableau Public source).

One Sheet file for the whole project, one tab per subject (docs/requirements.md). This module
replaces a tab's contents with a query's rows, which is idempotent: running it twice gives the
same tab. The daily window trimming and GCS archive come with the scheduled job.

The Sheets API is called directly with `requests` over an authorised session (google-auth),
so no Sheets client library is needed. Credentials are Application Default Credentials: the
export service account on Cloud Run, or a person impersonating it locally. No key files.
"""

import time
from datetime import date, datetime
from decimal import Decimal

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
)
MAX_FILE_CELLS = 7_000_000   # stop above this; Google's limit is 10 million per file
CHUNK_ROWS = 10_000          # rows per write: one request must stay under Google's ~10 MB limit
FORMULA_STARTS = ("=", "+", "-", "@")


def cell(value):
    """One value as the Sheets API takes it with USER_ENTERED input: numbers stay numbers and
    ISO dates become dates (so Tableau types them), text that would be read as a formula is
    kept as text, and missing values are blank."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def to_values(columns, rows):
    """A header row and one row per record, ready for a values update."""
    return [list(columns)] + [[cell(row[c]) for c in columns] for row in rows]


def file_cells(spreadsheet):
    """Cells allocated across every tab of a spreadsheet resource (what Google counts)."""
    return sum(
        sheet["properties"]["gridProperties"]["rowCount"] * sheet["properties"]["gridProperties"]["columnCount"]
        for sheet in spreadsheet.get("sheets", [])
    )


def cells_after_replace(spreadsheet, tab, values):
    """The file's cell count once `tab` holds exactly `values` (other tabs unchanged)."""
    others = sum(
        s["properties"]["gridProperties"]["rowCount"] * s["properties"]["gridProperties"]["columnCount"]
        for s in spreadsheet.get("sheets", []) if s["properties"]["title"] != tab
    )
    width = max((len(r) for r in values), default=0)
    return others + len(values) * width


class SheetsClient:
    """The few Sheets API calls the export needs. `session` is an authorised requests session."""

    def __init__(self, session, spreadsheet_id, timeout=60, sleep=time.sleep):
        self.session = session
        self.spreadsheet_id = spreadsheet_id
        self.timeout = timeout
        self._sleep = sleep

    def _call(self, method, path="", **kwargs):
        # Google allows about 60 writes a minute; the all-routes tabs need dozens of chunked
        # writes, so a 429 (or a passing 5xx) waits and tries again rather than failing the run.
        for attempt in range(6):
            response = self.session.request(method, f"{SHEETS_API}/{self.spreadsheet_id}{path}",
                                            timeout=self.timeout, **kwargs)
            if response.status_code not in (429, 500, 502, 503) or attempt == 5:
                break
            self._sleep(min(60, 5 * 2 ** attempt))
        if response.status_code >= 400:
            # The API's own message (e.g. "The caller does not have permission"), never credentials.
            message = response.json().get("error", {}).get("message", response.reason)
            raise RuntimeError(f"Sheets API {method} {path or '/'} failed ({response.status_code}): {message}")
        return response.json()

    def spreadsheet(self):
        return self._call("GET", params={"fields": "properties.title,sheets.properties"})

    def ensure_tab(self, spreadsheet, tab):
        if any(s["properties"]["title"] == tab for s in spreadsheet.get("sheets", [])):
            return
        self._call("POST", ":batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": tab}}}]})

    def replace(self, spreadsheet, tab, values):
        """Make `tab` hold exactly `values`: resize the grid to fit (so the file's cell count
        stays honest), clear it and write. Returns the number of cells written."""
        self.ensure_tab(spreadsheet, tab)
        sheet_id = next((s["properties"]["sheetId"] for s in self.spreadsheet()["sheets"]
                         if s["properties"]["title"] == tab))
        rows, columns = max(len(values), 1), max((len(r) for r in values), default=1)
        self._call("POST", ":batchUpdate", json={"requests": [
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {
                    "rowCount": rows, "columnCount": columns, "frozenRowCount": 1}},
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount)"}},
        ]})
        quoted = "'" + tab.replace("'", "''") + "'"
        self._call("POST", f"/values/{quoted}:clear")
        # In chunks: a large tab (the all-routes bus tabs reach ~1 million cells) is far over
        # what one request may carry.
        for start in range(0, len(values), CHUNK_ROWS):
            self._call("PUT", f"/values/{quoted}!A{start + 1}", params={"valueInputOption": "USER_ENTERED"},
                       json={"values": values[start:start + CHUNK_ROWS]})
        return rows * columns
