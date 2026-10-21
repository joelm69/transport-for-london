import copy

from src.deduplication.line_status import detect_changes, flatten_statuses
from tests.unit.helpers import line, with_status

POLL_1 = {"poll_id": "p1", "collected_at_utc": "2026-10-21T17:29:46+00:00", "collection_window": "test"}
POLL_2 = {"poll_id": "p2", "collected_at_utc": "2026-10-21T17:35:39+00:00", "collection_window": "test"}


def flat(data, poll, config):
    return flatten_statuses(data, poll, config["severity_classes"])


def baseline(data, config):
    _, state = detect_changes({}, flat(data, POLL_1, config), is_baseline=True)
    return state


def changes_between(before, after, config):
    changes, _ = detect_changes(baseline(before, config), flat(after, POLL_2, config), is_baseline=False)
    return changes


def by_line(changes):
    return {c["line_id"]: c for c in changes}


def test_baseline_emits_every_status(config, first_poll):
    changes, state = detect_changes({}, flat(first_poll, POLL_1, config), is_baseline=True)
    assert len(changes) == 20
    assert {c["change_type"] for c in changes} == {"BASELINE"}
    assert set(state) == set(config["expected_line_ids"])


def test_live_polls_detect_only_the_elizabeth_line(config, first_poll, second_poll):
    """Real data: between 17:29 and 17:35 only the Elizabeth line changed.
    Bakerloo and tram toDate moved (rolling placeholder) but must NOT count."""
    changes = changes_between(first_poll, second_poll, config)

    assert len(changes) == 1
    change = changes[0]
    assert change["line_id"] == "elizabeth"
    assert change["change_type"] == "NEW_INCIDENT"
    assert change["severity_description"] == "Severe Delays"
    assert change["event_from_utc"] == "2026-10-21T17:32:38+00:00"
    assert change["incident_key"] is not None


def test_same_incident_new_text_is_update(config, first_poll):
    after = with_status(first_poll, "central", reason="Central Line: Severe delays. Tickets accepted on buses.")
    change = by_line(changes_between(first_poll, after, config))["central"]
    before_key = by_line(flat(first_poll, POLL_1, config))["central"]["incident_key"]

    assert change["change_type"] == "UPDATE"
    assert change["incident_key"] == before_key


def test_escalation_same_from_date_is_update(config, first_poll):
    after = with_status(first_poll, "victoria", statusSeverity=6, statusSeverityDescription="Severe Delays")
    assert by_line(changes_between(first_poll, after, config))["victoria"]["change_type"] == "UPDATE"


def test_new_cause_same_severity_is_new_incident(config, first_poll):
    """The merge problem from step 2: same severity, different incident."""
    after = copy.deepcopy(first_poll)
    status = line(after, "central")["lineStatuses"][0]
    status["reason"] = "Central Line: Severe delays due to a person ill on a train at Bank."
    status["validityPeriods"][0]["fromDate"] = "2026-10-21T17:34:00Z"

    change = by_line(changes_between(first_poll, after, config))["central"]
    assert change["change_type"] == "NEW_INCIDENT"
    assert change["resolved_incident_keys"]  # the signal failure ended


def test_return_to_good_service_is_resolved(config, first_poll):
    good = copy.deepcopy(line(first_poll, "circle")["lineStatuses"][0])
    after = copy.deepcopy(first_poll)
    line(after, "victoria")["lineStatuses"] = [good]

    change = by_line(changes_between(first_poll, after, config))["victoria"]
    old_key = by_line(flat(first_poll, POLL_1, config))["victoria"]["incident_key"]

    assert change["change_type"] == "RESOLVED"
    assert change["severity_class"] == "good"
    assert change["resolved_incident_keys"] == [old_key]


def test_two_statuses_one_changes_emits_full_line_state(config, first_poll):
    before = copy.deepcopy(first_poll)
    second = copy.deepcopy(line(first_poll, "victoria")["lineStatuses"][0])
    second["reason"] = "Victoria Line: Part closure at Brixton."
    second["validityPeriods"][0]["fromDate"] = "2026-10-21T10:00:00Z"
    line(before, "victoria")["lineStatuses"].append(second)

    after = copy.deepcopy(before)
    line(after, "victoria")["lineStatuses"][0]["reason"] = "Victoria Line: Minor delays, updated."

    victoria = [c for c in changes_between(before, after, config) if c["line_id"] == "victoria"]
    assert sorted(c["change_type"] for c in victoria) == ["ONGOING", "UPDATE"]


def test_no_from_date_gives_null_incident_key(config, first_poll):
    data = with_status(first_poll, "central", validityPeriods=[])
    assert by_line(flat(data, POLL_1, config))["central"]["incident_key"] is None


def test_service_closed_is_its_own_class(config, first_poll):
    data = with_status(first_poll, "waterloo-city", statusSeverity=20,
                       statusSeverityDescription="Service Closed")
    assert by_line(flat(data, POLL_1, config))["waterloo-city"]["severity_class"] == "closed"


def test_processing_is_deterministic(config, first_poll, second_poll):
    """Check C2: same input, same output."""
    assert changes_between(first_poll, second_poll, config) == changes_between(first_poll, second_poll, config)


def test_unchanged_poll_emits_nothing(config, first_poll):
    assert changes_between(first_poll, copy.deepcopy(first_poll), config) == []
