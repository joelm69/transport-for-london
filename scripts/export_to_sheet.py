"""Export BigQuery queries to tabs of the project's Google Sheet.

    python scripts/export_to_sheet.py --all                      # every tab in config/sheet_exports.json
    python scripts/export_to_sheet.py SQL TAB                    # one tab
    python scripts/export_to_sheet.py ... --dry-run              # query and size check only, no Sheet access
    python scripts/export_to_sheet.py --all --through-today      # include today so far (one-off, e.g.
                                                                 # to design charts before the first night)

The nightly Cloud Run Job (deploy/sheet-export/) runs `--all`. The Sheet's ID comes from
TFL_SHEET_ID (.env locally, an environment variable on Cloud Run). Credentials: Application
Default Credentials, the export service account. Locally, impersonate it:

    gcloud auth application-default login \\
        --impersonate-service-account=tfl-sheets-export@transport-for-london-511318.iam.gserviceaccount.com
"""

import argparse
import json
import os
import sys
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.exports.sheets import MAX_FILE_CELLS, SCOPES, SheetsClient, cells_after_replace, to_values  # noqa: E402

PROJECT_ID = "transport-for-london-511318"
CONFIG = ROOT / "config" / "sheet_exports.json"


COMPLETED_DAYS = "< CURRENT_DATE('Europe/London')"   # every export query's "completed days only" rule


def query_values(client, sql_path, through_today=False):
    sql = sql_path.read_text()
    if through_today:
        # Today's hours so far as well; the next nightly run (without the flag) replaces them.
        sql = sql.replace(COMPLETED_DAYS, "<= CURRENT_DATE('Europe/London')")
    job = client.query(sql)
    result = job.result()
    columns = [field.name for field in result.schema]
    values = to_values(columns, [dict(row) for row in result])
    print(f"{len(values) - 1} rows x {len(columns)} columns from {sql_path.relative_to(ROOT)} "
          f"({job.total_bytes_processed or 0:,} bytes scanned)", flush=True)
    return values


def export(bq, sheets, sql_path, tab, through_today=False):
    values = query_values(bq, sql_path, through_today)
    if sheets is None:
        return
    spreadsheet = sheets.spreadsheet()
    total = cells_after_replace(spreadsheet, tab, values)
    if total > MAX_FILE_CELLS:
        raise RuntimeError(f"Stopped before '{tab}': the Sheet would hold {total:,} cells "
                           f"(limit set at {MAX_FILE_CELLS:,}).")
    written = sheets.replace(spreadsheet, tab, values)
    print(f"Wrote {written:,} cells to '{tab}'; the file now holds about {total:,} cells.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sql", type=Path, nargs="?", help="query file, e.g. analytics/exports/line_reliability_daily.sql")
    parser.add_argument("tab", nargs="?", help="Sheet tab to replace, e.g. line_reliability_daily")
    parser.add_argument("--all", action="store_true", help="every tab in config/sheet_exports.json")
    parser.add_argument("--dry-run", action="store_true", help="query and check sizes; don't touch the Sheet")
    parser.add_argument("--through-today", action="store_true",
                        help="include today so far (one-off; the nightly run replaces it with the full day)")
    args = parser.parse_args()
    if args.all == bool(args.sql and args.tab):
        parser.error("give either --all or both SQL and TAB")

    jobs = ([(ROOT / t["sql"], t["tab"]) for t in json.loads(CONFIG.read_text())["tabs"]] if args.all
            else [(args.sql.resolve(), args.tab)])

    credentials, _ = google.auth.default(scopes=SCOPES)
    bq = bigquery.Client(project=PROJECT_ID, credentials=credentials, location="US")
    sheets = None
    if not args.dry_run:
        sheet_id = os.environ.get("TFL_SHEET_ID")
        if not sheet_id:
            sys.exit("TFL_SHEET_ID is not set (the ID in the Sheet's URL, between /d/ and /edit).")
        sheets = SheetsClient(AuthorizedSession(credentials), sheet_id)

    # One failing tab doesn't stop the others; the run still fails at the end so the job retries
    # and Cloud Run marks the execution as failed.
    failed = []
    for sql_path, tab in jobs:
        try:
            export(bq, sheets, sql_path, tab, args.through_today)
        except Exception as exc:   # noqa: BLE001 - reported, then the run exits non-zero
            print(f"FAILED '{tab}': {exc}", file=sys.stderr, flush=True)
            failed.append(tab)
    if failed:
        sys.exit(f"{len(failed)} of {len(jobs)} tabs failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
