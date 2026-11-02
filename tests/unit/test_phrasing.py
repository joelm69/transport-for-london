"""How the assistant reads natural questions: about 80 ways people ask about rail, buses, bikes,
roads and the statistics, each with the part of the assistant that should answer it. A change that
breaks how everyday wording is read fails here."""

import re

import pytest

from src.presentation import advice, analysis, assistant, catalogue, figures, phrasing, road_questions


def branch(q):
    q2, _ = assistant.read_misspellings(q)
    q2 = phrasing.normalise(q2)
    t = q2.lower()
    if assistant.greeting(q2): return "greeting"
    if re.search(assistant.CAPABILITIES, t): return "capabilities"
    if catalogue.asks_about_features(q2): return "features"
    if catalogue.asks_about_data(q2): return "data"
    if advice.asks_for_advice(q2): return "advice"
    r = road_questions.choose(q2)
    if r: return f"road:{r}"
    f = figures.choose(q2)
    if f: return f"figure:{f}"
    if analysis.cannot_analyse(q2): return "cannot"
    if analysis.map_plan(q2): return "map"
    s = analysis.stat_plan(q2)
    if s: return f"stat:{s['stat']}:{s['measure']}"
    p = analysis.plan(q2)
    if p: return f"plan:{'+'.join(p['measures'])}:{p['dimension']}:{p['series'] or ''}:{p['kind'] or ''}"
    topic, when = assistant.route(q2)
    return f"topic:{topic}:{when}" if topic else "OFF_TOPIC"

