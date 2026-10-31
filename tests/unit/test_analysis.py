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


# Statistical analyses --------------------------------------------------------------------------

from datetime import date, timedelta   # noqa: E402

from src.presentation.analysis import run_stat, stat_plan, stat_sql   # noqa: E402

DAY0 = date(2026, 10, 19)   # a Monday


class Stats(Sources):
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        return self.rows


def test_stat_plans():
    assert stat_plan("What is the distribution of Tube waiting times?") == {"stat": "distribution", "measure": "tube_wait"}
    assert stat_plan("Is the weekend difference in rail disruption statistically significant?")["stat"] == "significance"
    assert stat_plan("Which line-days had unusual disruption?")["stat"] == "anomalies"
    assert stat_plan("Once a line is disrupted, how likely is it to stay disrupted the next hour?")["stat"] == "persistence"
    assert stat_plan("Does bunching give an early warning of disruption?") == {"stat": "lead_lag", "measure": "tube_bunching"}
    assert stat_plan("Are lift outages concentrated on a few stations?") == {"stat": "concentration", "measure": "accessibility"}
    assert stat_plan("How does countdown accuracy improve as the train approaches?")["stat"] == "horizon"
    assert stat_plan("Is the Central line running?") is None
    assert "DROP" not in stat_sql(stat_plan("What is the distribution of Tube waiting times? DROP TABLE x"))


def test_distribution():
    rows = [{"e": "central", "t": i, "v": v} for i, v in enumerate([3, 4, 4, 5, 5, 5, 6, 6, 7, 20])]
    result = run_stat({"stat": "distribution", "measure": "tube_wait"}, Stats(rows))
    assert result["answer"].startswith("Across 10 line-hours, the median Tube waiting time was **5.0 min**")
    assert "right-skewed" in result["answer"] and result["chart"]["kind"] == "bar"
    assert round(sum(d["y"] for d in result["chart"]["data"]), 6) == 1


def test_significance_permutation_test():
    days = [DAY0 + timedelta(days=i) for i in range(14)]
    rows = [{"d": d, "weekend": d.weekday() >= 5, "v": 0.5 if d.weekday() >= 5 else 0.2 + 0.01 * (i % 3)}
            for i, d in enumerate(days)]
    result = run_stat({"stat": "significance", "measure": "rail_disruption"}, Stats(rows))
    assert "higher on weekends" in result["answer"] and "**unlikely to be chance**" in result["answer"]
    assert result["chart"]["kind"] == "strip"
    same = [{**r, "v": 0.3} for r in rows]
    assert "could well be chance" in run_stat({"stat": "significance", "measure": "rail_disruption"},
                                              Stats(same))["answer"]


def test_persistence_and_horizon():
    rows = [{"e": "Central", "dis": 100, "stay": 90, "ok": 200, "start": 10}]
    text = run_stat({"stat": "persistence", "measure": "rail_disruption"}, Stats(rows + rows))["answer"]
    assert "**90%** of the time" in text and "**5%**" in text and "about **10 hours**" in text
    horizon = [{"h": 10, "error": 57.0, "bias": 41.0, "n": 1}, {"h": 2, "error": 21.0, "bias": 11.0, "n": 1}]
    text = run_stat({"stat": "horizon", "measure": "tube_countdown"}, Stats(horizon))["answer"]
    assert "falls from **57 s** 10 min before to **21 s** 2 min before" in text and "optimistic" in text


def test_capabilities_lists_the_analyses():
    result = assistant.answer("What analyses can you do?", Data())
    assert "**Statistics:**" in result["answer"]
    assert result["suggestions"][0] == analysis.STAT_SUGGESTIONS[0]
