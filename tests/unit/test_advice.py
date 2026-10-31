"""Travel advice: tips worked out from the figures, for the transport the question names."""

from src.presentation import advice, assistant, bike_recommendation, bus_recommendation
from tests.unit.test_bike_recommendation import ROW as BIKE_ROW
from tests.unit.test_bus_recommendation import ROW as BUS_ROW
from tests.unit.test_road_recommendation import ROW as ROAD_ROW
from src.presentation import road_recommendation

HOURS = [{"d0": h, "s0": h, "value": v, "weight": 1000.0, "days": 9}
         for h, v in [(6, 0.07), (8, 0.10), (12, 0.14), (19, 0.16), (23, 0.12)]]


class Data:
    def query(self, sql):
        if "IF(EXTRACT(DAYOFWEEK" in sql:
            return [{"d0": "Weekday", "value": 0.28, "weight": 1.0}, {"d0": "Weekend", "value": 0.43, "weight": 1.0}]
        if "line_id AS d0" in sql:
            return [{"d0": n, "value": v, "weight": 1.0} for n, v in
                    [("suffragette", 0.8), ("piccadilly", 0.5), ("central", 0.45), ("victoria", 0.1),
                     ("jubilee", 0.05), ("weaver", 0.02)]]
        return HOURS

    def recommendation(self, area):
        return {"bus": (BUS_ROW, bus_recommendation), "bikes": (BIKE_ROW, bike_recommendation),
                "roads": (ROAD_ROW, road_recommendation)}.get(area)

    def line_cards(self):
        return [{"line_name": "Central", "status": "Minor Delays", "level": "delays"}]

    def roads(self):
        raise TimeoutError   # a failing source is skipped


def test_asks_for_advice():
    assert advice.asks_for_advice("Any travel tips for the Tube?")
    assert advice.asks_for_advice("When is the best time to catch a bus?")
    assert advice.asks_for_advice("Which lines should I avoid?")
    assert not advice.asks_for_advice("Is the Central line running?")


def test_tube_tips_quote_the_figures():
    text = advice.advise("Any travel tips for the Tube?", Data())["answer"]
    assert "**Shortest Tube waits** are around **06:00**" in text
    assert "**Allow extra time on the Suffragette, Piccadilly and Central lines**" in text
    assert "43% of the time at weekends against 28% on weekdays" in text
    assert "**Right now:** Central: Minor Delays." in text


def test_bus_bike_and_road_tips():
    bus = advice.advise("When is the best time to catch a bus?", Data())
    assert "**Buses are most evenly spaced around 06:00** (7% of gaps bunched)" in bus["answer"]
    assert "**Allow extra time on routes 189, 19, 98**" in bus["answer"]
    assert "Give me traffic tips for driving in London" in bus["suggestions"]
    bikes = advice.advise("Any advice for cycling?", Data())["answer"]
    assert "**Docking stations are emptiest around 23:00**" in bikes
    assert "Exhibition Road Museums 1 (South Kensington)" in bikes
    roads = advice.advise("traffic tips for driving", Data())["answer"]
    assert "**Plan around A3, South Circular (A205), North Circular (A406), A41**" in roads
    assert "Right now TfL rates" not in roads   # the live road feed failed and is skipped


def test_the_assistant_gives_advice_for_every_mode_when_none_is_named():
    result = assistant.answer("Any travel tips?", Data())
    for title in ("**Tube and rail**", "**Buses**", "**Santander Cycles**", "**Driving and traffic**"):
        assert title in result["answer"]
