"""Everyday wording read as the assistant's terms, so a question asked naturally ("do buses clump
together?", "when am I least likely to find a bike?", "is it real or just chance?") reaches the
same analysis as one asked in the platform's own words. Only the matched words change; the rest
of the question (station and line names included) is kept as typed.

Pure functions, shared by the portfolio and the TfL repository.
"""

import re

# Words that put a question on a time, day or place dimension: with one of these, "usually" or
# "on average" asks about that dimension rather than for the typical value overall.
DIMENSION = (r"\b(hours?|hourly|time of (the )?day|through(out)? the day|all day|rush hours?|peaks?|morning|afternoon|evening|night|overnight|"
             r"weekends?|weekdays?|day of (the )?week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
             r"over time|daily|each day|trend\w*|getting|by line|by route|by road|by station|which)\b|^when\b")

# (pattern, replacement), applied in order, case-insensitively
RULES = [
    # "Break it down into trend and pattern" is a decomposition, not vehicle breakdowns
    (r"\bbreak(ing)? (\w+ ){0,5}down (into|by)\b", "decompose into"),
    (r"\bbreak(ing)? down\b(?=.{0,60}\b(into|trend|pattern|components?|cycle)\b)", "decompose"),
    # Lifts "breaking down" are lift failures, not vehicle breakdowns
    (r"\b(lifts?\b.{0,40}?)\bbreak(s|ing)? ?down\b", r"\1fail"),
    (r"\blifts? (out of order|broken|not working|out of service|failing|fail\w*)\b", "lifts out of service"),
    # Buses and trains arriving together
    (r"\b(clump\w*( together)?|bunch(es|ed)? up|arrive together|come in (twos|pairs|threes|convoys?)|bunched together)\b",
     "bunching"),
    # How often trains or buses come
    (r"\bcome (more |less )?(often|frequently)\b|\bhow (often|frequently) do (the )?(trains?|buses|tubes?) come\b",
     "frequency"),
    # Countdown clocks
    (r"\b(countdown (clocks?|boards?)|arrival (boards?|clocks?|times? shown)|dot matrix)\b", "countdowns"),
    (r"\bfurther (out|ahead|away)\b|\bminutes ahead\b|\bfar(ther)? out\b", "minutes before"),
    # Bikes
    (r"\b(find a bike|get a bike|no bikes? (left|available)|bike shortages?|short of bikes|out of bikes|"
     r"run(s)? out of bikes|emptier|emptiest)\b", "empty bike stations"),
    (r"\b(return (a|my|the) bike|park (a|my|the) bike|dock (a|my|the) bike|nowhere to (dock|park|return)|"
     r"fill(s|ed)? up|no (free )?docks?|fuller|fullest)\b", "full bike stations"),
    (r"\b(broken|faulty|unusable|damaged) docks?\b|\bdocks? (are |that are )?(broken|faulty|unusable)\b",
     "docks out of use"),
    # Roads
    (r"\b(traffic jams?|jammed|gridlock\w*|tailbacks?|snarl\w*|busy roads?|heavy traffic|congested)\b",
     "congestion"),
    (r"\b(crash(es|ed)?|smash(es)?|pile[- ]?ups?|collid\w*)\b", "collisions"),
    # Rail reliability
    (r"\bunreliab\w*|\bproblems?\b|\bissues?\b|\bgo(es)? wrong\b", "disruption"),
    (r"\bstill (be )?disrupted\b|\bin an hour\b|\ban hour later\b", "stays disrupted next hour"),
    # Statistics in plain words
    (r"\b(real or (just )?(by )?chance|just (by )?chance|(a )?coincidence|(a )?fluke|statistically real|"
     r"real difference|does it really differ)\b", "statistically significant"),
    (r"\bhow (confident|sure|certain) (are you|is that|can (i|we) be)\b.*|\bcan (i|we) (trust|rely on) (that|this|it)\b.*|"
     r"\bis (that|this|it) (reliable|robust|trustworthy)\b.*", "is that statistically significant"),
    (r"\b(behave alike|behave similarly|similar patterns?|the same pattern|act alike)\b", "similar"),
    (r"\bgroup(s|ed)? (\w+ )?(lines|routes|stations|roads) by\b", r"cluster \3 by"),
    (r"\b(predict\w*|warn\w*) .{0,30}\blater\b", "early warning"),
    (r"\bcan you predict\b|\bpredict(ion)?s? (of |for )?(delays?|disruption|waits?|bunching|traffic|incidents?)\b",
     r"forecast \3"),
    (r"\b(how much|how many) (data|days|history)\b", "what data"),
    (r"\bin (certain|some|particular) (parts|areas) of london\b|\bgeograph\w*\b|\bspatial\w*\b", "where in london"),
    # Cause and effect in plain words: read as a relationship question
    (r"\b(slow(s|ed)? down|suffer\w*|push(es|ed)? up|drag(s)? down|knock[- ]on|spill(s)? over|"
     r"mean(s)? (longer|more|less|shorter|fewer)|go(es)? hand in hand)\b", "affect"),
    (r"\bwhen (the )?(tube|trains?|lines?|roads?|buses|traffic)( is| are)? (disrupted|congested|bad|worse|busy)\b",
     r"affect \2 disruption"),
]
TYPICAL = (r"\b(usually|normally|typically|on average|average|normal|typical|how often|how common|"
           r"how much of the time)\b")


def normalise(question):
    """`question` with everyday wording replaced by the assistant's terms."""
    text = question
    for pattern, replacement in RULES:
        text = re.sub(pattern, replacement, text, flags=re.I)
    # "how long do I usually wait?" asks for the typical value and its spread (the distribution),
    # unless a time, day or place is named ("on average at weekends" compares weekends)
    # How often lifts fail is best answered station by station
    if re.search(r"\blifts?\b", text, re.I) and re.search(r"\bhow (often|common)\b", text, re.I) \
            and not re.search(DIMENSION, text, re.I):
        text += " (which stations most)"
    # "Stations" that run empty or full are bike docking stations (unless the Tube is named)
    if (re.search(r"\b(empty|full)\b", text, re.I)
            and not re.search(r"\b(tube|underground|rail|trains?|bikes?)\b", text, re.I)):
        text = re.sub(r"\bstations?\b", lambda m: "bike " + m.group(0), text, count=1, flags=re.I)
    # Countdown accuracy with no time named: how it improves as arrival nears
    if (re.search(r"\bcountdowns?\b", text, re.I) and re.search(r"\baccura\w*", text, re.I)
            and not re.search(DIMENSION, text, re.I)):
        text += " (by minutes before arrival)"
    if (re.search(TYPICAL, text, re.I) and not re.search(DIMENSION, text, re.I)
            and not re.search(r"\b(causes?|reasons?|why|chart|plot|graph|draw|show)\b", text, re.I)):
        text = re.sub(TYPICAL, "typical range", text, count=1, flags=re.I)
    return text
