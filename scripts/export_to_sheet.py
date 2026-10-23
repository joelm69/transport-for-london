"""Export one BigQuery query to one tab of the project's Google Sheet.

    python scripts/export_to_sheet.py analytics/exports/line_reliability_daily.sql line_reliability_daily
    python scripts/export_to_sheet.py ... --dry-run     # query and size check only, no Sheet access

The Sheet's ID comes from TFL_SHEET_ID (in .env locally, an environment variable on Cloud Run).
Credentials: Application Default Credentials. Locally, impersonate the export service account:

    gcloud auth application-default login \\
        --impersonate-service-account=tfl-sheets-export@transport-for-london-511318.iam.gserviceaccount.com
"""

import argparse
import os
import sys
from pathlib import Path

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import bigquery

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.exports.sheets import MAX_FILE_CELLS, SCOPES, SheetsClient, cells_after_replace, to_values  # noqa: E402

PROJECT_ID = "transport-for-london-511318"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sql", type=Path, help="query file, e.g. analytics/exports/line_reliability_daily.sql")
    parser.add_argument("tab", help="Sheet tab to replace, e.g. line_reliability_daily")
    parser.add_argument("--dry-run", action="store_true", help="query and check sizes; don't touch the Sheet")
    args = parser.parse_args()

    credentials, _ = google.auth.default(scopes=SCOPES)
    job = bigquery.Client(project=PROJECT_ID, credentials=credentials, location="US").query(args.sql.read_text())
    result = job.result()
    columns = [field.name for field in result.schema]
    values = to_values(columns, [dict(row) for row in result])
    print(f"{len(values) - 1} rows x {len(columns)} columns from {args.sql} "
          f"({job.total_bytes_processed or 0:,} bytes scanned)")

    if args.dry_run:
        return

    sheet_id = os.environ.get("TFL_SHEET_ID")
    if not sheet_id:
        sys.exit("TFL_SHEET_ID is not set (the ID in the Sheet's URL, between /d/ and /edit).")
    sheets = SheetsClient(AuthorizedSession(credentials), sheet_id)
    spreadsheet = sheets.spreadsheet()
    total = cells_after_replace(spreadsheet, args.tab, values)
    if total > MAX_FILE_CELLS:
        sys.exit(f"Stopped: the Sheet would hold {total:,} cells (limit set at {MAX_FILE_CELLS:,}).")
    written = sheets.replace(spreadsheet, args.tab, values)
    print(f"Wrote {written:,} cells to '{args.tab}' in '{spreadsheet['properties']['title']}'; "
          f"the file now holds about {total:,} cells.")


if __name__ == "__main__":
    main()
