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
from difflib import SequenceMatcher
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

CATEGORIES = ("closure", "accessibility", "planned", "live")
CLOSURE_TYPES = {"Part Closure", "Closure"}   # "Closure": a whole station, first seen 2026-10-26


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


SIMILAR_MESSAGE = 0.9   # two messages at one station this alike are the same notice

# "Hounslow East: ...", "Hounslow East - ...", "PIMLICO STATION: ...": TfL's own heading.
_HEADING = re.compile(r"^(?P<name>[^:.]{1,60}?)\s*(?::|\s[-\u2013]\s)\s*")


_NAME_NOISE = {"station", "stations", "underground", "rail", "dlr", "tram", "stop"}


# TfL's category label in front of some accessibility messages; not part of the message.
_LABEL = re.compile(r"^no step free access\s*[-:\u2013]\s*")


def _plain_name(name):
    """'Burnham (Berks)' and 'BURNHAM STATION' -> 'burnham'; '&' and 'and' match."""
    name = re.sub(r"\(.*?\)", " ", name.lower()).replace("&", "and")
    return " ".join(w for w in re.split(r"[^a-z0-9]+", name) if w and w not in _NAME_NOISE)


def message_body(description, station):
    """The message without TfL's leading station-name heading, as shown under a station title."""
    match = _HEADING.match(description)
    if match:
        heading, name = _plain_name(match["name"]), _plain_name(station)
        if heading == name or SequenceMatcher(None, heading, name).ratio() >= 0.8:
            body = description[match.end():]
            return body[:1].upper() + body[1:]
    return description


def _message_key(description, station=""):
    """Matches the same message republished with a different heading, ending or spacing
    (Hainault and Hounslow East, 2026-10-23)."""
    text = " ".join(message_body(description, station).lower().split()).rstrip(" .")
    return _LABEL.sub("", text)


def notice_items(data, accessibility_pattern, modes=None):
    """One item per (station, message), most important first.

    Order: closures (part or whole station), then accessibility, then the rest; newest first within each.
    """
    accessibility = re.compile(accessibility_pattern, re.IGNORECASE)
    groups = {}

    for record in data:
        if not isinstance(record, dict) or not record.get("description"):
            continue
        if modes and record.get("mode") not in modes:
            continue
        description = " ".join(record["description"].split())   # trailing \r\n seen in real data
        station = short_station_name(record.get("commonName"))
        key = (station.lower(), _message_key(description, station))
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
        if len(description) > len(item["description"]):   # keep the copy with the full stop
            item["description"] = description
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
    for item in _merge_similar(list(groups.values())):
        item["atco_codes"] = sorted(c for c in item["atco_codes"] if c)
        item["modes"] = sorted(m for m in item["modes"] if m)
        item["mode_name"] = " · ".join(MODE_NAMES.get(m, m) for m in item["modes"])
        item["types"] = sorted(t for t in item["types"] if t)
        item["appearances"] = sorted(a for a in item["appearances"] if a)
        item["categories"] = {
            "closure": bool(CLOSURE_TYPES & set(item["types"])),
            "accessibility": bool(accessibility.search(item["description"])),
            "planned": "PlannedWork" in item["appearances"],
            "live": "RealTime" in item["appearances"],
        }
        items.append(item)

    def rank(item):
        c = item["categories"]
        group = 0 if c["closure"] else 1 if c["accessibility"] else 2
        newest_first = -(item["since"].timestamp() if item["since"] else 0)
        return group, newest_first, item["station"]

    return sorted(items, key=rank)


def _merge_similar(items):
    """Within one station, fold a message into an earlier one when the two are nearly the same
    text (one republished with small edits). Keeps the longer text."""
    kept = []
    for item in items:
        key = _message_key(item["description"], item["station"])
        twin = next((k for k in kept if k["station"].lower() == item["station"].lower()
                     and SequenceMatcher(None, _message_key(k["description"], k["station"]), key).ratio()
                     >= SIMILAR_MESSAGE), None)
        if twin is None:
            kept.append(item)
            continue
        if len(item["description"]) > len(twin["description"]):
            twin["description"] = item["description"]
        for field in ("atco_codes", "modes", "types", "appearances"):
            twin[field] |= item[field]
        if item["since"] and (twin["since"] is None or item["since"] < twin["since"]):
            twin["since"] = item["since"]
        if item["planned_until"] and (twin["planned_until"] is None or item["planned_until"] > twin["planned_until"]):
            twin["planned_until"] = item["planned_until"]
    return kept


MAX_TITLE_STATIONS = 3   # more stations than this: a short title, the names listed below it

# "Suffragette Line: No service between ..." -> "Suffragette line"; also "London Tramlink: ...".
_LINE_HEADING = re.compile(r"^(?P<line>[A-Za-z&' ]{1,40}?(?:\bline|tramlink|\bDLR|overground))\s*:", re.IGNORECASE)


def _line_of(text):
    match = _LINE_HEADING.match(text)
    if not match:
        return None
    line = match["line"].strip()
    return line[:-4] + "line" if line.lower().endswith(" line") else line


def station_cards(items):
    """Display cards from notice_items() output: one card per station listing its messages,
    and one card for several stations when they carry exactly the same messages (a section
    closure announced at both ends, a coach notice at several stations). A card for more than
    MAX_TITLE_STATIONS stations gets a short title (the line named in the message and the
    count) and the names in station_list. Keeps the order of
    `items`, so call it per section (closures, accessibility, ...)."""
    by_station = {}
    for item in items:
        card = by_station.setdefault(item["station"].lower(), {
            "stations": [item["station"]], "modes": set(), "messages": [],
        })
        card["modes"].update(item["modes"])
        card["messages"].append({
            "text": message_body(item["description"], item["station"]),
            "since": item["since"], "planned_until": item["planned_until"],
        })

    cards = {}
    for card in by_station.values():
        texts = tuple(sorted(_message_key(m["text"]) for m in card["messages"]))
        shared = cards.get(texts)
        if shared is None:
            cards[texts] = card
            continue
        shared["stations"] += card["stations"]
        shared["modes"] |= card["modes"]
        for mine, theirs in zip(sorted(shared["messages"], key=lambda m: _message_key(m["text"])),
                                sorted(card["messages"], key=lambda m: _message_key(m["text"]))):
            if theirs["since"] and (mine["since"] is None or theirs["since"] < mine["since"]):
                mine["since"] = theirs["since"]
            if theirs["planned_until"] and (mine["planned_until"] is None or theirs["planned_until"] > mine["planned_until"]):
                mine["planned_until"] = theirs["planned_until"]

    result = []
    for card in cards.values():
        card["modes"] = sorted(m for m in card["modes"] if m)
        card["mode_name"] = " · ".join(MODE_NAMES.get(m, m) for m in card["modes"])
        if len(card["stations"]) > MAX_TITLE_STATIONS:
            # A section closure posted at every station on it: name the line, list the stations.
            line = _line_of(card["messages"][0]["text"])
            count = f'{len(card["stations"])} stations'
            card["title"] = f"{line} · {count}" if line else count
            card["station_list"] = ", ".join(card["stations"])
        else:
            card["title"] = " · ".join(card["stations"])
            card["station_list"] = None
        result.append(card)
    return result


def notice_summary(items):
    """Counts per category, plus the number of stations with any notice."""
    counts = {category: sum(1 for i in items if i["categories"][category]) for category in CATEGORIES}
    counts["stations"] = len({i["station"].lower() for i in items})
    counts["notices"] = len(items)
    return counts
