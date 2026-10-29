"""Data-quality rules for stop disruption polls (docs/endpoints/stop_disruptions.md, section 4).

Pure functions. Levels: ERROR (no change records from the poll), EXCLUDED (this record
cannot be identified and is skipped; the rest of the poll is processed), WARN (kept, flagged).
"""

import re
from collections import defaultdict
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


def _text(record):
    return " ".join((record.get("description") or "").lower().split())


def poll_identities(data):
    """The identity of each record in one poll, by index (None: unidentifiable).

    Usually disruption_identity(). When two records share it but say different things, each
    also carries its text, so both are kept as separate notices: since 2026-10-24 TfL posts
    the Circle and District line closures at a station as two notices with the same station,
    type and start, which made every poll fail. Records that clash keep text-based identities
    only while they clash; every other notice keeps the identity it always had.
    """
    base = {i: disruption_identity(r) for i, r in enumerate(data) if isinstance(r, dict)}
    texts = defaultdict(set)
    for i, identity in base.items():
        if identity is not None:
            texts[identity].add(_text(data[i]))
    return {i: (identity + (_text(data[i]),) if identity is not None and len(texts[identity]) > 1 else identity)
            for i, identity in base.items()}


def validate_poll(data, config, collected_at):
    """Apply rules P1-P2 and D1-D8. Record-level issues carry the record index in
    `status_index`. (P3, a key clash rejecting the whole poll, was retired on 2026-10-26:
    clashing notices now get separate identities, see poll_identities.)"""
    if not isinstance(data, list):
        return [Issue(ERROR, "P1", "Response body is not a JSON array")]

    issues = []
    if not data:
        issues.append(Issue(WARN, "P2", "No records (London normally has station notices)"))

    modes = set(config["modes"])
    types = set(config["known_types"])
    appearances = set(config["known_appearances"])
    max_from = collected_at + timedelta(seconds=config["future_from_date_tolerance_seconds"])
    identities = poll_identities(data)
    seen = set()
    names = defaultdict(set)

    for index, record in enumerate(data):
        def issue(level, rule, message):
            return Issue(level, rule, message, record.get("atcoCode") if isinstance(record, dict) else None, index)

        if not isinstance(record, dict):
            issues.append(issue(EXCLUDED, "D1", "Record is not an object"))
            continue

        identity = identities.get(index)
        if identity is None or not (record.get("description") or "").strip():
            issues.append(issue(EXCLUDED, "D1", "Missing atcoCode, type, description or parseable fromDate"))
            continue
        if identity in seen:
            # The same notice twice (same station, type, start and text): keep the first.
            issues.append(issue(EXCLUDED, "D8", "Duplicate of an earlier record in this poll"))
            continue
        seen.add(identity)

        if not NAPTAN.fullmatch(record["atcoCode"]):
            issues.append(issue(WARN, "D2", f"Unexpected atcoCode format {record['atcoCode']!r}"))
        if record.get("mode") not in modes:
            issues.append(issue(WARN, "D3", f"Unknown mode {record.get('mode')!r}"))
        if record["type"] not in types or record.get("appearance") not in appearances:
            issues.append(issue(WARN, "D4", f"Unknown type {record['type']!r} or appearance "
                                            f"{record.get('appearance')!r}"))

        from_date = parse_utc(record["fromDate"])
        if record.get("appearance") == "PlannedWork":
            to_date = parse_utc(record.get("toDate"))
            if to_date is None or to_date <= from_date:
                issues.append(issue(WARN, "D5", "Planned work without a valid end after its start"))
        # Planned works are announced before they start, so only live notices must have begun.
        if from_date > max_from and record.get("appearance") != "PlannedWork":
            issues.append(issue(WARN, "D6", "fromDate is in the future"))
        names[record["atcoCode"]].add(record.get("commonName"))

    for code, values in names.items():
        if len(values) > 1:
            issues.append(Issue(WARN, "D7", f"Several station names for {code}", code))

    return issues
