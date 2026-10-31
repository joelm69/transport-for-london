"""Analyses behind the assistant's charts: choosing one from a question, and the finding it writes."""

from src.presentation import analysis, assistant, bus_recommendation
from src.presentation.analysis import SQL, choose
from tests.unit.test_assistant import Sources
from tests.unit.test_bus_recommendation import ROW as BUS_ROW

ROWS = {
    "rail_by_line": [{"line": "Tram", "disrupted": 0.99}, {"line": "Central", "disrupted": 0.55},
                     {"line": "Victoria", "disrupted": 0.05}],
    "rail_by_hour": [{"hour": 7, "disrupted": 0.2}, {"hour": 8, "disrupted": 0.31}, {"hour": 23, "disrupted": 0.1}],
    "rail_causes": [{"cause": "Train cancellations", "hours": 150.0}, {"cause": "Weather", "hours": 50.0}],
    "tube_bunching": [{"line": "hammersmith-city", "bunching": 0.12}, {"line": "victoria", "bunching": 0.04}],
}


class Charts(Sources):
    def query(self, sql):
        name = next(n for n, q in SQL.items() if q == sql)
        return ROWS.get(name, [])

    def recommendation(self, area):
        return (BUS_ROW, bus_recommendation) if area == "bus" else None


def test_choose():
    assert choose("Chart disruption by line this week") == "rail_by_line"
    assert choose("Graph disruption by hour of day") == "rail_by_hour"
    assert choose("Show me the trend of good service over time") == "rail_by_day"
    assert choose("What causes disruption?") == "rail_causes"
    assert choose("Plot Tube bunching by line") == "tube_bunching"
    assert choose("Chart the slowest bus routes") == "bus_slowest"
    assert choose("Graph how long road incidents take to clear") == "roads_clear"
    assert choose("Chart lift outages") == "accessibility"
    assert choose("Is the Central line running?") is None        # not a chart
    assert choose("Chart the weather in Paris") is None           # nothing this platform collects


def test_findings_and_charts():
    result = analysis.run("rail_by_line", Charts())
    assert result["answer"].startswith("Over the last 7 complete days, the **Tram** line was disrupted most (99.0%")
    assert result["chart"]["kind"] == "bar" and result["chart"]["format"] == "pct"
    hour = analysis.run("rail_by_hour", Charts())
    assert "**08:00** was the most disrupted hour (31.0%" in hour["answer"]
    assert hour["chart"]["data"][0] == {"hour": "07:00", "disrupted": 0.2}
    assert "75.0% of the disrupted time" in analysis.run("rail_causes", Charts())["answer"]
    tube = analysis.run("tube_bunching", Charts())
    assert "**Hammersmith & City**" in tube["answer"]
    bus = analysis.run("bus_bunched", Charts())
    assert bus["chart"]["data"][0] == {"route": "Route 29", "bunching": 0.2452}


def test_no_data_yet():
    assert analysis.run("tube_wait", Charts()) == {"answer": "There isn't enough data for that chart yet.", "chart": None}


def test_the_assistant_returns_the_chart():
    result = assistant.answer("Chart disruption by line this week", Charts())
    assert result["chart"]["title"] == "Share of service time disrupted, by line"
    assert result["source"] == "BigQuery analysis of the last 7 complete days"
    assert assistant.answer("Chart the weather in Paris", Charts())["answer"].startswith("Sorry, I can only")
