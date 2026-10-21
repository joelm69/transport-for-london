"""Data-quality rules for stop disruption polls (docs/endpoints/stop_disruptions.md, section 4).

Pure functions. Levels: ERROR (no change records from the poll), EXCLUDED (this record
cannot be identified and is skipped; the rest of the poll is processed), WARN (kept, flagged).
"""

import re
from collections import Counter, defaultdict
from datetime import timedelta

from src.validation.line_status import ERROR, WARN, Issue, has_errors, parse_utc  # noqa: F401

EXCLUDED = "EXCLUDED"
NAPTAN = re.compile(r"\d{3}[0-9A-Z]{4,12}")


def disruption_identity(record):
    """(atcoCode, type, fromDate as UTC) or None if the record cannot be identified."""
    from_date = parse_utc(record.get("fromDate"))
    if not (record.get("atcoCode") and record.get("type") and from_date):
        return None
    return record["atcoCode"], record["type"], from_date.isoformat()


def validate_poll(data, config, collected_at):
    """Apply rules P1-P3 and D1-D7. Record-level issues carry the record index in
    `status_index`."""
    if not isinstance(data, list):
        return [Issue(ERROR, "P1", "Response body is not a JSON array")]

    issues = []
    if not data:
        issues.append(Issue(WARN, "P2", "No records (London normally has station notices)"))

    modes = set(config["modes"])
    types = set(config["known_types"])
    appearances = set(config["known_appearances"])
    max_from = collected_at + timedelta(seconds=config["future_from_date_tolerance_seconds"])
    identities = Counter()
    names = defaultdict(set)

    for index, record in enumerate(data):
        def issue(level, rule, message):
            return Issue(level, rule, message, record.get("atcoCode") if isinstance(record, dict) else None, index)

        if not isinstance(record, dict):
            issues.append(issue(EXCLUDED, "D1", "Record is not an object"))
            continue

        identity = disruption_identity(record)
        if identity is None or not (record.get("description") or "").strip():
            issues.append(issue(EXCLUDED, "D1", "Missing atcoCode, type, description or parseable fromDate"))
            continue
        identities[identity] += 1

        if not NAPTAN.fullmatch(record["atcoCode"]):
            issues.append(issue(WARN, "D2", f"Unexpected atcoCode format {record['atcoCode']!r}"))
        if record.get("mode") not in modes:
            issues.append(issue(WARN, "D3", f"Unknown mode {record.get('mode')!r}"))
        if record["type"] not in types or record.get("appearance") not in appearances:
            issues.append(issue(WARN, "D4", "Unknown type or appearance"))

        from_date = parse_utc(record["fromDate"])
        if record.get("appearance") == "PlannedWork":
            to_date = parse_utc(record.get("toDate"))
            if to_date is None or to_date <= from_date:
                issues.append(issue(WARN, "D5", "Planned work without a valid end after its start"))
        if from_date > max_from:
            issues.append(issue(WARN, "D6", "fromDate is in the future"))
        names[record["atcoCode"]].add(record.get("commonName"))

    clashes = [key for key, count in identities.items() if count > 1]
    if clashes:
        issues.append(Issue(ERROR, "P3", f"{len(clashes)} disruption_key clash(es), e.g. {clashes[0]}"))

    for code, values in names.items():
        if len(values) > 1:
            issues.append(Issue(WARN, "D7", f"Several station names for {code}", code))

    return issues
