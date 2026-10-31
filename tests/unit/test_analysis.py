"""The analysis engine: reading a question, choosing the chart, and the numbers behind the answer."""

from src.presentation import analysis, assistant
from src.presentation.analysis import build_sql, pearson, plan
from tests.unit.test_assistant import Sources

HOURS = [{"d0": h, "s0": h, "value": v, "weight": 100, "days": 9}
         for h, v in [(3, 0.05), (8, 0.11), (12, 0.14), (19, 0.155)]]
CAUSES = [{"d0": "Not stated", "value": 300.0, "weight": 1, "days": 10},
          {"d0": "Train cancellations", "value": 220.0, "weight": 1, "days": 10},
          {"d0": "Operational Incident", "value": 150.0, "weight": 1, "days": 10}]
PAIRS = [{"entity": "central", "hour_start": i, "x": x, "y": 2 * x + 1} for i, x in enumerate([0.1, 0.2, 0.3, 0.4])]


class Data(Sources):
    def query(self, sql):
        if "main_cause" in sql:
            return CAUSES
        if "JOIN m1" in sql:
            return PAIRS
        return HOURS


def test_plans():
    assert plan("Is there a relationship between time of day and bus bunching?") == {
        "measures": ["bus_bunching"], "dimension": "hour", "series": None, "kind": None}
    assert plan("Is bunching related to waiting time on the Tube?")["measures"] == ["tube_bunching", "tube_wait"]
    assert plan("Do weekends have less rail disruption than weekdays?")["dimension"] == "day_type"
    assert plan("Which day of the week has the most rail disruption?")["dimension"] == "weekday"
    assert plan("Bus bunching by hour on weekdays and weekends")["series"] == "day_type"
    assert plan("Compare Tube bunching by line")["dimension"] == "entity"
    assert plan("I want a pie chart for the reason why we have disruptions in the lines") == {
        "measures": ["rail_disruption"], "dimension": "cause", "series": None, "kind": "pie"}
    assert plan("Is the Central line running?") is None
    assert plan("What does bunching mean?") is None
    assert plan("Which lines were least reliable this week?") is None   # answered by the recommendation


def test_sql_comes_from_the_catalogue_only():
    sql = build_sql(plan("Is there a relationship between time of day and bus bunching? DROP TABLE x"))
    assert "DROP" not in sql and "mode = 'bus'" in sql and "arrival_line_hourly" in sql


def test_line_chart_through_the_day():
    result = analysis.run(plan("Is there a relationship between time of day and bus bunching?"), Data())
    assert result["chart"]["kind"] == "line"
    assert result["answer"].startswith("**Yes: bus bunching changes strongly through the day.** It is highest at "
                                       "**19:00** (15.5%) and lowest at **03:00** (5.0%)")
    assert "_Based on 9 days of data" in result["answer"]


def test_pie_of_reasons_and_unsuitable_pies():
    pie = analysis.run(plan("Show a pie chart of the reasons for rail disruption"), Data())
    assert pie["chart"]["kind"] == "pie"
    assert pie["answer"].startswith("**Train cancellations** was the most common stated reason: 220 disrupted "
                                    "line-hours, 33% of all disrupted time")
    assert "no reason for 45%" in pie["answer"]
    hourly = analysis.run(plan("Pie chart of bus bunching by hour"), Data())
    assert hourly["chart"]["kind"] == "line" and "A pie chart shows parts of a whole" in hourly["answer"]


def test_scatter_and_correlation():
    assert round(pearson([1, 2, 3], [2, 4, 6]), 6) == 1
    result = analysis.run(plan("Is bunching related to waiting time on the Tube?"), Data())
    assert result["chart"]["kind"] == "scatter"
    assert result["answer"].startswith("**There is a strong relationship** (correlation r = 1.00")


def test_the_assistant_returns_the_chart():
    result = assistant.answer("Is there a relationship between time of day and bus bunching?", Data())
    assert result["chart"]["kind"] == "line"
    assert result["source"] == "BigQuery analysis of the last 28 days"
    assert assistant.answer("Chart the weather in Paris", Data())["answer"].startswith("Sorry, I can only")
