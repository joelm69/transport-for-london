
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20261015T194811052221Z"

RAW_FILE = (
    PROJECT_ROOT
    / "data/raw/bus_line_status_runs"
    / "date=2026-10-15"
    / f"run_id={RUN_ID}"
    / "bus_line_status.jsonl"
)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "data/processed"
    / f"run_id={RUN_ID}"
    / "bus_line_status.csv"
)

EXPECTED_LINES = 675
EXPECTED_STATUS_ROWS = 789

REQUIRED_COLUMNS = {
    "collection_run_id",
    "collected_at_utc",
    "line_id",
    "line_name",
    "mode_name",
    "status_index",
    "status_severity",
    "status_severity_description",
    "status_reason",
    "status_created_at",
    "validity_periods_json",
    "disruption_json",
    "status_details_json",
}


def fail(message):
    raise AssertionError(message)


def main():
    if not RAW_FILE.is_file():
        fail(f"Raw input file missing: {RAW_FILE}")

    if not OUTPUT_FILE.is_file():
        fail(f"Transformed output missing: {OUTPUT_FILE}")

    # Validate raw input records and line IDs.
    raw_records = []

    with RAW_FILE.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                fail(f"Blank raw input line: {line_number}")

            try:
                raw_records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                fail(f"Invalid JSON on raw line {line_number}: {exc}")

    raw_line_ids = [record.get("line_id") for record in raw_records]

    if len(raw_records) != EXPECTED_LINES:
        fail(
            f"Expected {EXPECTED_LINES} raw records; "
            f"got {len(raw_records)}"
        )

    if any(not line_id for line_id in raw_line_ids):
        fail("Raw input contains a missing line ID")

    if len(set(raw_line_ids)) != EXPECTED_LINES:
        fail("Raw input contains duplicate line IDs")

    for record in raw_records:
        if record.get("collection_run_id") != RUN_ID:
            fail("Raw input contains an unexpected collection run ID")

    expected_status_count = sum(
        len(record.get("line_statuses") or [])
        for record in raw_records
    )

    # Read and validate transformed output.
    with OUTPUT_FILE.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        columns = set(reader.fieldnames or [])

        if not REQUIRED_COLUMNS.issubset(columns):
            fail(f"Missing output columns: {REQUIRED_COLUMNS - columns}")

        rows = list(reader)

    if len(rows) != EXPECTED_STATUS_ROWS:
        fail(
            f"Expected {EXPECTED_STATUS_ROWS} output rows; "
            f"got {len(rows)}"
        )

    if len(rows) != expected_status_count:
        fail(
            f"Status count mismatch: raw={expected_status_count}, "
            f"output={len(rows)}"
        )

    output_line_ids = {row["line_id"] for row in rows}

    if output_line_ids != set(raw_line_ids):
        fail("Output line coverage differs from the raw input")

    seen_keys = set()
    severity_counts = {}
    blank_cleaned_timestamps = 0
    preserved_original_placeholders = 0
    uncleaned_placeholders = 0

    for row_number, row in enumerate(rows, start=2):
        if row["collection_run_id"] != RUN_ID:
            fail(f"Unexpected run ID on output row {row_number}")

        if not row["line_id"]:
            fail(f"Missing line ID on output row {row_number}")

        if not row["collected_at_utc"]:
            fail(f"Missing collection timestamp on output row {row_number}")

        try:
            details = json.loads(row["status_details_json"])
            json.loads(row["validity_periods_json"])
            json.loads(row["disruption_json"])
        except json.JSONDecodeError as exc:
            fail(f"Invalid JSON on output row {row_number}: {exc}")

        if not isinstance(details, dict):
            fail(f"Status details must be a JSON object on row {row_number}")


       

        expected_severity = details.get("statusSeverity")
        actual_severity = row["status_severity"]

        if str(expected_severity if expected_severity is not None else "") != actual_severity:
            fail(f"Status severity mismatch on output row {row_number}")

        expected_description = (
            details.get("statusSeverityDescription") or ""
        )
        if expected_description != row["status_severity_description"]:
            fail(f"Status description mismatch on output row {row_number}")

        expected_reason = details.get("reason") or ""
        if expected_reason != row["status_reason"]:
            fail(f"Status reason mismatch on output row {row_number}")


        # Timestamp-cleaning checks.
        cleaned_timestamp = row["status_created_at"]
        original_created = str(details.get("created") or "")

        if not cleaned_timestamp:
            blank_cleaned_timestamps += 1

        if original_created.startswith("0001-01-01"):
            preserved_original_placeholders += 1

        if cleaned_timestamp.startswith("0001-01-01"):
            uncleaned_placeholders += 1

        # Validate status fields and unique line/status keys.
        if details:
            if row["status_severity"] == "":
                fail(f"Missing severity on output row {row_number}")

            if row["status_severity_description"] == "":
                fail(
                    f"Missing severity description on output row "
                    f"{row_number}"
                )

        key = (
            row["collection_run_id"],
            row["line_id"],
            row["status_index"],
        )

        if key in seen_keys:
            fail(f"Duplicate line/status key: {key}")

        seen_keys.add(key)

        severity = row["status_severity"] or "(no status)"
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    # Enforce the known timestamp-cleaning expectations for this snapshot.
    if blank_cleaned_timestamps != EXPECTED_STATUS_ROWS:
        fail(
            f"Expected {EXPECTED_STATUS_ROWS} blank cleaned timestamps; "
            f"got {blank_cleaned_timestamps}"
        )

    if preserved_original_placeholders != EXPECTED_STATUS_ROWS:
        fail(
            f"Expected {EXPECTED_STATUS_ROWS} preserved original "
            f"placeholders; got {preserved_original_placeholders}"
        )

    if uncleaned_placeholders != 0:
        fail(
            f"Found {uncleaned_placeholders} uncleaned placeholder "
            "timestamps"
        )

    print("BUS LINE-STATUS VALIDATION PASSED")
    print(f"Raw records: {len(raw_records)}")
    print(f"Unique bus lines: {len(output_line_ids)}")
    print(f"Raw nested statuses: {expected_status_count}")
    print(f"Output rows: {len(rows)}")
    print(f"Unique line/status keys: {len(seen_keys)}")
    print(f"Status severity counts: {severity_counts}")
    print(f"Blank cleaned timestamps: {blank_cleaned_timestamps}")
    print(
        "Original placeholders preserved: "
        f"{preserved_original_placeholders}"
    )
    print(f"Uncleaned placeholders: {uncleaned_placeholders}")


if __name__ == "__main__":
    main()
