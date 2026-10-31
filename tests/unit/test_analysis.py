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
    assert assistant.answer("Chart the weather in Paris", Data())["answer"].startswith("I can't analyse weather")
    assert assistant.answer("Who won the football?", Data())["answer"].startswith("Sorry, I can only")


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
    assert analysis.STAT_SUGGESTIONS[0] in result["suggestions"]
    assert "**The recommendations' figures:**" in result["answer"]


def test_analyses_it_cannot_do_get_suggestions_of_the_same_kind():
    # Bike and road history is in the chat's dataset, so these are analysed
    assert stat_plan("Draw histogram of the bikes") == {"stat": "distribution", "measure": "bike_empty"}
    assert analysis.cannot_analyse("Pie chart of road traffic") is None
    weather = assistant.answer("Plot the weather against disruption", Data())
    assert weather["answer"].startswith("I can't analyse weather: this platform doesn't collect weather data")
    which = assistant.answer("draw a histogram", Data())
    assert "Which one?" in which["answer"] and which["suggestions"] == analysis.KIND_SUGGESTIONS["histogram"]
    scatter = assistant.answer("Scatter plot please", Data())
    assert scatter["suggestions"] == analysis.KIND_SUGGESTIONS["scatter"]
    # Questions it can answer are not caught
    assert analysis.cannot_analyse("Is there a relationship between time of day and bus bunching?") is None
    assert analysis.cannot_analyse("How many bikes are at Waterloo?") is None
    assert analysis.cannot_analyse("Show a pie chart of the reasons for rail disruption") is None


def test_misspellings_are_read_and_said():
    fixed, changes = assistant.read_misspellings("histgram of bus bunchng")
    assert fixed == "histogram of bus bunching" and changes == [("histgram", "histogram"), ("bunchng", "bunching")]
    assert assistant.read_misspellings("Is the Central line running?") == ("Is the Central line running?", [])
    result = assistant.answer("histgram of bus bunchng", Data())
    assert result["answer"].startswith("_Reading “histgram” as “histogram”, “bunchng” as “bunching”._")


# More charts, trends, regression, the catalogue -------------------------------------------------

from src.presentation import catalogue   # noqa: E402
from src.presentation.analysis import linear_fit, permutation_p   # noqa: E402


def test_chart_kinds_shape_the_plan():
    assert plan("Heatmap of bus bunching by hour and day of week") == {
        "measures": ["bus_bunching"], "dimension": "hour", "series": "weekday", "kind": "heatmap"}
    assert plan("Bus bunching by hour and day of week")["series"] == "weekday"
    assert plan("Box plot of Tube waiting time by line")["kind"] == "box"
    assert plan("Box plot of Tube waiting time")["dimension"] == "entity"
    assert plan("Control chart of rail disruption")["dimension"] == "date"
    assert plan("Rolling average of Tube bunching")["dimension"] == "time"
    assert plan("Is rail disruption getting worse over time?")["trend"] is True
    assert "LIMIT 400" in build_sql(plan("Heatmap of bus bunching by hour and day of week"))   # 168 cells fit


def test_trend_statistics():
    slope, intercept, r2 = linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
    assert (round(slope, 6), round(intercept, 6), round(r2, 6)) == (2, 1, 1)
    assert permutation_p(list(range(10)), [v * 2 for v in range(10)]) < 0.01
    assert permutation_p(list(range(8)), [1, 3, 2, 3, 1, 2, 3, 1]) > 0.2


class Boxes(Sources):
    def query(self, sql):
        return [{"g": line, "s": line, "v": v} for line, values in
                (("central", [3, 4, 5, 6, 7, 30]), ("victoria", [1, 1.5, 2, 2.5, 3, 3.5])) for v in values]


def test_box_plot():
    result = analysis.run(plan("Box plot of Tube waiting time by line"), Boxes())
    assert result["chart"]["kind"] == "box"
    first = result["chart"]["data"][0]
    assert first["x"] == "Central" and first["median"] == 5.5 and first["outliers"] == 1
    assert "The highest median is **Central**" in result["answer"]


def test_catalogue_and_features():
    assert catalogue.asks_about_data("What data do you collect?")
    assert catalogue.asks_about_data("How often is BikePoint collected?")
    assert not catalogue.asks_about_data("How often are bike stations empty?")
    assert catalogue.describe("How often is BikePoint collected?").startswith("- **Santander Cycles docking stations**")
    assert "Needs new data or development" in assistant.answer("Suggest new features", Data())["answer"]


def test_bike_road_and_all_route_measures():
    assert plan("Heatmap of empty bike stations by hour and day of week")["measures"] == ["bike_empty"]
    assert plan("How do full bike stations change through the day?")["measures"] == ["bike_full"]
    assert plan("Are main roads more delayed at weekends?")["measures"] == ["road_delayed"]
    assert plan("Plot road incidents by hour")["measures"] == ["road_incidents"]
    assert plan("Compare bus bunching by route")["measures"] == ["bus_network_bunching"]
    assert plan("How does bus bunching change through the day?")["measures"] == ["bus_bunching"]   # 12 routes, hourly
    assert plan("Is the Central line running?") is None
    sql = analysis.stat_sql(stat_plan("Draw histogram of the bikes"))
    assert "bike_station_hourly" in sql and "FARM_FINGERPRINT" in sql   # an even sample, not alphabetical


