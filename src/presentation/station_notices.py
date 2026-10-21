"""Turn a live stop disruption response into display items for a station notices panel.

Pure functions (no Streamlit, no network), shared by the portfolio panel and future
MCP tools.

Display grouping differs from data identity on purpose. The data keeps the same message
published under two types (e.g. "Information" and "Interchange Message"), or under two
station codes (NaPTAN codes are per network, so an interchange such as Willesden Junction
has a 910G rail code and a 940G Underground code), as separate records. A panel shows each
(station name, message) once, listing every type and mode.
"""

import re
from datetime import datetime

STATION_SUFFIXES = (
    " Underground Station", " Rail Station", " DLR Station", " Tram Stop", " Station",
)

MODE_NAMES = {
    "tube": "Tube",
    "dlr": "DLR",
    "overground": "Overground",
    "elizabeth-line": "Elizabeth line",
    "tram": "Tram",
}

CATEGORIES = ("part_closure", "accessibility", "planned", "live")


def _utc(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def short_station_name(name):
    """'Barons Court Underground Station' -> 'Barons Court'."""
    name = (name or "").strip()
    for suffix in STATION_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def notice_items(data, accessibility_pattern, modes=None):
    """One item per (station, message), most important first.

    Order: part closures, then accessibility, then the rest; newest first within each.
    """
    accessibility = re.compile(accessibility_pattern, re.IGNORECASE)
    groups = {}

    for record in data:
        if not isinstance(record, dict) or not record.get("description"):
            continue
        if modes and record.get("mode") not in modes:
            continue
        description = record["description"].strip()   # trailing \r\n seen in real data
        station = short_station_name(record.get("commonName"))
        key = (station.lower(), description)
        item = groups.setdefault(key, {
            "station": station,
            "description": description,
            "atco_codes": set(),
            "modes": set(),
            "types": set(),
            "appearances": set(),
            "since": None,
            "planned_until": None,
        })
        item["atco_codes"].add(record.get("atcoCode"))
        item["modes"].add(record.get("mode"))
        item["types"].add(record.get("type"))
        item["appearances"].add(record.get("appearance"))

        start = _utc(record.get("fromDate"))
        if start and (item["since"] is None or start < item["since"]):
            item["since"] = start
        if record.get("appearance") == "PlannedWork":
            end = _utc(record.get("toDate"))
            if end and (item["planned_until"] is None or end > item["planned_until"]):
                item["planned_until"] = end

    items = []
    for item in groups.values():
        item["atco_codes"] = sorted(c for c in item["atco_codes"] if c)
        item["modes"] = sorted(m for m in item["modes"] if m)
        item["mode_name"] = " · ".join(MODE_NAMES.get(m, m) for m in item["modes"])
        item["types"] = sorted(t for t in item["types"] if t)
        item["appearances"] = sorted(a for a in item["appearances"] if a)
        item["categories"] = {
            "part_closure": "Part Closure" in item["types"],
            "accessibility": bool(accessibility.search(item["description"])),
            "planned": "PlannedWork" in item["appearances"],
            "live": "RealTime" in item["appearances"],
        }
        items.append(item)

    def rank(item):
        c = item["categories"]
        group = 0 if c["part_closure"] else 1 if c["accessibility"] else 2
        newest_first = -(item["since"].timestamp() if item["since"] else 0)
        return group, newest_first, item["station"]

    return sorted(items, key=rank)


def notice_summary(items):
    """Counts per category, plus the number of stations with any notice."""
    counts = {category: sum(1 for i in items if i["categories"][category]) for category in CATEGORIES}
    counts["stations"] = len({i["station"].lower() for i in items})
    counts["notices"] = len(items)
    return counts
