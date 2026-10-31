"""The "Ask the data" assistant: answers a typed question from the platform's own data, without a
paid AI model.

It reads the question for what it is about (rail, buses, bikes, roads, or what a figure means)
and whether it asks about now or about the past week, then answers from the matching source:
the live TfL feeds, the business recommendation figures (last 7 complete London days) or the
documentation search (the retrieval half of RAG). It is rule-based: it answers greetings, says
when a question is outside this project, and returns clickable suggestions (`suggestions`) of
questions it can answer.

Asked for a chart or an analysis ("chart disruption by hour"), it runs one of the fixed analyses
in analysis.py on the history and returns the chart with the finding.

Pure functions, shared by the portfolio and the TfL repository: the caller passes `sources`, an
object that fetches the data (so the portfolio reuses its cached live feeds):
    sources.line_cards()          -> line cards (src/presentation/line_board.line_cards)
    sources.bike_rows()           -> docking stations (src/presentation/bike_live.stations)
    sources.roads()               -> (disruptions, corridors) (src/presentation/road_live)
    sources.recommendation(area)  -> (figures row, text module) or None; area: rail, bus, bikes, roads
    sources.docs(query)           -> documentation chunks, best first
    sources.query(sql)            -> rows of one of analysis.py's fixed queries
Each may raise; the assistant then says that source couldn't be read.
"""

import re

from . import analysis

EXAMPLES = [
    "Which Tube lines have problems right now?",
    "How many bikes are at Waterloo?",
    "Which main roads are delayed?",
    "Chart disruption by line this week",
    "Graph disruption by hour of day",
    "Plot Tube bunching by line",
    "Is the Central line running?",
    "Which lines were least reliable this week?",
    "Which bus routes bunch most?",
    "What does bunching mean?",
    "Why use the median time to clear?",
]

WORDS = {
    "definition": r"\b(mean|means|meaning|define|definition|defined|measured|calculated|counted|explain)\b|"
                  r"\bwhy\b.*\b(use|used|median|average)\b",
    "bikes": r"\b(bikes?|cycles?|cycling|docks?|docking|santander|e-?bikes?)\b",
    "roads": r"\b(roads?|traffic|incidents?|roadworks?|works|collisions?|crash(es)?|breakdowns?|signal faults?|"
             r"a\d{1,3}|north circular|south circular|tunnel|corridors?|congestion)\b",
    "bus": r"\b(bus|buses|bunch\w*|routes?)\b",
    "rail": r"\b(tube|underground|overground|dlr|elizabeth|tram|trains?|rail|lines?|"
            r"bakerloo|central|circle|district|hammersmith|jubilee|metropolitan|northern|piccadilly|victoria|"
            r"waterloo & city|waterloo and city|liberty|lioness|mildmay|suffragette|weaver|windrush)\b",
    "history": r"\b(week|weekly|days|recommend\w*|trends?|history|historically|usually|typically|typical|"
               r"most|least|worst|best|reliable|unreliable|often|average|overall|slowest|fastest|longest)\b",
}
# Questions about the platform itself, answered from its documentation.
PLATFORM = (r"\b(platform|pipeline|collect\w*|kafka|bigquery|tableau|dashboards?|mcp|rag|agent|"
            r"google sheets?|cloud run|vm|this project|recommendations?)\b")
# Terms the documentation defines: a "what is ..." question about one of them is in scope.
TERMS = (r"\b(bunch\w*|headways?|headroom|delayed share|problem share|out of use|serious|severe|median|"
         r"overrun\w*|disrupt\w*|good service|planned works|incidents?|roadworks?|peaks?|docks?|tfl|"
         r"transport|london|stations?|lifts?|step[- ]free|accessib\w*|waits?|gaps?|speed|reliab\w*)\b")
GREETING = (r"^\s*(hi+|hello|hey+|hiya|howdy|yo|greetings|good (morning|afternoon|evening|day)|"
            r"thanks?|thank you|cheers|ok(ay)?|great|cool|bye|goodbye)\b")
SUGGESTIONS = [
    "Which Tube lines have problems right now?",
    "How many bikes are at Waterloo?",
    "Which main roads are delayed?",
    "Chart disruption by line this week",
    "Graph disruption by hour of day",
    "What does bunching mean?",
]
OFF_TOPIC = ("Sorry, I can only answer questions about this TfL project: London's rail, buses, bikes "
             "and roads, and how this platform measures them. Try one of these:")

LINE_NAMES = ["bakerloo", "central", "circle", "district", "hammersmith", "jubilee", "metropolitan",
              "northern", "piccadilly", "victoria", "waterloo & city", "elizabeth", "dlr", "tram",
              "liberty", "lioness", "mildmay", "suffragette", "weaver", "windrush"]
