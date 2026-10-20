
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RUN_ID = "20261016T202642305170Z"

INPUT_FILE = (
    PROJECT_ROOT
    / "data/raw/bus_route_sequences_runs"
    / "date=2026-10-16"
    / f"run_id={RUN_ID}"
    / "bus_route_sequences.jsonl"
)

OUTPUT_DIR = PROJECT_ROOT / "data/processed" / f"run_id={RUN_ID}"

ROUTE_STOP_FILE = OUTPUT_DIR / "route_stop_occurrences.csv"
UNIQUE_STOPS_FILE = OUTPUT_DIR / "unique_bus_stops.csv"

ROUTE_STOP_FIELDS = [
    "collection_run_id",
    "line_id",
    "direction",
    "branch_id",
    "sequence_number",
    "stop_order",
    "stop_id",
    "stop_name",
    "latitude",
    "longitude",
    "stop_letter",
    "parent_id",
]

STOP_FIELDS = [
    "stop_id",
    "stop_name",
    "latitude",
    "longitude",
    "stop_letter",
    "parent_id",
]

EXPECTED_LINE_DIRECTION_PAIRS = 1343
EXPECTED_ORIGINAL_OCCURRENCES = 56062
EXPECTED_DUPLICATE_SEQUENCES = 2
EXPECTED_REMOVED_OCCURRENCES = 76
EXPECTED_PROCESSED_OCCURRENCES = 55986
EXPECTED_UNIQUE_STOPS = 19985


