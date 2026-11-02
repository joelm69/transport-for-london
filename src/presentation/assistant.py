"""The "Ask the data" assistant: answers a typed question from the platform's own data, without a
paid AI model.

It reads the question for what it is about (rail, buses, bikes, roads, or what a figure means)
and whether it asks about now or about the past week, then answers from the matching source:
the live TfL feeds, the business recommendation figures (last 7 complete London days) or the
documentation search (the retrieval half of RAG). It is rule-based: it answers greetings, says
when a question is outside this project and returns clickable suggestions (`suggestions`) of
questions it can answer.

Asked "what analyses can you do?", it lists them with clickable examples. Asked for a chart or a
statistic it can't produce (bike or road history, weather, or no measure named), it says why and
suggests the same kind of analysis it can do. Misspelt words are read as the closest known word
("histgram" as "histogram") and the answer says so. Asked a statistical
question (a distribution, a significance test, anomalies, persistence, early warning,
concentration, countdown accuracy), it runs analysis.py's statistical analyses. Asked how
something changes or whether two things are related ("is there a relationship between
time of day and bus bunching?"), it runs analysis.py's engine on the history and returns the most
suitable chart with the numbers behind the answer.

Pure functions, shared by the portfolio and the TfL repository: the caller passes `sources`, an
object that fetches the data (so the portfolio reuses its cached live feeds):
    sources.line_cards()          -> line cards (src/presentation/line_board.line_cards)
    sources.bike_rows()           -> docking stations (src/presentation/bike_live.stations)
    sources.roads()               -> (disruptions, corridors) (src/presentation/road_live)
    sources.recommendation(area)  -> (figures row, text module) or None; area: rail, bus, bikes, roads
    sources.docs(query)           -> documentation chunks, best first
    sources.query(sql)            -> rows of a query analysis.py builds from its catalogue
Each may raise; the assistant then says that source couldn't be read.
"""

import random
import re

from . import advice, analysis, catalogue, figures, phrasing, road_questions

# Questions offered as buttons, by topic. starters() picks a random mix from these for each
# visit, so returning visitors see different questions; every one is answered by the routes below.
QUESTION_POOL = {
    "live": [
        "Which Tube lines have problems right now?",
        "Is the Central line running?",
        "How many bikes are at Waterloo?",
        "Which main roads are delayed?",
        "Are there any serious road incidents right now?",
    ],
    "rail": [
        "How does Tube waiting time change through the day?",
        "Which lines were least reliable this week?",
        "Show a pie chart of the reasons for rail disruption",
        "Do weekends have less rail disruption than weekdays?",
        "Which day of the week has the most rail disruption?",
        "How accurate are Tube countdowns by hour?",
        "Is bunching related to waiting time on the Tube?",
        "Show planned versus unplanned rail disruption",
    ],
    "bus": [
        "Is there a relationship between time of day and bus bunching?",
        "Which bus routes bunch most?",
        "Plot theoretical versus actual bus wait time and the passenger-hours lost",
        "When is the best time to catch a bus?",
        "Heatmap of bus bunching by hour and day of week",
    ],
    "bikes": [
        "Chart how often bike stations are empty or full",
        "Where in London do bike stations fill up during the morning rush?",
        "Cluster bike stations by when they run empty",
        "How many bike docks are out of use?",
        "When are bike stations most likely to be empty?",
    ],
    "roads": [
        "Do more road incidents make London's main roads more delayed?",
        "How do road incidents change through the day?",
        "Compare main road delays by road",
        "Chart how long road incidents take to clear",
        "Do roadworks overrun?",
        "Give me traffic tips for driving in London",
        "When do most road accidents happen?",
        "What are the most common types of road incident?",
        "Which areas have the most road accidents?",
        "How many road accidents are there each day?",
        "Is traffic worse at weekends?",
        "Which main road is most congested?",
    ],
    "statistics": [
        "Forecast Tube waiting time for tomorrow",
        "Is rail disruption getting worse over time?",
        "Which line-days had unusual disruption?",
        "Box plot of Tube waiting time by line",
        "Control chart of bus bunching",
    ],
    "project": [
        "What analyses can you do?",
        "What data do you collect?",
        "What does bunching mean?",
        "Any travel tips for the Tube?",
    ],
}
ALL_QUESTIONS = [q for questions in QUESTION_POOL.values() for q in questions]
EXAMPLES = ALL_QUESTIONS   # kept for callers that list every example


