import copy

from src.validation.line_status import ERROR, WARN, has_errors, validate_poll
from tests.unit.helpers import line, with_status


def run(data, config, severity_codes, collected_at):
    return validate_poll(
        data, config["expected_line_ids"], severity_codes,
        set(config["severity_classes"]["good"]), collected_at,
    )


def rules(issues, level=None):
    return sorted({i.rule for i in issues if level is None or i.level == level})


def test_live_responses_have_no_issues(config, severity_codes, collected_at, first_poll, second_poll):
    assert run(first_poll, config, severity_codes, collected_at) == []
    assert run(second_poll, config, severity_codes, collected_at) == []


def test_p1_non_array_is_error(config, severity_codes, collected_at):
    issues = run({"message": "error"}, config, severity_codes, collected_at)
    assert rules(issues, ERROR) == ["P1"]


def test_p2_missing_line_is_error(config, severity_codes, collected_at, first_poll):
    data = [item for item in first_poll if item["id"] != "victoria"]
    issues = run(data, config, severity_codes, collected_at)
    assert has_errors(issues)
    assert "victoria" in issues[0].message


def test_p2_duplicate_line_is_error(config, severity_codes, collected_at, first_poll):
    data = first_poll + [copy.deepcopy(line(first_poll, "central"))]
    assert rules(run(data, config, severity_codes, collected_at), ERROR) == ["P2"]


def test_p2_unknown_extra_line_is_only_a_warning(config, severity_codes, collected_at, first_poll):
    extra = copy.deepcopy(line(first_poll, "circle"))
    extra["id"] = "new-line"
    issues = run(first_poll + [extra], config, severity_codes, collected_at)
    assert not has_errors(issues)
    assert rules(issues, WARN) == ["P2"]


def test_p3_line_without_statuses_is_error(config, severity_codes, collected_at, first_poll):
    data = copy.deepcopy(first_poll)
    line(data, "dlr")["lineStatuses"] = []
    assert rules(run(data, config, severity_codes, collected_at), ERROR) == ["P3"]


def test_s1_unknown_severity_is_warning_not_error(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "victoria", statusSeverity=99)
    issues = run(data, config, severity_codes, collected_at)
    assert not has_errors(issues)
    assert rules(issues) == ["S1"]


def test_s2_description_mismatch(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "victoria", statusSeverityDescription="Severe Delays")
    assert rules(run(data, config, severity_codes, collected_at)) == ["S2"]


def test_s3_line_id_mismatch_is_error(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "victoria", lineId="central")
    assert rules(run(data, config, severity_codes, collected_at), ERROR) == ["S3"]


def test_s3_missing_line_id_on_good_service_is_fine(config, severity_codes, collected_at, first_poll):
    assert line(first_poll, "circle")["lineStatuses"][0].get("lineId") is None
    assert run(first_poll, config, severity_codes, collected_at) == []


def test_s4_disrupted_without_reason(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "central", reason="")
    assert rules(run(data, config, severity_codes, collected_at)) == ["S4"]


def test_s5_disrupted_without_from_date(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "central", validityPeriods=[])
    assert rules(run(data, config, severity_codes, collected_at)) == ["S5"]


def test_s6_is_now_false(config, severity_codes, collected_at, first_poll):
    data = copy.deepcopy(first_poll)
    line(data, "central")["lineStatuses"][0]["validityPeriods"][0]["isNow"] = False
    assert rules(run(data, config, severity_codes, collected_at)) == ["S6"]


def test_s7_from_date_in_future(config, severity_codes, collected_at, first_poll):
    data = copy.deepcopy(first_poll)
    line(data, "central")["lineStatuses"][0]["validityPeriods"][0]["fromDate"] = "2026-10-22T09:00:00Z"
    assert rules(run(data, config, severity_codes, collected_at)) == ["S7"]


def test_s8_good_service_with_reason(config, severity_codes, collected_at, first_poll):
    data = with_status(first_poll, "circle", reason="Unexpected text")
    assert rules(run(data, config, severity_codes, collected_at)) == ["S8"]