FILLER = set("""a an the is are was were any there how many much what which where who when can i get
find at in near around by on of for to do does bikes bike cycles cycle docks dock docking station
stations santander free available right now today currently please tell me show me left e ebikes
e-bikes""".split())


def _has(kind, text):
    return re.search(WORDS[kind], text) is not None


def _list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def _count(n, noun):
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _pct(value):
    return "–" if value is None else f"{value * 100:.0f}%"


def greeting(question):
    """A reply to a message that is only a greeting or thanks, else None."""
    text = question.lower().strip()
    if not re.match(GREETING, text) or len(text.split()) > 6 or route(question)[0] not in (None,):
        return None
    if re.match(r"^\s*(thanks?|thank you|cheers)\b", text):
        return "You're welcome! Anything else you'd like to know? Try one of these:"
    if re.match(r"^\s*(bye|goodbye)\b", text):
        return "Goodbye! Come back any time for live TfL data."
    if re.match(r"^\s*(ok(ay)?|great|cool)\b", text):
        return "Anything else you'd like to know? Try one of these:"
    return ("Hello! I can answer questions about London's transport from this platform's live TfL "
            "feeds, its history and its documentation. Try one of these:")


def route(question):
    """(topic, when) for a question: topic is rail, bus, bikes, roads, definition or None;
    when is "now" or "history"."""
    text = question.lower()
    when = "history" if _has("history", text) else "now"
    in_scope = re.search(TERMS, text) or re.search(PLATFORM, text)
    if _has("definition", text):
        return ("definition" if in_scope or any(_has(t, text) for t in ("bikes", "roads", "bus", "rail"))
                else None), when
    for topic in ("bikes", "roads", "bus", "rail"):
        if _has(topic, text):
            return topic, when
    if in_scope:
        return "definition", when
    return None, when


# Live answers --------------------------------------------------------------------------------

def _rail_now(question, sources):
    cards = sources.line_cards()
    text = question.lower()
    named = [n for n in LINE_NAMES if n in text or n.replace(" & ", " and ") in text]
    if named:
        found = [c for c in cards if any(n in (c["line_name"] or "").lower() for n in named)]
        if not found:
            return f"I couldn't find the {_list(named)} line in TfL's feed right now."
        parts = []
        for c in found:
            line = f"**{c['line_name']}: {c['status']}**"
            if c["other_statuses"]:
                line += f" (also {', '.join(c['other_statuses'])})"
            if c["level"] != "good" and c["duration"]:
                line += f", for {c['duration']}"
            parts.append(line + ("." if not c["reasons"] else f". {c['reasons'][0]}"))
        return "\n\n".join(parts)
    problems = [c for c in cards if c["level"] in ("stopped", "delays")]
    good = sum(c["level"] == "good" for c in cards)
    if not problems:
        return f"Good service on all {len(cards)} lines right now." if good == len(cards) else (
            f"No delays right now: {good} of {len(cards)} lines have good service and the rest are closed for the night.")
    lines = "\n".join(f"- **{c['line_name']}**: {c['status']}" + (f" ({c['duration']})" if c["duration"] else "")
                      for c in problems)
    return f"{len(problems)} of {len(cards)} lines have problems right now; {good} have good service.\n\n{lines}"


def _place(question):
    """The place a bike question asks about: 'how many bikes near Hyde Park?' -> 'hyde park'."""
    match = re.search(r"\b(?:near|at|in|around|by)\s+(.+?)[?.!]*$", question.lower())
    words = match.group(1).split() if match else re.findall(r"[a-z0-9'&]+", question.lower())
    return " ".join(w for w in words if w not in FILLER)


def _bikes_now(question, sources):
    rows = sources.bike_rows()
    place = _place(question)
    if not place:
        empty = sum(r["state"] == "empty" for r in rows)
        full = sum(r["state"] == "full" for r in rows)
        bikes = sum(r["bikes"] for r in rows)
        return (f"Right now there are **{bikes:,} bikes** at {len(rows)} docking stations. "
                f"{empty} stations are empty (no bike to take) and {full} are full (nowhere to return one). "
                "Ask about a place, e.g. \"bikes at Waterloo\".")
    matches = [r for r in rows if all(w in r["name"].lower() for w in place.split())]
    if not matches:
        return f"I couldn't find a docking station matching \"{place}\". Try part of a street or area name."
    shown = sorted(matches, key=lambda r: -r["bikes"])[:8]
    lines = "\n".join(f"- **{r['name']}**: {_count(r['bikes'], 'bike')} ({_count(r['e_bikes'], 'e-bike')}), "
                      f"{_count(r['empty_docks'], 'free dock')}"
                      + (" — empty" if r["state"] == "empty" else " — full" if r["state"] == "full" else "")
                      for r in shown)
    more = f"\n\n…and {len(matches) - len(shown)} more." if len(matches) > len(shown) else ""
    return f"{len(matches)} docking station{'s' if len(matches) != 1 else ''} match \"{place}\" right now:\n\n{lines}{more}"