def starters(n=6, rng=random):
    """n questions in random order, at most one per topic until every topic has had one, so the
    mix spans live data, rail, buses, bikes, roads and statistics."""
    topics = rng.sample(list(QUESTION_POOL), len(QUESTION_POOL))
    picked = [rng.choice(QUESTION_POOL[t]) for t in topics][:n]
    rest = [q for q in ALL_QUESTIONS if q not in picked]
    return picked + rng.sample(rest, max(0, n - len(picked)))


def suggest(n=4, rng=random):
    """Suggestions for a greeting, an off-topic question or a miss: a fresh random mix each time."""
    return starters(n, rng)

WORDS = {
    "definition": r"\b(mean|means|meaning|define|definition|defined|measured|calculated|counted|explain)\b|"
                  r"\bwhy\b.*\b(use|used|median|average)\b",
    "bikes": r"\b(bikes?|bicycles?|cycling|cyclists?|cycle hire|docks?|docking|santander|e-?bikes?)\b",
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
CAPABILITIES = (r"\b(what|which) (analys\w*|statistic\w*|charts?|kinds? of (analys\w*|charts?|questions?))\b|"
                r"\bwhat can you (do|analy[sz]e|show|chart|answer)\b|\bcapabilit\w*|\bsuggest\w* (an )?analys\w*")


def capabilities():
    """What the assistant can answer, for "what analyses can you do?"."""
    return (
        "Here's what I can do with this platform's data:\n\n"
        "- **Live now:** line status, bikes and free docks at any station, delayed main roads and incidents.\n"
        "- **This week:** the rail, bus, bike and road findings behind the business recommendations.\n"
        "- **Patterns and charts:** how a measure changes by hour, day of week, weekday or weekend, date, line "
        "or route (line or bar chart), how two measures relate (scatter plot with correlation) and the reasons "
        "for disruption (bars or a pie).\n"
        "- **The recommendations' figures:** even-spacing against actual bus waits and the passenger-hours "
        "lost, planned against unplanned rail disruption, train against infrastructure causes, bike stations "
        "empty or full, docks out of use, road clearing times and roadworks overruns, charted with how each is "
        "worked out.\n"
        "- **More charts:** heatmaps (hour × day of week), box plots, control charts, rolling averages, area "
        "and stacked bar charts; trends over time with a fitted slope and significance and regression on "
        "scatter plots.\n"
        "- **The project itself:** what data it collects, how often and since when and suggestions for new "
        "features.\n"
        "- **Forecasting and patterns:** a 24-hour forecast with a 95% range, decomposition into trend and "
        "daily cycle and clustering lines, routes or stations by their daily pattern.\n"
        "- **Statistics:** distributions (median, spread, skew), a significance test for weekday against weekend, "
        "anomaly detection (z-scores), persistence (how long disruptions last), early warning (lead–lag "
        "correlation), concentration (Pareto curve and Gini) and how countdown accuracy improves near arrival.\n"
        "- **Travel advice:** the best times to travel by Tube and bus, lines and routes to allow extra time on, "
        "roads to plan around and what's wrong right now, worked out from the data.\n"
        "- **Definitions:** what any figure means and how it's measured.\n\n"
        "Measures: bus and Tube bunching, waiting time and gap regularity, dropped trains, rail disruption and "
        "incidents, countdown accuracy and accessibility. Try one of these:")


# Words the assistant knows, for reading misspellings ("histgram" -> "histogram").
VOCABULARY = sorted(set("""
histogram distribution bunching bunched disruption disruptions disrupted waiting relationship related
correlation concentrated significant statistically anomalies unusual persistence countdown countdowns
accuracy accurate accessibility weekend weekends weekday weekdays morning evening regularity incidents
compare chart graph scatter doughnut reliable reliability stations station bicycles bikes traffic
roadworks underground overground elizabeth piccadilly metropolitan northern central victoria jubilee
bakerloo district circle waterloo hammersmith analyses analysis waiting delayed delays incident accident
accidents collision collisions breakdown breakdowns crashes congestion congested analyse analyze
analysing forecast predict prediction typical usually unusually normally
unreliable reliably clumping clumped emptier fuller jammed gridlock tailbacks normal significantly
average together typically confident certain chance coincidence""".split()))


def read_misspellings(question):
    """(question with likely misspellings corrected, [(typed, read as)]): only words of five or
    more letters that aren't already known and only close matches."""
    import difflib
    fixed, changes = [], []
    for word in question.split():
        core = word.strip(".,?!;:()\"'").lower()
        if len(core) >= 5 and core.isalpha() and core not in VOCABULARY:
            match = difflib.get_close_matches(core, VOCABULARY, n=1, cutoff=0.84)
            if match:
                changes.append((core, match[0]))
                word = word.lower().replace(core, match[0])
        fixed.append(word)
    return " ".join(fixed), changes


OFF_TOPIC = ("Sorry, I can only answer questions about this TfL project: London's rail, buses, bikes "
             "and roads and how this platform measures them. Try one of these:")

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


def readable(text):
    """Text with code blocks and JSON removed, so a visitor never sees raw data or code."""
    text = re.sub(r"```.*?(```|$)", "", text, flags=re.S)
    lines = [line for line in text.splitlines()
             if not re.match(r'\s*([{}\[\]],?|"[^"]*"\s*:.*|`[^`]*`)\s*$', line)   # JSON lines
             and not (line.lstrip().startswith("|") and "`" in line)   # tables of field names
             and not re.search(r"json", line, re.I)]   # JSON formats and .json files
    # A table left with only its header and separator goes too
    kept = [line for i, line in enumerate(lines)
            if not (line.lstrip().startswith("|") and not any(
                n.lstrip().startswith("|") and not set(n.strip()) <= set("|-: ")
                for n in lines[max(0, i - 2):i] + lines[i + 1:i + 3]
                if n is not line) and set(line.strip()) <= set("|-: ") | set(line.strip()))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _definition(question, sources):
    for hit in sources.docs(question):
        text = readable(hit["text"])
        if text and len(text) >= len(hit["text"]) / 2:   # skip chunks that were mostly code or JSON
            return f"**{hit['title']}**\n\n{text}\n\n_From the platform's documentation._"
    return "I couldn't find that in the platform's documentation. Try one of these:"


FOLLOW_ON = (r"^(and|but|also|so|ok(ay)?|then|now|what about|how about|same|instead|only|just|"
             r"by|as|in|on|for|over|per|split|show|draw|plot|chart|make)\b|"
             r"\b(it|that|this|those|these|them|same|instead|again)\b")
MODE_WORDS = (r"\b(bus(es)?|routes?|tube|underground|trains?|rail|lines?|bikes?|cycles?|cycling|docks?|"
              r"stations?|roads?|traffic|main roads?|accidents?|collisions?|incidents?|breakdowns?)\b")
DIMENSION_WORDS = (r"\b(through(out)? the day|by hour|(at )?what time|time of (the )?day|in the (morning|evening|"
                   r"afternoon|rush hours?)|at night|by day of (the )?week|day of (the )?week|which day|at weekends?|"
                   r"weekends?|weekdays?|over time|each day|per day|by date|by line|by route|by road|by borough|"
                   r"by area|(in|on) which (areas?|lines?|routes?|roads?)|which (areas?|boroughs?))\b|^when\b")
CHART_KIND_WORDS = (r"\b(pie|donut|doughnut|bar|stacked|line|area|scatter|heat ?map|box|control|rolling)( bar)?"
                    r"( charts?| graphs?| plots?)?\b|\bas an?\b")
FAILED = ("Sorry, I can only answer", "I couldn't", "isn't responding", "aren't available", "There isn't enough")


def with_context(question, previous):
    """The question rewritten with the previous one, for a follow-up such as "what about buses?",
    "show it as a pie chart" or "and by day of week?", else None."""
    if not previous:
        return None
    text = question.strip().rstrip("?.!").strip()
    rest = re.sub(r"^(and|but|also|so|ok(ay)?|then|now|what about|how about|instead)\b[ ,]*", "", text,
                  flags=re.I)
    earlier = previous.strip().rstrip("?.!")
    # A different mode, breakdown or chart type replaces the earlier one
    for words in (MODE_WORDS, DIMENSION_WORDS, CHART_KIND_WORDS):
        if re.search(words, rest, re.I):
            earlier = re.sub(words, "", earlier, flags=re.I)
    rest = re.sub(r"\b(it|that|this|those|these|them)\b", "", rest, flags=re.I)
    return re.sub(r"\s+", " ", f"{earlier} {rest}").strip() + "?"


def answer(question, sources, history=None):
    """{"answer": markdown, "source": what was used, "suggestions": questions to offer as buttons,
    "chart": a chart description from analysis.py or None} for a typed question. `history` is the
    visitor's earlier questions, oldest first, so a follow-up ("what about buses?") is read with the
    one before it; "asked" in the result is the question actually answered, for the next history."""
    question = (question or "").strip()
    if re.search(r"^is that statistically significant", phrasing.normalise(question)) and not history:
        return {"answer": "Ask me a question about the data first (for example \"Is traffic worse at weekends?\"), "
                          "then ask how confident I am: I'll test whether the difference could be chance.",
                "source": None, "chart": None, "suggestions": suggest(), "asked": question}
    previous = next((q for q in reversed(history or []) if q.strip() and not greeting(q)), None)
    if previous and re.search(r"^is that statistically significant", phrasing.normalise(question)):
        earlier = analysis.plan(phrasing.normalise(previous))
        if earlier and len(earlier["measures"]) == 2:   # how sure a relationship is
            try:
                rows = [dict(r) for r in sources.query(analysis.build_sql(earlier))]
                return {**analysis.relationship_confidence(earlier, rows), "asked": previous,
                        "source": f"BigQuery analysis of the last {analysis.DAYS} days", "suggestions": []}
            except Exception:   # noqa: BLE001
                pass
    result = _answer_one(question, sources)
    failed = result["answer"].startswith(FAILED) or not (result.get("chart") or result.get("charts"))
    follow_on = re.search(FOLLOW_ON, question.lower()) and len(question.split()) <= 9
    if previous and not greeting(question) and (follow_on or result["answer"].startswith(FAILED)):
        combined = with_context(phrasing.normalise(question), previous)
        again = _answer_one(combined, sources)
        if not again["answer"].startswith(FAILED) and (failed or follow_on):
            return {**again, "asked": combined,
                    "answer": "_Following on from your last question._\n\n" + again["answer"]}
    return {**result, "asked": question}


def _answer_one(question, sources):
    """answer() for one question on its own."""
    reply = greeting(question) if question else (
        "I can answer questions about London's transport from this platform's data. Try one of these:")
    if reply:
        return {"answer": reply, "source": None, "chart": None,
                "suggestions": [] if "Goodbye" in reply else suggest()}
    question, changes = read_misspellings(question)
    question = phrasing.normalise(question)   # everyday wording read as the assistant's terms
    note = ("_Reading " + ", ".join(f"“{a}” as “{b}”" for a, b in changes) + "._\n\n") if changes else ""
    result = answer_read(question, sources)
    text = result["answer"]
    if "```" in text or re.search(r'\{\s*"\w+"\s*:', text):   # never show raw JSON or code
        text = readable(text)
    return {**result, "answer": note + text}


def answer_read(question, sources):
    """answer() for a question with misspellings already corrected."""
    topic, when = route(question)
    labels = {"rail": "live TfL line status", "bikes": "live TfL BikePoint feed", "roads": "live TfL road feeds"}
    result = {"source": None, "suggestions": [], "chart": None}
    try:
        if re.search(CAPABILITIES, question.lower()):
            return {**result, "answer": capabilities(), "suggestions": figures.SUGGESTIONS[:2]
                    + analysis.STAT_SUGGESTIONS[:3] + analysis.SUGGESTIONS[:2]}
        if catalogue.asks_about_features(question):
            return {**result, "answer": "New analyses and features, by what they need:\n\n" + catalogue.features(),
                    "source": "the project catalogue", "suggestions": ["What data do you collect?",
                                                                      "What analyses can you do?"]}
        if catalogue.asks_about_data(question):
            return {**result, "answer": catalogue.describe(question), "source": "the project catalogue",
                    "suggestions": ["What analyses can you do?", "Suggest new features"]}
        if advice.asks_for_advice(question):
            return {**result, **advice.advise(question, sources)}
        road = road_questions.choose(question)
        if road:
            return {**result, **road_questions.run(road, question, sources)}
        figure = figures.choose(question)
        if figure:
            ran = figures.run(figure, sources)
            charts = ran["charts"]
            return {**result, "answer": ran["answer"], "charts": charts, "chart": charts[0] if charts else None,
                    "source": "the business recommendation figures (BigQuery)"}
        declined = analysis.cannot_analyse(question)
        if declined:
            return {**result, **declined}
        where = analysis.map_plan(question)
        if where:
            ran = analysis.run_map(where, sources)
            return {**result, **ran, "charts": ran.get("charts"),
                    "source": "BigQuery bike history, mapped with the live BikePoint feed",
                    "suggestions": ["Where in London do bike stations fill up during the morning rush?",
                                    "Cluster bike stations by when they run empty",
                                    "Heatmap of empty bike stations by hour and day of week"]}
        stat = analysis.stat_plan(question)
        if stat:
            ran = analysis.run_stat(stat, sources)
            others = [q for name, (_, _, q) in analysis.STATS.items() if name != stat["stat"]]
            return {**result, **ran, "charts": ran.get("charts"),
                    "source": f"BigQuery statistical analysis of the last {analysis.DAYS} days",
                    "suggestions": others[:3]}
        chosen = analysis.plan(question)
        if chosen:
            ran = analysis.run(chosen, sources)
            source = f"BigQuery analysis of the last {analysis.DAYS} days"
            result["suggestions"] = analysis.follow_ups(chosen) if ran["chart"] else analysis.SUGGESTIONS[:4]
            return {**result, **ran, "source": source}
        if topic is None:
            return {**result, "answer": OFF_TOPIC, "suggestions": suggest()}
        if topic == "definition":
            text = _definition(question, sources)
            found = not text.startswith("I couldn't find")
            return {**result, "answer": text, "source": "documentation search" if found else None,
                    "suggestions": [] if found else suggest()}
        if topic == "bus":   # no live bus tool here: live buses by route and stop are on the page
            return {**result, "answer": _history("bus", sources), "source": "bus recommendation figures"}
        if when == "history":
            return {**result, "answer": _history(topic, sources), "source": f"{topic} recommendation figures"}
        live = {"rail": _rail_now, "bikes": _bikes_now, "roads": _roads_now}[topic]
        return {**result, "answer": live(question, sources), "source": labels[topic]}
    except Exception:   # noqa: BLE001 - one source failing shouldn't break the chat
        return {**result, "answer": "That data source isn't responding right now. Please try again in a minute."}
