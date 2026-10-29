"""Data-quality rules for BikePoint polls (docs/endpoints/bikepoint.md, section 4).

Pure functions. Levels: ERROR (no change records from the poll), EXCLUDED (this station
cannot be read and is skipped; the rest of the poll is processed), WARN (kept, flagged).
"""

from datetime import timedelta

from src.validation.line_status import ERROR, WARN, Issue, has_errors, parse_utc  # noqa: F401

EXCLUDED = "EXCLUDED"


def properties(record):
    """additionalProperties as {key: value}."""
    return {p.get("key"): p.get("value") for p in record.get("additionalProperties") or []
            if isinstance(p, dict)}


def counts(record, config):
    """The five dock counts as whole numbers, or None if any is missing or not a number ≥ 0."""
    props = properties(record)
    result = {}
    for key, name in config["count_fields"].items():
        value = props.get(key)
        if not (isinstance(value, str) and value.isdigit()):
            return None
        result[name] = int(value)
    return result


def validate_poll(data, config, collected_at):
    """Apply rules P1-P3 and B1-B5. Station-level issues carry the record index in `status_index`."""
    if not isinstance(data, list):
        return [Issue(ERROR, "P1", "Response body is not a JSON array")]

    issues = []
    if len(data) < config["min_stations"]:
        issues.append(Issue(WARN, "P2", f"Only {len(data)} stations (normally about 800)"))

    ids = [r.get("id") for r in data if isinstance(r, dict) and r.get("id")]
    if len(ids) != len(set(ids)):
        return issues + [Issue(ERROR, "P3", "Station id repeated within the poll")]

    (lat_min, lat_max), (lon_min, lon_max) = config["london_bounds"]["lat"], config["london_bounds"]["lon"]
    max_modified = collected_at + timedelta(seconds=config["future_modified_tolerance_seconds"])

    for index, record in enumerate(data):
        def issue(level, rule, message):
            return Issue(level, rule, message, record.get("id") if isinstance(record, dict) else None, index)

        found = counts(record, config) if isinstance(record, dict) else None
        if not isinstance(record, dict) or not record.get("id") or found is None:
            issues.append(issue(EXCLUDED, "B1", "Missing id or a dock count that is not a whole number"))
            continue

        if found["standard_bikes"] + found["e_bikes"] != found["bikes"]:
            issues.append(issue(WARN, "B2", "Standard bikes + e-bikes do not add up to bikes"))
        if found["bikes"] + found["empty_docks"] > found["docks"]:
            issues.append(issue(WARN, "B3", "Bikes + empty docks exceed docks"))
        lat, lon = record.get("lat"), record.get("lon")
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            issues.append(issue(WARN, "B4", f"Location {lat}, {lon} is outside London"))
        modified = max((parse_utc(p.get("modified")) for p in record.get("additionalProperties") or []
                        if parse_utc(p.get("modified"))), default=None)
        if modified is not None and modified > max_modified:
            issues.append(issue(WARN, "B5", "modified is in the future"))

    return issues
