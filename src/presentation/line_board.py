"""Turn a live line status response into rows for the Streamlit board.

Pure functions (no Streamlit, no network) so the board logic is unit-tested
and reusable, for example by the MCP live tool.
"""

from datetime import datetime

from src.deduplication.line_status import flatten_statuses

# Statuses where some or all of the service is not running.
SERVICE_STOPPED = {1, 2, 3, 4, 5, 11, 16}

# Board levels, worst first. "closed" is normal overnight closure.
LEVEL_ORDER = {"stopped": 0, "delays": 1, "closed": 2, "good": 3}

MODE_NAMES = {
    "tube": "Tube",
    "dlr": "DLR",
    "overground": "Overground",
    "elizabeth-line": "Elizabeth line",
    "tram": "Tram",
}


def board_level(severity, severity_class):
    if severity_class == "good":
        return "good"
    if severity_class == "closed":
        return "closed"
    return "stopped" if severity in SERVICE_STOPPED else "delays"


def format_duration(minutes):
    if minutes is None:
        return None
    if minutes < 1:
        return "under 1 min"
    hours, mins = divmod(int(minutes), 60)
    if hours == 0:
        return f"{mins} min"
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days} d {hours} h"
    return f"{hours} h {mins} min" if mins else f"{hours} h"


def board_rows(data, now, severity_classes, modes=None):
    """One row per (line, status), worst first, then by line name.

    now: aware datetime used for "disrupted for" durations.
    modes: optional set of mode names to keep.
    """
    statuses = flatten_statuses(data, {}, severity_classes)
    rows = []

    for status in statuses:
        if modes and status["mode"] not in modes:
            continue

        level = board_level(status["severity"], status["severity_class"])
        since = status["event_from_utc"]
        minutes = None
        if since and level != "good":
            minutes = max(0.0, (now - datetime.fromisoformat(since)).total_seconds() / 60)

        rows.append({
            "line_id": status["line_id"],
            "line_name": status["line_name"],
            "mode": status["mode"],
            "mode_name": MODE_NAMES.get(status["mode"], status["mode"]),
            "severity": status["severity"],
            "status": status["severity_description"],
            "level": level,
            "reason": status["reason"].strip(),
            "since_utc": since,
            "duration_minutes": minutes,
            "duration": format_duration(minutes),
        })

    # Worst level first, then line name; within a line, the more severe status first
    # (TfL severities: lower is worse, e.g. 6 Severe Delays before 9 Minor Delays).
    rows.sort(key=lambda r: (LEVEL_ORDER[r["level"]], r["line_name"] or "", r["severity"] or 0))
    return rows


def line_cards(rows):
    """One card per line from board_rows(): the worst status as the headline, the line's other
    statuses listed, and each distinct reason once.

    TfL can describe one incident with several statuses: on 2026-10-22 the Elizabeth line had
    Severe Delays (Abbey Wood - Reading/Heathrow) and Minor Delays (Whitechapel - Shenfield)
    with identical reason text, which showed as two identical cards. History keeps every status;
    only the display merges them.
    """
    cards = {}
    for row in rows:                      # worst first, so the first row per line is the headline
        card = cards.get(row["line_id"])
        if card is None:
            cards[row["line_id"]] = {**row, "other_statuses": [],
                                     "reasons": [row["reason"]] if row["reason"] else []}
            continue
        if row["status"] != card["status"] and row["status"] not in card["other_statuses"]:
            card["other_statuses"].append(row["status"])
        if row["reason"] and row["reason"] not in card["reasons"]:
            card["reasons"].append(row["reason"])
        if (row["duration_minutes"] or 0) > (card["duration_minutes"] or 0):
            card.update(since_utc=row["since_utc"], duration_minutes=row["duration_minutes"],
                        duration=row["duration"])
    return list(cards.values())


def summary(rows):
    """Count lines by their worst level (a line with two statuses counts once)."""
    worst = {}
    for row in rows:
        current = worst.get(row["line_id"])
        if current is None or LEVEL_ORDER[row["level"]] < LEVEL_ORDER[current]:
            worst[row["line_id"]] = row["level"]

    counts = {level: 0 for level in LEVEL_ORDER}
    for level in worst.values():
        counts[level] += 1
    counts["lines"] = len(worst)
    return counts
