"""Charts of the recommendation figures: choosing one from a question and its numbers."""

from src.presentation import (assistant, bike_recommendation, bus_recommendation, figures, rail_recommendation,
                              road_recommendation)
from tests.unit.test_bike_recommendation import ROW as BIKE_ROW
from tests.unit.test_bus_recommendation import ROW as BUS_ROW
from tests.unit.test_road_recommendation import ROW as ROAD_ROW

RAIL_ROW = {
    "first_day": "2026-10-24", "last_day": "2026-10-30", "in_service_hours": 3017.5, "disrupted_hours": 1036.2,
    "planned_hours": 557.0, "unplanned_hours": 479.2, "good_service": 0.6566, "good_service_without_planned": 0.8412,
    "planned_share": 0.5375, "unplanned_share": 0.4625, "unplanned_service_share": 0.1588, "train_share": 0.4833,
    "infrastructure_share": 0.2241, "top_lines_share": 0.4435,
    "top_lines": [{"line_name": "Central", "main_cause": "Train cancellations", "main_cause_share": 0.85,
                   "unplanned_hours": 85.6, "unplanned_share": 0.556}],
}


class Data:
    def recommendation(self, area):
        return {"bus": (BUS_ROW, bus_recommendation), "bikes": (BIKE_ROW, bike_recommendation),
                "roads": (ROAD_ROW, road_recommendation), "rail": (RAIL_ROW, rail_recommendation)}[area]

    def query(self, sql):
        return []

    def docs(self, query):
        return []


QUESTION = ("Plot a two-bar comparison showing the theoretical wait time (6 minutes) versus the actual experienced "
            "wait time (7 minutes). Side-by-side, create a high-impact callout chart representing the 71,000 "
            "passenger-hours lost daily out of the baseline 5 million daily journeys to emphasize the hidden "
            "macro-scale cost of uneven spacing.")


def test_choose():
    assert figures.choose(QUESTION) == "bus_excess_wait"
    assert figures.choose("Show planned versus unplanned rail disruption") == "rail_service_split"
    assert figures.choose("Chart trains versus infrastructure causes") == "rail_train_vs_track"
    assert figures.choose("Chart how often bike stations are empty or full") == "bike_empty_full"
    assert figures.choose("How many bike docks are out of use?") == "bike_out_of_use"
    assert figures.choose("Do roadworks overrun?") == "road_overrun"
    assert figures.choose("How many bike docks are out of use right now?") is None   # live, not this week
    assert figures.choose("Which bus routes bunch most?") is None                    # the text answer
    assert figures.choose("Is the Central line running?") is None


def test_the_wait_comparison_and_the_callout():
    result = assistant.answer(QUESTION, Data())
    bars, callout = result["charts"]
    assert bars["kind"] == "bar" and [d["x"] for d in bars["data"]] == ["Even spacing (theory)", "Actual (experienced)"]
    assert [round(d["y"], 2) for d in bars["data"]] == [6.24, 7.04]
    assert [d["colour"] for d in bars["data"]] == ["#8E8E93", "#FF9F0A"]
    assert callout == {"kind": "callout", "value": "67,293", "label": "passenger-hours lost every day",
                       "detail": "to uneven bus spacing: 0.8 extra minutes (12.8%) on each of about 5.05 million "
                                 "daily journeys"}
    assert "**6.2 min**" in result["answer"] and "**67,293 passenger-hours lost every day**" in result["answer"]
    assert "upper estimate" in result["answer"]


def test_rail_bike_and_road_figures():
    rail = assistant.answer("Show planned versus unplanned rail disruption", Data())
    assert rail["charts"][0]["kind"] == "pie" and rail["charts"][1]["value"] == "84.1%"
    assert "rise from 65.7% to **84.1%**" in rail["answer"]
    bikes = assistant.answer("Chart how often bike stations are empty or full", Data())
    assert [d["x"] for d in bikes["charts"][0]["data"]][2] == "Empty, weekday peaks"
    roads = assistant.answer("Do roadworks overrun?", Data())
    assert "29 of the 40 roadworks" in roads["answer"] and roads["charts"][1]["value"] == "19 min"