# Forecasting, decomposition, clustering ---------------------------------------------------------

from datetime import datetime, timedelta as _td, timezone   # noqa: E402

from src.presentation.analysis import kmeans   # noqa: E402


def _hours(days, base=0.10, cycle=0.05):
    """Hourly rows with a clear daily cycle (peak at 18:00)."""
    from zoneinfo import ZoneInfo
    start = datetime(2026, 11, 2, 0, tzinfo=timezone.utc)   # after the clocks changed: London = UTC
    rows = []
    for i in range(days * 24):
        t = start + _td(hours=i)
        peak = t.astimezone(ZoneInfo("Europe/London")).hour == 18
        rows.append({"d0": t, "value": base + (cycle if peak else 0) + 0.001 * (i % 5), "weight": 100.0})
    return rows


def test_new_stat_plans():
    assert stat_plan("Forecast Tube waiting time for the next 24 hours") == {"stat": "forecast", "measure": "tube_wait"}
    assert stat_plan("Decompose bus bunching into trend and daily cycle") == {
        "stat": "decomposition", "measure": "bus_bunching"}   # "daily cycle" is not cycling
    assert stat_plan("Cluster bike stations by when they run empty") == {"stat": "clusters", "measure": "bike_empty"}
    assert stat_plan("Cluster bus routes by bunching") == {"stat": "clusters", "measure": "bus_network_bunching"}
    assert stat_plan("Forecast all-routes bus bunching")["measure"] == "bus_bunching"   # needs an hourly series


def test_forecast_and_decomposition():
    forecast = run_stat({"stat": "forecast", "measure": "tube_wait", "measures": ["tube_wait"]}, Stats(_hours(8)))
    assert "should peak around **18:00**" in forecast["answer"] and forecast["chart"]["kind"] == "line"
    assert {d["series"] for d in forecast["chart"]["data"]} >= {"Forecast", "95% range (high)", "Recent hours"}
    assert "needs at least three days" in run_stat({"stat": "forecast", "measure": "tube_wait"}, Stats(_hours(2)))["answer"]
    parts = run_stat({"stat": "decomposition", "measure": "bus_bunching"}, Stats(_hours(6)))
    assert "The cycle is highest around 18:00" in parts["answer"] and len(parts["charts"]) == 2


def test_kmeans_groups_alike_profiles():
    vectors = [[1, 1], [1.1, 0.9], [5, 5], [5.2, 4.8], [9, 9], [9.1, 8.9]]
    labels, _ = kmeans(vectors, 3)
    assert labels[0] == labels[1] and labels[2] == labels[3] and labels[4] == labels[5]
    assert len(set(labels)) == 3


def test_map_plan_and_morans_i():
    p = analysis.map_plan("Where in London do bike stations run out of bikes during the evening rush?")
    assert p["measure"] == "bike_empty" and p["label"].startswith("the weekday evening rush")
    assert analysis.map_plan("Where is the Central line disrupted?") is None   # maps are for bike stations
    # Two tight groups with alike values -> positive autocorrelation
    points = [(51.50 + i * 0.001, -0.10, 0.9) for i in range(6)] + [(51.60 + i * 0.001, -0.20, 0.0) for i in range(6)]
    moran, p_value = analysis.morans_i(points, rounds=99)
    assert moran > 0.5 and p_value < 0.05


def test_road_questions():
    p = plan("Do more road incidents make London's main roads more delayed?")
    assert p["measures"] == ["road_incidents", "road_delayed"]
    assert "'London' AS entity" in build_sql(p)   # compared London-wide, hour by hour
    assert plan("Compare main road delays by road")["dimension"] == "entity"

    class Pairs(Sources):
        def query(self, sql):
            return [{"x": float(i % 4), "y": 0.3 + 0.01 * (i % 4)} for i in range(30)]
    text = analysis.run(p, Pairs())["answer"]
    assert "for each extra incident reported in the hour" in text
    assert "TfL's main-road rating changes rarely" in text and "With only 30 hours" in text


def test_relationship_follow_ups_stay_on_topic():
    ups = analysis.follow_ups(plan("Do more road incidents make London's main roads more delayed?"))
    assert ups and all("road" in q for q in ups)
    assert all(plan(q) for q in ups)


def test_roads_and_buses_are_compared_across_london():
    p = plan("Is there a relationship between road disruptions and bus traffic?")
    assert p["measures"] == ["road_delayed", "bus_bunching"]
    assert plan("Do road incidents make buses bunch more?")["measures"] == ["road_incidents", "bus_bunching"]
    assert plan("Is bus waiting time related to main road delays?")["measures"] == ["road_delayed", "bus_wait"]
    assert "'London' AS entity" in build_sql(p)   # roads and bus routes meet at London level
    assert plan("Is bunching related to waiting time on the Tube?")["measures"] == ["tube_bunching", "tube_wait"]

    class Pairs(Sources):
        def query(self, sql):
            return [{"x": 0.3 + 0.01 * (i % 5), "y": 0.1 + 0.02 * (i % 5) + 0.001 * (i % 3)} for i in range(30)]
    text = analysis.run(p, Pairs())["answer"]
    assert "over 30 hours" in text and "time of day rather than" in text and "a weak link" not in text
