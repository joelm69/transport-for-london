"""Integration test: the stop disruption BigQuery views on hand-made scenarios.

Builds the views from analytics/stop_disruptions/02_views.sql in a separate dataset
(tfl_sql_tests) over small tables whose correct answers are known in advance:
overlapping notices (gaps and islands), an update, a resolution, a planned-works
overrun, a flicker, and a notice that disappears during a collector restart.

Needs the bq CLI and access to the project. Run on demand:
    .venv/bin/python -m pytest tests/integration/test_stop_disruption_views_bq.py -v
"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "transport-for-london-511318"
TEST_DATASET = "tfl_sql_tests"
LIVE = "transport-for-london-511318.tfl_transport_analytics"
TEST = f"{PROJECT}.{TEST_DATASET}"

CHANGES_SCHEMA = [
    ("poll_id", "STRING"), ("collected_at_utc", "STRING"), ("collection_window", "STRING"),
    ("disruption_key", "STRING"), ("fingerprint", "STRING"), ("previous_fingerprint", "STRING"),
    ("atco_code", "STRING"), ("station_atco_code", "STRING"), ("station_name", "STRING"),
    ("mode", "STRING"), ("type", "STRING"), ("appearance", "STRING"), ("description", "STRING"),
    ("from_utc", "STRING"), ("to_utc", "STRING"), ("to_date_raw", "STRING"),
    ("to_date_kind", "STRING"), ("accessibility_flag", "BOOLEAN"), ("change_type", "STRING"),
    # Nested list of lines, as in the real external table.
    ("concerned_lines", [("id", "STRING"), ("direction", "STRING")]),
]
COVERAGE_SCHEMA = [
    ("poll_id", "STRING"), ("collected_at_utc", "STRING"), ("collection_window", "STRING"),
    ("valid", "BOOLEAN"), ("raw_reason", "STRING"),
]


def at(hhmm):
    return f"2026-10-21T{hhmm}:00+00:00"


def coverage_rows():
    rows = []
    for minute in range(0, 31):                       # session 1: 10:00-10:30
        rows.append({"poll_id": f"a{minute}", "collected_at_utc": at(f"10:{minute:02d}"),
                     "collection_window": "continuous", "valid": True,
                     "raw_reason": "baseline" if minute == 0 else None})
    for minute in range(0, 11):                       # after a restart: 11:00-11:10
        rows.append({"poll_id": f"b{minute}", "collected_at_utc": at(f"11:{minute:02d}"),
                     "collection_window": "continuous", "valid": True,
                     "raw_reason": "baseline" if minute == 0 else None})
    rows.append({"poll_id": "smoke", "collected_at_utc": at("09:30"),
                 "collection_window": "smoke_test", "valid": True, "raw_reason": "baseline"})
    return rows


def change(key, hhmm, change_type, station, *, type_="Information", appearance="RealTime",
           description="notice", from_utc="10:00", to_utc=None, access=False, fp="f1"):
    return {
        "poll_id": hhmm, "collected_at_utc": at(hhmm), "collection_window": "continuous",
        "disruption_key": key, "fingerprint": fp, "previous_fingerprint": None,
        "atco_code": station, "station_atco_code": station, "station_name": f"Station {station}",
        "mode": "tube", "type": type_, "appearance": appearance, "description": description,
        "from_utc": at(from_utc), "to_utc": at(to_utc) if to_utc else None, "to_date_raw": None,
        "to_date_kind": "planned" if appearance == "PlannedWork" else "end_of_day",
        "accessibility_flag": access, "change_type": change_type,
    }


def change_rows():
    return [
        # S1: lift outage A (10:00-10:20) overlaps escalator notice B (from 10:10, still open)
        change("A", "10:00", "BASELINE", "S1", description="Lift out of service", from_utc="09:00", access=True),
        change("A", "10:20", "RESOLVED", "S1", description="Lift out of service", from_utc="09:00", access=True),
        change("B", "10:10", "NEW_INCIDENT", "S1", description="No escalator", from_utc="10:10", access=True),
        change("B", "11:00", "BASELINE", "S1", description="No escalator", from_utc="10:10", access=True),
        # S2: planned part closure, updated 10:05, planned end 10:15, resolved 10:25 (+10 min overrun)
        change("C", "10:00", "BASELINE", "S2", type_="Part Closure", appearance="PlannedWork",
               from_utc="08:00", to_utc="10:15"),
        change("C", "10:05", "UPDATE", "S2", type_="Part Closure", appearance="PlannedWork",
               from_utc="08:00", to_utc="10:15", fp="f2"),
        change("C", "10:25", "RESOLVED", "S2", type_="Part Closure", appearance="PlannedWork",
               from_utc="08:00", to_utc="10:15", fp="f2"),
        # S3: flicker, appears 10:12 and vanishes 10:13
        change("D", "10:12", "NEW_INCIDENT", "S3", from_utc="10:12"),
        change("D", "10:13", "RESOLVED", "S3", from_utc="10:12"),
        # S4: present in session 1, absent from the post-restart baseline (no RESOLVED row)
        change("E", "10:00", "BASELINE", "S4", from_utc="07:00"),
        # Smoke test row must be ignored
        {**change("Z", "09:30", "BASELINE", "S9"), "collection_window": "smoke_test"},
    ]


def bq(*args, stdin=None):
    result = subprocess.run(["bq", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def truthy(value):
    """bq --format=json returns booleans as the strings 'true' / 'false'."""
    return str(value).lower() == "true"


def query(sql):
    out = bq("query", "--use_legacy_sql=false", f"--project_id={PROJECT}", "--format=json", sql)
    return json.loads(out[out.index("["):]) if "[" in out else []


def schema_json(schema):
    """BigQuery JSON schema; a list type means a repeated RECORD."""
    fields = []
    for name, kind in schema:
        if isinstance(kind, list):
            fields.append({"name": name, "type": "RECORD", "mode": "REPEATED",
                           "fields": schema_json(kind)})
        else:
            fields.append({"name": name, "type": kind})
    return fields


def load(table, rows, schema):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as data, \
            tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as spec:
        data.write("\n".join(json.dumps(r) for r in rows))
        json.dump(schema_json(schema), spec)
    bq("load", f"--project_id={PROJECT}", "--replace", "--source_format=NEWLINE_DELIMITED_JSON",
       f"{TEST_DATASET}.{table}", data.name, spec.name)


@pytest.fixture(scope="module")
def built():
    bq("--location=US", "mk", "--force", "--dataset", f"{PROJECT}:{TEST_DATASET}")
    load("ext_stop_disruption_changes", change_rows(), CHANGES_SCHEMA)
    load("ext_stop_disruption_coverage", coverage_rows(), COVERAGE_SCHEMA)
    views = (ROOT / "analytics" / "stop_disruptions" / "02_views.sql").read_text().replace(LIVE, TEST)
    bq("query", "--use_legacy_sql=false", f"--project_id={PROJECT}", "--format=none", stdin=views)
    return True


def test_sessions_split_at_gap_and_restart(built):
    rows = query(f"SELECT FORMAT_TIMESTAMP('%H:%M', session_start) s, FORMAT_TIMESTAMP('%H:%M', session_end) e, polls "
                 f"FROM `{TEST}.stop_disruption_sessions` ORDER BY s")
    assert [(r["s"], r["e"], int(r["polls"])) for r in rows] == [("10:00", "10:31", 31), ("11:00", "11:11", 11)]


def test_incidents(built):
    rows = {r["disruption_key"]: r for r in query(
        f"SELECT disruption_key, is_censored, is_flap, updates, "
        f"FORMAT_TIMESTAMP('%H:%M', last_seen) last_seen, ROUND(overrun_hours * 60) overrun_min "
        f"FROM `{TEST}.stop_disruption_incidents`")}
    assert set(rows) == {"A", "B", "C", "D", "E"}            # smoke test excluded
    assert not truthy(rows["A"]["is_censored"])
    assert truthy(rows["B"]["is_censored"]) and rows["B"]["last_seen"] == "11:11"
    assert int(rows["C"]["updates"]) == 1 and float(rows["C"]["overrun_min"]) == 10
    assert truthy(rows["D"]["is_flap"])
    # Vanished during the restart: ends at session 1's end, not stretched to 11:11.
    assert truthy(rows["E"]["is_censored"]) and rows["E"]["last_seen"] == "10:31"


def test_station_minutes_merge_overlaps(built):
    rows = {r["atco_code"]: r for r in query(
        f"SELECT atco_code, observed_minutes, notice_minutes, accessibility_minutes, part_closure_minutes "
        f"FROM `{TEST}.station_disruption_daily`")}
    s1 = rows["S1"]
    # A (10:00-10:20) and B (10:10-10:31) merge to 10:00-10:31 = 31, plus 11:00-11:11 = 11.
    assert float(s1["accessibility_minutes"]) == 42 and float(s1["notice_minutes"]) == 42
    assert float(rows["S2"]["part_closure_minutes"]) == 25
    assert float(rows["S4"]["notice_minutes"]) == 31
    assert "S3" not in rows                                   # flicker excluded
    assert float(s1["observed_minutes"]) == 42


def test_accessibility_daily(built):
    rows = query(f"SELECT mode, stations_with_accessibility_issues n, stations_part_closed pc "
                 f"FROM `{TEST}.accessibility_daily`")
    assert [(r["mode"], int(r["n"]), int(r["pc"])) for r in rows] == [("tube", 1, 1)]
