
import csv
import math
import sys
from pathlib import Path

RUN_ID = "20261016T202642305170Z"
EXPECTED_OCCURRENCES = 55986
EXPECTED_UNIQUE_STOPS = 19985

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "processed" / f"run_id={RUN_ID}"

OCCURRENCES_FILE = DATA_DIR / "route_stop_occurrences.csv"
STOPS_FILE = DATA_DIR / "unique_bus_stops.csv"

COORDINATE_RANGES = {
    "latitude": (-90, 90),
    "longitude": (-180, 180),
}

STOP_METADATA_FIELDS = (
    "stop_name",
    "latitude",
    "longitude",
    "stop_letter",
    "parent_id",
)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise AssertionError(f"CSV has no header: {path.name}")

        return list(reader)


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def validate_coordinates(table_name, rows):
    checked = 0
    missing = 0

    for row_number, row in enumerate(rows, start=2):
        for field, (minimum, maximum) in COORDINATE_RANGES.items():
            value = (row.get(field) or "").strip()

            if not value:
                missing += 1
                continue

            try:
                coordinate = float(value)
            except ValueError as exc:
                raise AssertionError(
                    f"Invalid {field} in {table_name}, "
                    f"CSV row {row_number}: {value!r}"
                ) from exc

            if not math.isfinite(coordinate):
                raise AssertionError(
                    f"Non-finite {field} in {table_name}, "
                    f"CSV row {row_number}: {value!r}"
                )

            if not minimum <= coordinate <= maximum:
                raise AssertionError(
                    f"Out-of-range {field} in {table_name}, "
                    f"CSV row {row_number}: {value!r}"
                )

            checked += 1

    require(
        True,
        f"{table_name}: {checked} coordinates valid; "
        f"{missing} coordinate values missing",
    )


def validate_positions(occurrences):
    for row_number, row in enumerate(occurrences, start=2):
        for field in ("sequence_number", "stop_order"):
            value = (row.get(field) or "").strip()

            if not value:
                raise AssertionError(
                    f"Missing {field} on occurrence CSV row {row_number}"
                )

            try:
                number = int(value)
            except ValueError as exc:
                raise AssertionError(
                    f"Invalid {field} on occurrence CSV row "
                    f"{row_number}: {value!r}"
                ) from exc

            if number < 1:
                raise AssertionError(
                    f"{field} must be positive on occurrence CSV row "
                    f"{row_number}: {number}"
                )

    require(
        True,
        "All sequence numbers and stop positions are positive integers",
    )


def validate_metadata_consistency(occurrences, stops):
    stops_by_id = {row["stop_id"]: row for row in stops}
    mismatch_counts = {field: 0 for field in STOP_METADATA_FIELDS}

    for occurrence in occurrences:
        catalogue = stops_by_id[occurrence["stop_id"]]

        for field in STOP_METADATA_FIELDS:
            occurrence_value = (occurrence.get(field) or "").strip()
            catalogue_value = (catalogue.get(field) or "").strip()

            # Missing metadata is allowed. Compare only when both
            # tables have a populated value.
            if (
                occurrence_value
                and catalogue_value
                and occurrence_value != catalogue_value
            ):
                mismatch_counts[field] += 1

    for field, count in mismatch_counts.items():
        require(
            count == 0,
            f"Occurrence metadata agrees with catalogue for {field} "
            f"(mismatches: {count})",
        )


def main():
    if not OCCURRENCES_FILE.is_file():
        raise FileNotFoundError(
            f"Missing occurrence file: {OCCURRENCES_FILE}"
        )

    if not STOPS_FILE.is_file():
        raise FileNotFoundError(
            f"Missing unique-stop file: {STOPS_FILE}"
        )

    occurrences = read_csv(OCCURRENCES_FILE)
    stops = read_csv(STOPS_FILE)

    require(
        len(occurrences) == EXPECTED_OCCURRENCES,
        f"Route-stop occurrence count is {len(occurrences)}",
    )
    require(
        len(stops) == EXPECTED_UNIQUE_STOPS,
        f"Unique-stop count is {len(stops)}",
    )

    occurrence_keys = [
        (
            row["line_id"],
            row["direction"],
            row["branch_id"],
            row["sequence_number"],
            row["stop_order"],
        )
        for row in occurrences
    ]

    require(
        len(occurrence_keys) == len(set(occurrence_keys)),
        "No duplicate route-stop positions",
    )

    stop_ids = [row["stop_id"] for row in stops]

    require(
        all(stop_id.strip() for stop_id in stop_ids),
        "No empty stop IDs in unique-stop catalogue",
    )
    require(
        len(stop_ids) == len(set(stop_ids)),
        "No duplicate stop IDs in unique-stop catalogue",
    )
    require(
        all(row["stop_id"].strip() for row in occurrences),
        "No empty stop IDs in route-stop occurrences",
    )
    require(
        all(row["line_id"].strip() for row in occurrences),
        "No empty line IDs",
    )
    require(
        all(row["direction"].strip() for row in occurrences),
        "No empty route directions",
    )

    catalogue_ids = set(stop_ids)
    missing_ids = {
        row["stop_id"]
        for row in occurrences
        if row["stop_id"] not in catalogue_ids
    }

    require(
        not missing_ids,
        f"All route-stop IDs exist in catalogue "
        f"(missing: {len(missing_ids)})",
    )

    run_ids = {row["collection_run_id"] for row in occurrences}

    require(
        run_ids == {RUN_ID},
        f"All rows belong to collection run {RUN_ID}",
    )

    stop_run_ids = {
        row["collection_run_id"]
        for row in stops
        if "collection_run_id" in row
    }

    require(
        not stop_run_ids,
        "Unique-stop catalogue is a run-independent dimension table",
    )

    validate_positions(occurrences)
    validate_coordinates("Route-stop occurrences", occurrences)
    validate_coordinates("Unique-stop catalogue", stops)
    validate_metadata_consistency(occurrences, stops)

    print("\nDATA QUALITY VALIDATION PASSED")


if __name__ == "__main__":
    try:
        main()
    except (
        AssertionError,
        OSError,
        KeyError,
        csv.Error,
    ) as error:
        print(
            f"\nDATA QUALITY VALIDATION FAILED: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
