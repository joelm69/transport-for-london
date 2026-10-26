"""Turn stop disruption polls into change records (docs/endpoints/stop_disruptions.md).

Pure functions. Unlike line status, records are independent of each other, so each
disruption_key has its own history:
  BASELINE      first valid poll of a session (full state)
  NEW_INCIDENT  a disruption_key not seen in the previous valid poll
  UPDATE        same disruption_key, different fingerprint
  RESOLVED      a disruption_key from the previous valid poll that is gone
"""

import hashlib
import json
import re
from datetime import timedelta

from src.validation.stop_disruptions import EXCLUDED, parse_utc, poll_identities

END_OF_SERVICE_TIMES = {"00:29", "01:29"}  # 01:29 London time in summer / winter


def _sha256(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def to_date_kind(appearance, to_raw, collected_at, config):
    """What a toDate means (step 4 finding): planned / rolling / end_of_day / dated / missing."""
    if appearance == "PlannedWork":
        return "planned"
    to_date = parse_utc(to_raw)
    if to_date is None:
        return "missing"
    rolling = collected_at + timedelta(hours=config["rolling_to_date_hours"])
    if abs((to_date - rolling).total_seconds()) <= config["rolling_to_date_tolerance_seconds"]:
        return "rolling"
    if to_date.strftime("%H:%M") in END_OF_SERVICE_TIMES:
        return "end_of_day"
    return "dated"


def normalise(data, poll, config, collected_at, issues=()):
    """One clean record per identifiable disruption, sorted by disruption_key.
    Records with an EXCLUDED issue are dropped; WARN rules become dq_flags."""
    excluded = {i.status_index for i in issues if i.level == EXCLUDED}
    flags = {}
    for issue in issues:
        if issue.level != EXCLUDED and issue.status_index is not None:
            flags.setdefault(issue.status_index, []).append(issue.rule)

    accessibility = re.compile(config["accessibility_pattern"], re.IGNORECASE)
    identities = poll_identities(data)
    records = []
    for index, raw in enumerate(data):
        if index in excluded:
            continue
        identity = identities[index]
        appearance = raw.get("appearance")
        description = (raw.get("description") or "").strip()
        planned_to = parse_utc(raw.get("toDate")) if appearance == "PlannedWork" else None

        records.append({
            **poll,
            "disruption_key": _sha256(*identity),
            "fingerprint": _sha256(description, appearance, planned_to.isoformat() if planned_to else ""),
            "atco_code": raw["atcoCode"],
            "station_atco_code": raw.get("stationAtcoCode"),
            "station_name": raw.get("commonName"),
            "mode": raw.get("mode"),
            "type": raw["type"],
            "appearance": appearance,
            "description": description,
            "from_utc": identity[2],
            "to_utc": planned_to.isoformat() if planned_to else None,
            "to_date_raw": raw.get("toDate"),
            "to_date_kind": to_date_kind(appearance, raw.get("toDate"), collected_at, config),
            "concerned_lines": [
                {"id": line.get("id"), "direction": line.get("direction")}
                for line in raw.get("concernedLines") or []
            ],
            "accessibility_flag": bool(accessibility.search(description)),
            "dq_flags": sorted(flags.get(index, [])),
        })
    return sorted(records, key=lambda r: r["disruption_key"])


def content_hash(records):
    """Order-insensitive hash of what matters: keys and fingerprints only.

    Shuffled record order and rolling RealTime toDates do not change it, so it
    changes exactly when a disruption appears, disappears or changes.
    """
    pairs = sorted((r["disruption_key"], r["fingerprint"]) for r in records)
    return hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()


def detect_changes(previous_state, records, is_baseline, poll):
    """Return (change_records, new_state). State: {disruption_key: record}."""
    current = {r["disruption_key"]: r for r in records}
    if is_baseline:
        return [{**r, "change_type": "BASELINE", "previous_fingerprint": None} for r in records], current

    changes = []
    for key in sorted(set(current) | set(previous_state)):
        now, before = current.get(key), previous_state.get(key)
        if before is None:
            changes.append({**now, "change_type": "NEW_INCIDENT", "previous_fingerprint": None})
        elif now is None:
            # The disruption's last known content, stamped with the poll that saw it gone.
            changes.append({**before, **poll, "change_type": "RESOLVED",
                            "previous_fingerprint": before["fingerprint"]})
        elif now["fingerprint"] != before["fingerprint"]:
            changes.append({**now, "change_type": "UPDATE", "previous_fingerprint": before["fingerprint"]})
    return changes, current