# (question, expected substring of the branch)
CASES = [
 # rail, nuanced
 ("Is the Tube any better at weekends?", "plan:rail_disruption:day_type"),
 ("Which Tube line should I avoid?", "advice"),
 ("Which line is the most unreliable?", "rail_disruption:entity"),
 ("Does the Tube get worse late at night?", ":hour"),
 ("Are rail problems mostly planned or unplanned?", "figure:"),
 ("Why do lines get disrupted?", "cause"),
 ("What usually causes delays on the Underground?", "cause"),
 ("Do trains come more often in the rush hour?", "plan:tube_wait:hour"),
 ("How long do I usually wait for a Tube?", "tube_wait"),
 ("Is waiting time on the Tube getting longer?", "plan:tube_wait:date"),
 ("Does bunching on the Tube mean longer waits?", "plan:tube_bunching+tube_wait"),
 ("How accurate are the countdown clocks on the Tube?", "countdown"),
 ("Are Tube countdowns less accurate further out?", "stat:horizon"),
 ("Which days had unusually bad disruption?", "stat:anomalies"),
 ("If a line is disrupted now, will it still be disrupted in an hour?", "stat:persistence"),
 ("Is the difference between weekdays and weekends real or just chance?", "stat:significance"),
 ("Is most disruption concentrated on a few lines?", "stat:concentration"),
 ("Do Tube waits predict bunching later?", "stat:lead_lag"),
 ("How spread out are Tube waiting times?", "stat:distribution"),
 ("What's a normal waiting time for the Tube?", "tube_wait"),
 ("Which lines behave alike through the day?", "stat:clusters"),
 ("Break down Tube waiting time into trend and daily pattern", "stat:decomposition"),
 ("What will Tube waits look like tomorrow?", "stat:forecast:tube_wait"),
 ("How often are lifts out of order at stations?", "accessibility"),
 ("When do lifts break down most?", "accessibility"),
 # buses
 ("Do buses tend to clump together more in the rush hour?", "plan:bus_bunching:hour"),
 ("Are buses worse in the evening?", "plan:bus_"),
 ("Which bus routes are the least reliable?", "bus"),
 ("Is bus bunching getting worse?", "plan:bus_bunching:date"),
 ("How long will I wait for a bus on average?", "bus_wait"),
 ("Does bus bunching make waits longer?", "plan:bus_bunching+bus_wait"),
 ("Are bus waits worse at weekends than weekdays?", "bus_wait:day_type"),
 ("Which day of the week is worst for buses?", "plan:bus_"),
 ("Forecast bus bunching for tomorrow", "stat:forecast:bus_bunching"),
 ("Group bus routes by how they behave", "stat:clusters"),
 ("Which bus routes had unusual days?", "stat:anomalies"),
 ("How much extra waiting does bunching cause?", "figure:"),
 ("Is the gap between buses bigger at night?", "plan:bus_network_gap:hour"),
 ("Heatmap of bus waits by hour and day", "heatmap"),
 ("Box plot of bus bunching by route", "box"),
 # bikes
 ("When am I least likely to find a bike?", "bike_empty:hour"),
 ("Where do docks fill up in the morning?", "map"),
 ("Is it harder to return a bike in the evening?", "bike_full:hour"),
 ("Are bike stations emptier at weekends?", "bike_empty:day_type"),
 ("Which stations are empty most often?", "bike_empty"),
 ("How many docks are broken?", "figure:"),
 ("Is bike availability getting worse?", "bike_"),
 ("Do empty stations cluster in certain parts of London?", "map"),
 ("Forecast empty bike stations tomorrow", "stat:forecast:bike_empty"),
 ("Is the weekend difference for bikes significant?", "stat:significance:bike"),
 ("How many bikes are there at a typical station through the day?", "bike_available:hour"),
 # roads
 ("Is traffic worse in the rush hour?", "road_delayed:hour"),
 ("When are the roads quietest?", "advice"),
 ("Which road is the most congested?", "road_delayed:entity"),
 ("Are there more crashes in the evening?", "road_collisions:hour"),
 ("What's the most common kind of road incident?", "road:types"),
 ("Where do most accidents happen?", "road:areas"),
 ("How many accidents happen a day?", "road:daily"),
 ("Do road accidents slow down buses?", "plan:road_collisions+bus_"),
 ("If the roads are jammed, do buses suffer?", "plan:road_delayed+bus_"),
 ("Does more traffic mean longer bus waits?", "plan:road_delayed+bus_wait"),
 ("Are road incidents getting more frequent?", "road_incidents:date"),
 ("How long does it take to clear a crash?", "figure:road_clear"),
 ("Do roadworks overrun?", "figure:road_overrun"),
 ("Forecast road incidents tomorrow", "stat:forecast:road_incidents"),
 ("Are breakdowns more common at weekends?", "road_breakdowns:day_type"),
 ("Is congestion on main roads significantly worse on weekdays?", "stat:significance:road_delayed"),
 ("Which hours have unusual numbers of incidents?", "stat:anomalies"),
 ("Is road congestion concentrated on a few roads?", "stat:concentration:road_delayed"),
 # cross-mode
 ("Does rail disruption push up bus bunching?", "plan:rail_disruption+bus_bunching"),
 ("When the Tube is disrupted, are buses worse?", "plan:rail_disruption+bus_"),
 ("Is there a link between bike shortages and Tube disruption?", "plan:"),
 # advice and nuance
 ("What's the best time to travel by Tube?", "advice"),
 ("I'm driving into London tomorrow morning, any tips?", "advice"),
 ("Should I take the bus or the Tube?", "advice"),
 ("When should I avoid cycling?", "advice"),
 # meta / reasoning
 ("How confident are you in that?", ""),
 ("How much data do you have?", "data"),
 ("What can you analyse?", "capabilities"),
 ("Can you predict delays?", "stat:forecast"),
]


@pytest.mark.parametrize("question, expected", CASES)
def test_natural_questions_reach_the_right_analysis(question, expected):
    assert expected in branch(question)


def test_phrasing_keeps_names_and_only_changes_matched_words():
    assert phrasing.normalise("How many bikes are at Waterloo?") == "How many bikes are at Waterloo?"
    assert phrasing.normalise("Do buses clump together at Victoria?") == "Do buses bunching at Victoria?"
    assert "statistically significant" in phrasing.normalise("Is that real or just chance?")
    # "On average at weekends" compares weekends; "on average" alone asks for the typical value
    assert "typical range" in phrasing.normalise("How long do I wait for a bus on average?")
    assert "typical range" not in phrasing.normalise("How long do I wait for a bus on average at weekends?")