def _roads_now(question, sources):
    disruptions, corridors = sources.roads()
    incidents = [d for d in disruptions if d["is_incident"]]
    delayed = [c for c in corridors if not c["good"]]
    road = re.search(r"\b(a\d{1,3})\b", question.lower())
    if road:
        tag = road.group(1).upper()
        on_road = [d for d in disruptions if tag in d["roads"]]
        status = next((c for c in corridors if (c["id"] or "").upper() == tag or tag in (c["name"] or "")), None)
        head = (f"**{status['name']}**: TfL rates it {status['description']}." if status
                else f"{tag} is not one of TfL's 24 main roads, so it has no overall status.")
        inc = [d for d in on_road if d["is_incident"]]
        works = len(on_road) - len(inc)
        tail = f" {len(inc)} live incident{'s' if len(inc) != 1 else ''} and {works} roadworks are on it."
        return head + tail + "".join(f"\n- {d['sub_category'] or d['category']} ({d['severity']}): {d['location']}"
                                     for d in inc[:5])
    head = (f"**{len(delayed)} of {len(corridors)} main roads** are rated Serious or worse right now"
            + (f": {_list(c['name'] for c in delayed)}." if delayed else "."))
    head += f" There are {len(incidents)} live incidents and {len(disruptions) - len(incidents)} roadworks."
    serious = sorted(incidents, key=lambda d: -d["weight"])[:5]
    return head + "".join(f"\n- {d['sub_category'] or d['category']} ({d['severity']}): {d['location']}"
                          for d in serious)


# History and definitions ---------------------------------------------------------------------

def _history(area, sources):
    found = sources.recommendation(area)
    if not found:
        return f"The {area} figures aren't available right now."
    row, text = found
    findings = "\n".join(f"- **{h}:** {t}" for h, t in text.findings(row))
    period = f"\n\n_Figures for {text.period(row)}, the last 7 complete days with data._" if hasattr(text, "period") else ""
    return f"{text.summary(row)}\n\n{findings}{period}"


def _definition(question, sources):
    hits = sources.docs(question)
    if not hits:
        return "I couldn't find that in the platform's documentation. Try one of these:"
    best = hits[0]
    return f"**{best['title']}**\n\n{best['text']}\n\n_From the platform's documentation ({best['source']})._"


def answer(question, sources):
    """{"answer": markdown, "source": what was used, "suggestions": questions to offer as buttons,
    "chart": a chart description from analysis.py or None} for a typed question."""
    question = (question or "").strip()
    reply = greeting(question) if question else (
        "I can answer questions about London's transport from this platform's data. Try one of these:")
    if reply:
        return {"answer": reply, "source": None, "chart": None,
                "suggestions": [] if "Goodbye" in reply else SUGGESTIONS}
    topic, when = route(question)
    labels = {"rail": "live TfL line status", "bikes": "live TfL BikePoint feed", "roads": "live TfL road feeds"}
    result = {"source": None, "suggestions": [], "chart": None}
    try:
        chosen = analysis.choose(question)
        if chosen:
            ran = analysis.run(chosen, sources)
            source = ("BigQuery analysis of the last 7 complete days" if chosen in analysis.SQL
                      else "business recommendation figures (last 7 complete days)")
            return {**result, **ran, "source": source}
        if topic is None:
            return {**result, "answer": OFF_TOPIC, "suggestions": SUGGESTIONS}
        if topic == "definition":
            text = _definition(question, sources)
            found = not text.startswith("I couldn't find")
            return {**result, "answer": text, "source": "documentation search" if found else None,
                    "suggestions": [] if found else SUGGESTIONS}
        if topic == "bus":   # no live bus tool here: live buses by route and stop are on the page
            return {**result, "answer": _history("bus", sources), "source": "bus recommendation figures"}
        if when == "history":
            return {**result, "answer": _history(topic, sources), "source": f"{topic} recommendation figures"}
        live = {"rail": _rail_now, "bikes": _bikes_now, "roads": _roads_now}[topic]
        return {**result, "answer": live(question, sources), "source": labels[topic]}
    except Exception:   # noqa: BLE001 - one source failing shouldn't break the chat
        return {**result, "answer": "That data source isn't responding right now. Please try again in a minute."}
