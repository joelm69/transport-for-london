"""The agent's docs search and the read-only guard on its SQL tool."""

from src.agent import docs_search
from src.agent.tools import DATASET, check_sql


def test_docs_search_finds_the_definition_first():
    assert docs_search.search("what does bunched mean")[0]["title"] == "Buses: bunching"
    assert docs_search.search("What does bunching mean?")[0]["title"] == "Buses: bunching"
    assert docs_search.search("why the median time to clear")[0]["title"] == "Roads: incidents and time to clear"
    assert docs_search.search("what is headroom")[0]["title"] == "Bikes: problem share, peaks, headroom"


def test_docs_search_returns_nothing_for_unrelated_words():
    assert docs_search.search("zzzz qqqq") == []


def test_check_sql_allows_read_only_queries_on_the_dataset():
    assert check_sql(f"SELECT line_name FROM `{DATASET}.line_disruption_hourly` LIMIT 5") is None
    assert check_sql(f"WITH d AS (SELECT * FROM `{DATASET}.line_disruption_hourly`) "
                     "SELECT line_name, SUM(disrupted_minutes) FROM d GROUP BY 1;") is None
    assert check_sql(f"SELECT t.line FROM `{DATASET}.rail_recommendation`, UNNEST(top_lines) AS t") is None


def test_check_sql_blocks_anything_else():
    other = "`transport-for-london-511318.tfl_transport_analytics.road_disruptions`"
    assert "Only tables in" in check_sql(f"SELECT * FROM {other}")
    assert "in full" in check_sql("SELECT * FROM line_disruption_hourly")
    assert "Only SELECT" in check_sql(f"DELETE FROM `{DATASET}.bus_recommendation` WHERE TRUE")
    assert "one statement" in check_sql(f"SELECT 1; DROP TABLE `{DATASET}.bus_recommendation`")
    assert "read-only" in check_sql(f"WITH x AS (SELECT 1) DELETE FROM `{DATASET}.bus_recommendation` WHERE TRUE")
    assert check_sql(f"SELECT 'drop' AS word FROM `{DATASET}.bus_recommendation`") is None   # words in strings are fine