def main():
    if not INPUT_FILE.is_file():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    route_stop_rows = []
    unique_stops = {}

    expected_run_id = RUN_ID
    seen_pairs = set()
    original_occurrences = 0
    duplicate_sequences = []
    retained_sequences = 0

    with INPUT_FILE.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue

            record = json.loads(line)

            if record.get("collection_run_id") != expected_run_id:
                raise ValueError(
                    f"Unexpected run ID on input line {line_number}"
                )

            line_id = str(record["line_id"])
            direction = str(record["direction"]).lower()
            pair = (line_id, direction)

            if pair in seen_pairs:
                raise ValueError(
                    f"Duplicate line-direction pair: {pair}"
                )

            seen_pairs.add(pair)

            response = record.get("response") or {}
            sequences = response.get("stopPointSequences") or []

            # Sequence identity is determined by its complete ordered
            # stop records, not by branch_id or individual stop IDs.
            seen_sequence_signatures = set()

            for sequence_number, sequence in enumerate(sequences, 1):
                stops = sequence.get("stopPoint") or []
                original_occurrences += len(stops)

                signature = json.dumps(
                    stops,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )

                if signature in seen_sequence_signatures:
                    duplicate_sequences.append({
                        "line_id": line_id,
                        "direction": direction,
                        "branch_id": sequence.get("branchId"),
                        "sequence_number": sequence_number,
                        "stop_count": len(stops),
                    })
                    continue

                seen_sequence_signatures.add(signature)
                retained_sequences += 1

                for stop_order, stop in enumerate(stops, 1):
                    stop_id = str(stop.get("id") or "").strip()

                    if not stop_id:
                        raise ValueError(
                            f"Missing stop ID in record {pair}, "
                            f"sequence {sequence_number}, "
                            f"position {stop_order}"
                        )

                    stop_dimension_row = {
                        "stop_id": stop_id,
                        "stop_name": stop.get("name"),
                        "latitude": stop.get("lat"),
                        "longitude": stop.get("lon"),
                        "stop_letter": stop.get("stopLetter"),
                        "parent_id": stop.get("parentId"),
                    }

                    route_stop_rows.append({
                        "collection_run_id": expected_run_id,
                        "line_id": line_id,
                        "direction": direction,
                        "branch_id": sequence.get("branchId"),
                        "sequence_number": sequence_number,
                        "stop_order": stop_order,
                        **{
                            field: stop_dimension_row[field]
                            for field in STOP_FIELDS
                        },
                    })

                    if stop_id not in unique_stops:
                        unique_stops[stop_id] = stop_dimension_row
                    else:
                        existing = unique_stops[stop_id]

                        # Reject conflicting non-null stop metadata.
                        for field in STOP_FIELDS[1:]:
                            old_value = existing.get(field)
                            new_value = stop_dimension_row.get(field)

                            if (
                                old_value is not None
                                and new_value is not None
                                and old_value != new_value
                            ):
                                raise ValueError(
                                    f"Conflicting {field} for stop "
                                    f"{stop_id}: {old_value!r} "
                                    f"vs {new_value!r}"
                                )

                            if (
                                old_value is None
                                and new_value is not None
                            ):
                                existing[field] = new_value

    removed_occurrences = sum(
        duplicate["stop_count"] for duplicate in duplicate_sequences
    )

    # Validate all expected counts before writing any output files.
    checks = {
        "line-direction pairs": (
            len(seen_pairs),
            EXPECTED_LINE_DIRECTION_PAIRS,
        ),
        "original stop occurrences": (
            original_occurrences,
            EXPECTED_ORIGINAL_OCCURRENCES,
        ),
        "exact duplicate sequences": (
            len(duplicate_sequences),
            EXPECTED_DUPLICATE_SEQUENCES,
        ),
        "removed stop occurrences": (
            removed_occurrences,
            EXPECTED_REMOVED_OCCURRENCES,
        ),
        "processed stop occurrences": (
            len(route_stop_rows),
            EXPECTED_PROCESSED_OCCURRENCES,
        ),
        "retained sequences": (
            retained_sequences,
            1403,
        ),
        "unique stop IDs": (
            len(unique_stops),
            EXPECTED_UNIQUE_STOPS,
        ),
    }

    for name, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(
                f"Validation failed for {name}: "
                f"expected {expected}, found {actual}. "
                "No output files were written."
            )

    # Write temporary files first. Replace final files only after
    # both temporary CSVs have been written successfully.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    temporary_route_file = ROUTE_STOP_FILE.with_suffix(".csv.tmp")
    temporary_stops_file = UNIQUE_STOPS_FILE.with_suffix(".csv.tmp")

    try:
        with temporary_route_file.open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(
                file, fieldnames=ROUTE_STOP_FIELDS
            )
            writer.writeheader()
            writer.writerows(route_stop_rows)

        with temporary_stops_file.open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(
                file, fieldnames=STOP_FIELDS
            )
            writer.writeheader()
            writer.writerows(
                unique_stops[key] for key in sorted(unique_stops)
            )

        temporary_route_file.replace(ROUTE_STOP_FILE)
        temporary_stops_file.replace(UNIQUE_STOPS_FILE)

    finally:
        temporary_route_file.unlink(missing_ok=True)
        temporary_stops_file.unlink(missing_ok=True)

    print("TRANSFORMATION COMPLETE")
    print(f"Line-direction pairs: {len(seen_pairs)}")
    print(f"Original stop occurrences: {original_occurrences}")
    print(f"Duplicate sequences excluded: {len(duplicate_sequences)}")
    print(f"Stop occurrences excluded: {removed_occurrences}")
    print(f"Retained sequences: {retained_sequences}")
    print(f"Processed stop occurrences: {len(route_stop_rows)}")
    print(f"Unique stop IDs: {len(unique_stops)}")

    for duplicate in duplicate_sequences:
        print(
            "EXCLUDED: "
            f"{duplicate['line_id']} | "
            f"{duplicate['direction']} | "
            f"branch={duplicate['branch_id']} | "
            f"sequence={duplicate['sequence_number']} | "
            f"stops={duplicate['stop_count']}"
        )

    print(f"Route-stop table: {ROUTE_STOP_FILE}")
    print(f"Unique-stop table: {UNIQUE_STOPS_FILE}")


if __name__ == "__main__":
    main()
