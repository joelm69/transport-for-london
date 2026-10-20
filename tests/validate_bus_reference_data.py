import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

DATASETS = {
    "bus_line_status": {
        "path": (
            "data/raw/bus_line_status_runs/date=2026-10-15/"
            "run_id=20261015T194811052221Z/bus_line_status.jsonl"
        ),
        "expected_records": 675,
        "expected_run_id": "20261015T194811052221Z",
    },
    "bus_route_metadata": {
        "path": (
            "data/raw/bus_route_metadata_runs/date=2026-10-16/"
            "run_id=20261016T180428275770Z/bus_route_metadata.jsonl"
        ),
        "expected_records": 675,
        "expected_run_id": "20261016T180428275770Z",
    },
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def read_jsonl(path):
    records = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                raise AssertionError(
                    f"{path.name}: blank line at line {line_number}"
                )
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise AssertionError(
                    f"{path.name}: invalid JSON at line {line_number}: {error}"
                ) from error
    return records


def validate_common(name, records, config):
    require(
        len(records) == config["expected_records"],
        f"{name}: record count is {len(records)}",
    )

    line_ids = [record.get("line_id") for record in records]
    require(
        all(isinstance(value, str) and value.strip() for value in line_ids),
        f"{name}: all line IDs are non-empty strings",
    )
    require(
        len(line_ids) == len(set(line_ids)),
        f"{name}: no duplicate line IDs",
    )

    run_ids = {record.get("collection_run_id") for record in records}
    require(
        run_ids == {config["expected_run_id"]},
        f"{name}: all records belong to the expected run",
    )

    require(
        all(record.get("collected_at_utc") for record in records),
        f"{name}: collection timestamps are present",
    )


def validate_line_status(records):
    for record in records:
        require_fields = (
            record.get("line_name") is not None
            and record.get("mode_name") is not None
            and isinstance(record.get("line_statuses"), list)
        )
        if not require_fields:
            raise AssertionError(
                f"Line {record.get('line_id')}: missing required fields "
                "or line_statuses is not a list"
            )

    print("PASS: bus_line_status: required fields and status lists are valid")


def validate_route_metadata(records):
    total_routes = 0

    for record in records:
        response = record.get("response")
        if not isinstance(response, dict):
            raise AssertionError(
                f"Line {record.get('line_id')}: response is not an object"
            )

        if str(response.get("id", "")) != record["line_id"]:
            raise AssertionError(
                f"Line {record['line_id']}: response ID does not match record"
            )

        sections = response.get("routeSections") or []
        if not isinstance(sections, list):
            raise AssertionError(
                f"Line {record['line_id']}: routeSections is not a list"
            )

        if record.get("route_count") != len(sections):
            raise AssertionError(
                f"Line {record['line_id']}: route_count does not match "
                "response.routeSections"
            )

        total_routes += len(sections)

    print("PASS: bus_route_metadata: response IDs match record IDs")
    print("PASS: bus_route_metadata: route counts match routeSections")
    print(f"Route sections across all lines: {total_routes}")


def main():
    loaded = {}

    for name, config in DATASETS.items():
        path = BASE_DIR / config["path"]
        if not path.is_file():
            raise FileNotFoundError(
                f"{name}: expected data file not found: {path}"
            )

        records = read_jsonl(path)
        print(f"\nValidating {name}")
        validate_common(name, records, config)
        loaded[name] = records

    validate_line_status(loaded["bus_line_status"])
    validate_route_metadata(loaded["bus_route_metadata"])

    print("\nBUS REFERENCE DATA VALIDATION PASSED")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, FileNotFoundError, OSError, KeyError) as error:
        print(f"\nVALIDATION FAILED: {error}", file=sys.stderr)
        sys.exit(1)
