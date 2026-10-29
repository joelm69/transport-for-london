"""Turn BikePoint polls into change records (docs/endpoints/bikepoint.md).

Pure functions. Each station has its own history:
  BASELINE     first valid poll of a session (full state)
  NEW_STATION  a station not seen in the previous valid poll
  UPDATE       same station, different counts or flags
  REMOVED      a station from the previous valid poll that is gone
"""

import hashlib

from src.validation.bikepoint import EXCLUDED, counts, parse_utc, properties


def _sha256(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def normalise(data, poll, config, issues=()):
    """One clean record per readable station, sorted by station_id.
    Stations with an EXCLUDED issue are dropped; WARN rules become dq_flags."""
    excluded = {i.status_index for i in issues if i.level == EXCLUDED}
    flags = {}
    for issue in issues:
        if issue.level != EXCLUDED and issue.status_index is not None:
            flags.setdefault(issue.status_index, []).append(issue.rule)

    records = []
    for index, raw in enumerate(data):
        if index in excluded:
            continue
        props = properties(raw)
        found = counts(raw, config)
        installed, locked = props.get("Installed") == "true", props.get("Locked") == "true"
        modified = max((parse_utc(p.get("modified")) for p in raw.get("additionalProperties") or []
                        if parse_utc(p.get("modified"))), default=None)
        records.append({
            **poll,
            "station_id": raw["id"],
            "fingerprint": _sha256(*(found[name] for name in config["count_fields"].values()),
                                   installed, locked),
            "station_name": raw.get("commonName"),
            **found,
            # Neither holding a bike nor free: broken or reserved docks. Derived, not reported by TfL.
            "out_of_use_docks": max(found["docks"] - found["bikes"] - found["empty_docks"], 0),
            "installed": installed,
            "locked": locked,
            "temporary": props.get("Temporary") == "true",
            "lat": raw.get("lat"),
            "lon": raw.get("lon"),
            "modified_utc": modified.isoformat() if modified else None,
            "dq_flags": sorted(flags.get(index, [])),
        })
    return sorted(records, key=lambda r: r["station_id"])


def detect_changes(previous_state, records, is_baseline, poll):
    """Return (change_records, new_state). State: {station_id: record}."""
    current = {r["station_id"]: r for r in records}
    if is_baseline:
        return [{**r, "change_type": "BASELINE", "previous_fingerprint": None} for r in records], current

    changes = []
    for key in sorted(set(current) | set(previous_state)):
        now, before = current.get(key), previous_state.get(key)
        if before is None:
            changes.append({**now, "change_type": "NEW_STATION", "previous_fingerprint": None})
        elif now is None:
            # The station's last known counts, stamped with the poll that saw it gone.
            changes.append({**before, **poll, "change_type": "REMOVED",
                            "previous_fingerprint": before["fingerprint"]})
        elif now["fingerprint"] != before["fingerprint"]:
            changes.append({**now, "change_type": "UPDATE", "previous_fingerprint": before["fingerprint"]})
    return changes, current
