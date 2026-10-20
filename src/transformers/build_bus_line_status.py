
import csv
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "20261015T194811052221Z"

INPUT_FILE = (
    PROJECT_ROOT
    / "data/raw/bus_line_status_runs"
    / "date=2026-10-15"
    / f"run_id={RUN_ID}"
    / "bus_line_status.jsonl"
)

OUTPUT_DIR = PROJECT_ROOT / "data/processed" / f"run_id={RUN_ID}"
OUTPUT_FILE = OUTPUT_DIR / "bus_line_status.csv"

FIELDS = [
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
]


def parse_collection_timestamp(value, line_id):
    """Validate an ISO-8601 collection timestamp with a timezone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Missing collection timestamp for line {line_id}"
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid collection timestamp for line {line_id}: {value}"
        ) from exc

    if parsed.utcoffset() is None:
        raise ValueError(
            f"Collection timestamp must include a timezone "
            f"for line {line_id}"
        )

    return value


def validate_status(status, line_id):
    """Validate the fields used by the flattened status columns."""
    if status is not None and not isinstance(status, dict):
        raise ValueError(
            f"Invalid status object for line {line_id}"
        )

    if status is None:
        return

    severity = status.get("statusSeverity")

    if severity is not None and (
        isinstance(severity, bool)
        or not isinstance(severity, (int, float))
    ):
        raise ValueError(
            f"Invalid status severity for line {line_id}"
        )

    for field in ("statusSeverityDescription", "reason"):
        value = status.get(field)

        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"Invalid {field} for line {line_id}"
            )


def main():
    if not INPUT_FILE.is_file():
        raise FileNotFoundError(
            f"Input file not found: {INPUT_FILE}"
        )

    rows = []
    seen_line_ids = set()
    expected_run_ids = set()
    snapshot_timestamps = set()

    with INPUT_FILE.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                raise ValueError(
                    f"Blank input line: {line_number}"
                )

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on input line {line_number}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Input line {line_number} must contain a JSON object"
                )

            run_id = record.get("collection_run_id")
            line_id = str(record.get("line_id") or "").strip()
            statuses = record.get("line_statuses")

            if run_id != RUN_ID:
                raise ValueError(
                    f"Unexpected run ID on line {line_number}: {run_id}"
                )

            if not line_id:
                raise ValueError(
                    f"Missing line ID on line {line_number}"
                )

            if line_id in seen_line_ids:
                raise ValueError(f"Duplicate line ID: {line_id}")

            if not isinstance(statuses, list):
                raise ValueError(
                    f"line_statuses must be a list for line {line_id}"
                )

            collected_at_utc = parse_collection_timestamp(
                record.get("collected_at_utc"),
                line_id,
            )

            snapshot_timestamps.add(collected_at_utc)
            seen_line_ids.add(line_id)
            expected_run_ids.add(run_id)

            # Preserve a line even when it has no status entries.
            status_items = statuses if statuses else [None]

            for status_index, status in enumerate(
                status_items, start=1
            ):
                validate_status(status, line_id)
                status = status or {}

                created = status.get("created")

                # Exclude TfL's year-0001 placeholder from the
                # analytical timestamp while preserving it in JSON.
                if str(created or "").startswith("0001-01-01"):
                    cleaned_created = None
                else:
                    cleaned_created = created

                rows.append({
                    "collection_run_id": run_id,
                    "collected_at_utc": collected_at_utc,
                    "line_id": line_id,
                    "line_name": record.get("line_name"),
                    "mode_name": record.get("mode_name"),
                    "status_index": (
                        status_index if statuses else None
                    ),
                    "status_severity": status.get("statusSeverity"),
                    "status_severity_description": (
                        status.get("statusSeverityDescription")
                    ),
                    "status_reason": status.get("reason"),
                    "status_created_at": cleaned_created,
                    "validity_periods_json": json.dumps(
                        status.get("validityPeriods") or [],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "disruption_json": json.dumps(
                        status.get("disruption"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "status_details_json": json.dumps(
                        status,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                })

    if len(seen_line_ids) != 675:
        raise ValueError(
            f"Expected 675 unique lines; found {len(seen_line_ids)}"
        )

    if expected_run_ids != {RUN_ID}:
        raise ValueError(
            f"Unexpected run IDs: {expected_run_ids}"
        )

    if len(snapshot_timestamps) != 1:
        raise ValueError(
            "Expected one collection timestamp for this snapshot; "
            f"found {len(snapshot_timestamps)}"
        )

    if not rows:
        raise ValueError("No transformed rows produced.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = OUTPUT_FILE.with_suffix(".csv.tmp")

    try:
        with temporary_file.open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=FIELDS,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(rows)

        # Replace the previous output only after writing completes.
        temporary_file.replace(OUTPUT_FILE)
    finally:
        temporary_file.unlink(missing_ok=True)

    print("TRANSFORMATION COMPLETE")
    print(f"Unique bus lines: {len(seen_line_ids)}")
    print(f"Snapshot timestamps: {len(snapshot_timestamps)}")
    print(f"Status rows: {len(rows)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
