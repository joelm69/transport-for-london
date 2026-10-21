"""Data-quality rules for line status polls (docs/endpoints/line_status.md, section 4).

Pure functions: no network, no files. Each rule returns Issues instead of
raising, so the collector decides what an ERROR or WARN means for storage.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

ERROR = "ERROR"
WARN = "WARN"


@dataclass(frozen=True)
class Issue:
    level: str
    rule: str
    message: str
    line_id: str | None = None
    status_index: int | None = None


def parse_utc(value):
    """Parse a TfL ISO timestamp; return an aware datetime or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def validate_poll(data, expected_line_ids, severity_codes, good_severities,
                  collected_at, future_tolerance_seconds=300):
    """Apply rules P1–P3 and S1–S8 to one poll's response.

    severity_codes: {mode_name: {code: description}}; a mode missing from
    it falls back to the codes of any mode (all rail modes share them).
    """
    if not isinstance(data, list):
        return [Issue(ERROR, "P1", "Response body is not a JSON array")]

    issues = []
    expected = set(expected_line_ids)
    seen = []

    for line in data:
        if not isinstance(line, dict) or not line.get("id"):
            issues.append(Issue(ERROR, "P1", "Line entry without an id"))
            continue
        seen.append(line["id"])

    missing = sorted(expected - set(seen))
    duplicates = sorted({line_id for line_id in seen if seen.count(line_id) > 1})
    unknown = sorted(set(seen) - expected)

    if missing:
        issues.append(Issue(ERROR, "P2", f"Missing lines: {', '.join(missing)}"))
    if duplicates:
        issues.append(Issue(ERROR, "P2", f"Duplicate lines: {', '.join(duplicates)}"))
    for line_id in unknown:
        issues.append(Issue(WARN, "P2", "Unexpected line ID", line_id))

    any_mode_codes = next(iter(severity_codes.values()), {})
    max_from = collected_at + timedelta(seconds=future_tolerance_seconds)

    for line in data:
        if not isinstance(line, dict) or not line.get("id"):
            continue
        line_id = line["id"]
        statuses = line.get("lineStatuses")

        if not isinstance(statuses, list) or not statuses:
            issues.append(Issue(ERROR, "P3", "Line has no statuses", line_id))
            continue

        codes = severity_codes.get(line.get("modeName"), any_mode_codes)

        for index, status in enumerate(statuses):
            issues.extend(_validate_status(
                status, line_id, index, codes, good_severities, max_from
            ))

    return issues


def _validate_status(status, line_id, index, codes, good_severities, max_from):
    def issue(level, rule, message):
        return Issue(level, rule, message, line_id, index)

    if not isinstance(status, dict):
        return [issue(ERROR, "P3", "Status is not an object")]

    found = []
    severity = status.get("statusSeverity")
    reason = status.get("reason") or ""
    periods = status.get("validityPeriods") or []

    if severity not in codes:
        found.append(issue(WARN, "S1", f"Unknown severity code {severity!r}"))
    elif codes[severity] != status.get("statusSeverityDescription"):
        found.append(issue(WARN, "S2", "Severity description does not match code"))

    if status.get("lineId") is not None and status["lineId"] != line_id:
        found.append(issue(ERROR, "S3", f"status.lineId {status['lineId']!r} != line id"))

    if severity in good_severities:
        if reason or periods:
            found.append(issue(WARN, "S8", "Good status has a reason or validity period"))
        return found

    if not reason.strip():
        found.append(issue(WARN, "S4", "Disrupted status has no reason"))

    from_date = parse_utc(periods[0].get("fromDate")) if periods else None
    if from_date is None:
        found.append(issue(WARN, "S5", "No parseable validity fromDate"))
    else:
        if periods[0].get("isNow") is not True:
            found.append(issue(WARN, "S6", "Validity period isNow is not true"))
        if from_date > max_from:
            found.append(issue(WARN, "S7", "fromDate is in the future"))

    return found


def has_errors(issues):
    return any(issue.level == ERROR for issue in issues)
