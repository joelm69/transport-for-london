"""Data-quality rules for road polls (docs/endpoints/road.md, section 4).

Pure functions. The two feeds are checked separately: a failure in one never stops the other.
Levels: ERROR (no change records from that feed this poll), EXCLUDED (the record cannot be
identified and is skipped), WARN (kept, flagged).
"""

import json

from src.validation.line_status import ERROR, WARN, Issue, has_errors, parse_utc  # noqa: F401

EXCLUDED = "EXCLUDED"


def point(record):
    """(lat, lon) from TfL's "[lon,lat]" text, or None."""
    try:
        lon, lat = json.loads(record.get("point") or "")
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def validate_disruptions(data, config):
    """Rules P1-P3 and R1-R4 for /Road/all/Disruption."""
    if not isinstance(data, list):
        return [Issue(ERROR, "P1", "Disruption response is not a JSON array")]
    issues = []
    if not data:
        issues.append(Issue(WARN, "P2", "No road disruptions (London normally has over 100)"))
    ids = [r.get("id") for r in data if isinstance(r, dict) and r.get("id")]
    if len(ids) != len(set(ids)):
        return issues + [Issue(ERROR, "P3", "Disruption id repeated within the poll")]

    severities = set(config["known_severities"])
    (lat_min, lat_max), (lon_min, lon_max) = config["london_bounds"]["lat"], config["london_bounds"]["lon"]
    for index, record in enumerate(data):
        def issue(level, rule, message):
            return Issue(level, rule, message, record.get("id") if isinstance(record, dict) else None, index)

        if not isinstance(record, dict) or not record.get("id"):
            issues.append(issue(EXCLUDED, "R1", "Disruption without an id"))
            continue
        if record.get("severity") not in severities:
            issues.append(issue(WARN, "R2", f"Unknown severity {record.get('severity')!r}"))
        if parse_utc(record.get("startDateTime")) is None:
            issues.append(issue(WARN, "R3", "startDateTime missing or unparseable"))
        found = point(record)
        if found is None or not (lat_min <= found[0] <= lat_max and lon_min <= found[1] <= lon_max):
            issues.append(issue(WARN, "R4", f"Location {record.get('point')!r} missing or outside London"))
    return issues


def validate_corridors(data, config):
    """Rules C1-C3 for /Road/all/Status."""
    if not isinstance(data, list) or not data:
        return [Issue(ERROR, "C1", "Corridor response is not a non-empty JSON array")]
    ids = [r.get("id") for r in data if isinstance(r, dict)]
    if None in ids or len(ids) != len(set(ids)):
        return [Issue(ERROR, "C2", "Corridor without an id, or an id repeated")]
    statuses = set(config["known_corridor_statuses"])
    return [Issue(WARN, "C3", f"Unknown corridor status {r.get('statusSeverity')!r}", r["id"], i)
            for i, r in enumerate(data) if r.get("statusSeverity") not in statuses]
