"""Turn road polls into change records (docs/endpoints/road.md).

Pure functions. Two independent histories:
  disruptions  BASELINE, NEW, UPDATE, CLEARED (gone from the feed)
  corridors    BASELINE, NEW, UPDATE, REMOVED
A disruption's fingerprint leaves out TfL's update timestamps (currentUpdateDateTime,
lastModifiedTime), which can move without anything a road user would notice changing.
"""

import hashlib

from src.validation.road import EXCLUDED, parse_utc, point


def _sha256(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _text(value):
    return " ".join((value or "").split())


def _iso(value):
    parsed = parse_utc(value)
    return parsed.isoformat() if parsed else None


def closed_streets(record):
    """Street sections TfL marks as closed (the record's `hasClosures` flag is unreliable)."""
    return sorted({s.get("name") for s in record.get("streets") or []
                   if isinstance(s, dict) and s.get("closure") not in (None, "", "Open") and s.get("name")})


def normalise_disruptions(data, poll, issues=()):
    excluded = {i.status_index for i in issues if i.level == EXCLUDED}
    flags = {}
    for issue in issues:
        if issue.level != EXCLUDED and issue.status_index is not None:
            flags.setdefault(issue.status_index, []).append(issue.rule)

    records = []
    for index, raw in enumerate(data):
        if index in excluded:
            continue
        found = point(raw)
        corridors = sorted(c.upper() for c in raw.get("corridorIds") or [])
        closed = closed_streets(raw)
        content = (raw.get("category"), raw.get("subCategory"), raw.get("severity"),
                   _text(raw.get("location")), _text(raw.get("comments")), _text(raw.get("currentUpdate")),
                   _iso(raw.get("startDateTime")), _iso(raw.get("endDateTime")), corridors, closed)
        records.append({
            **poll,
            "disruption_id": raw["id"],
            "fingerprint": _sha256(*content),
            "category": raw.get("category"),
            "sub_category": raw.get("subCategory"),
            "is_incident": raw.get("category") != "Works",
            "severity": raw.get("severity"),
            "level_of_interest": raw.get("levelOfInterest"),
            "location": _text(raw.get("location")),
            "comments": _text(raw.get("comments")),
            "current_update": _text(raw.get("currentUpdate")),
            "corridor_ids": corridors,
            "closed_streets": closed,
            "lat": found[0] if found else None,
            "lon": found[1] if found else None,
            "start_utc": _iso(raw.get("startDateTime")),
            "end_utc": _iso(raw.get("endDateTime")),
            "last_modified_utc": _iso(raw.get("lastModifiedTime")),
            "is_provisional": raw.get("isProvisional"),
            "dq_flags": sorted(flags.get(index, [])),
        })
    return sorted(records, key=lambda r: r["disruption_id"])


def normalise_corridors(data, poll):
    records = [{
        **poll,
        "corridor_id": raw["id"],
        "fingerprint": _sha256(raw.get("statusSeverity"), raw.get("statusSeverityDescription")),
        "corridor_name": raw.get("displayName"),
        "status": raw.get("statusSeverity"),
        "status_description": raw.get("statusSeverityDescription"),
    } for raw in data]
    return sorted(records, key=lambda r: r["corridor_id"])


def detect_changes(previous_state, records, is_baseline, poll, key, new="NEW", gone="CLEARED"):
    """Return (change_records, new_state). State: {key: record}."""
    current = {r[key]: r for r in records}
    if is_baseline:
        return [{**r, "change_type": "BASELINE", "previous_fingerprint": None} for r in records], current

    changes = []
    for k in sorted(set(current) | set(previous_state)):
        now, before = current.get(k), previous_state.get(k)
        if before is None:
            changes.append({**now, "change_type": new, "previous_fingerprint": None})
        elif now is None:
            # Last known content, stamped with the poll that saw it gone.
            changes.append({**before, **poll, "change_type": gone, "previous_fingerprint": before["fingerprint"]})
        elif now["fingerprint"] != before["fingerprint"]:
            changes.append({**now, "change_type": "UPDATE", "previous_fingerprint": before["fingerprint"]})
    return changes, current
