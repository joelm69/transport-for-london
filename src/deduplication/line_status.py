"""Turn line status polls into change records (docs/endpoints/line_status.md).

Pure functions: given the previous state and a poll, return the change
records and the new state. Reprocessing the same polls in the same order
always produces the same records (check C2).

change_type values:
  BASELINE      first valid poll of a collection session (full state)
  NEW_INCIDENT  a non-good status with a new incident_key
  UPDATE        same incident_key, but severity or reason changed
  RESOLVED      line returned to a good status
  ONGOING       unchanged status, re-emitted because another status on the
                same line changed (so a line's latest rows = its full state)
"""

import hashlib

from src.validation.line_status import parse_utc


def _sha256(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def severity_class(severity, classes):
    if severity in classes["good"]:
        return "good"
    if severity in classes["closed"]:
        return "closed"
    return "disrupted"


def flatten_statuses(data, poll, classes, issues=()):
    """One record per (line, status). `poll` has poll_id, collected_at_utc,
    collection_window. WARN issues are attached as dq_flags."""
    flags = {}
    for issue in issues:
        if issue.line_id is not None:
            key = (issue.line_id, issue.status_index)
            flags.setdefault(key, []).append(issue.rule)

    records = []
    for line in sorted(data, key=lambda item: item["id"]):
        for index, status in enumerate(line["lineStatuses"]):
            severity = status.get("statusSeverity")
            reason = status.get("reason") or ""
            periods = status.get("validityPeriods") or []
            from_raw = periods[0].get("fromDate") if periods else None
            from_date = parse_utc(from_raw)
            sev_class = severity_class(severity, classes)

            incident_key = (
                _sha256(line["id"], from_date.isoformat())
                if from_date and sev_class != "good" else None
            )

            records.append({
                **poll,
                "line_id": line["id"],
                "line_name": line.get("name"),
                "mode": line.get("modeName"),
                "status_index": index,
                "severity": severity,
                "severity_description": status.get("statusSeverityDescription"),
                "severity_class": sev_class,
                "reason": reason,
                "incident_key": incident_key,
                "status_fingerprint": _sha256(line["id"], severity, reason),
                "event_from_utc": from_date.isoformat() if from_date else None,
                "to_date_raw": periods[0].get("toDate") if periods else None,
                "dq_flags": sorted(
                    flags.get((line["id"], index), [])
                    + flags.get((line["id"], None), [])
                ),
            })
    return records


def detect_changes(previous_state, statuses, is_baseline):
    """Compare this poll's statuses with the previous state.

    previous_state: {line_id: [status records]} from the last valid poll.
    Returns (change_records, new_state).
    """
    current = {}
    for record in statuses:
        current.setdefault(record["line_id"], []).append(record)

    changes = []

    for line_id in sorted(current):
        now = current[line_id]
        before = previous_state.get(line_id)

        if is_baseline or before is None:
            changes.extend(_change(r, "BASELINE", [], []) for r in now)
            continue

        before_fps = sorted(r["status_fingerprint"] for r in before)
        now_fps = sorted(r["status_fingerprint"] for r in now)
        if before_fps == now_fps:
            continue

        before_keys = {r["incident_key"] for r in before if r["incident_key"]}
        now_keys = {r["incident_key"] for r in now if r["incident_key"]}
        resolved = sorted(before_keys - now_keys)

        for record in now:
            if record["status_fingerprint"] in before_fps:
                change_type = "ONGOING"
            elif record["severity_class"] == "good":
                change_type = "RESOLVED"
            elif record["incident_key"] and record["incident_key"] in before_keys:
                change_type = "UPDATE"
            else:
                change_type = "NEW_INCIDENT"
            changes.append(_change(record, change_type, before_fps, resolved))

    return changes, current


def _change(record, change_type, previous_fingerprints, resolved_keys):
    return {
        **record,
        "change_type": change_type,
        "previous_fingerprints": previous_fingerprints,
        "resolved_incident_keys": resolved_keys,
    }
