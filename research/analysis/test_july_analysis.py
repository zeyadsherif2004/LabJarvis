import csv
import json
from pathlib import Path


RESEARCH = Path(__file__).resolve().parents[1]
PROCESSED = RESEARCH / "results" / "processed"


def _csv_by_key(filename: str, key: str) -> dict[str, dict[str, str]]:
    with (PROCESSED / filename).open(encoding="utf-8", newline="") as handle:
        return {row[key]: row for row in csv.DictReader(handle)}


def test_posthoc_threshold_matches_policy_c_acceptance_set() -> None:
    conditions = _csv_by_key("e3-condition-summary.csv", "policy")
    selective = _csv_by_key("e3-selective-accounting.csv", "policy")
    stats = json.loads((PROCESSED / "stats-tests.json").read_text(encoding="utf-8"))

    for field in ("false_accepts", "true_accepts"):
        assert conditions["C"][field] == conditions["T93.5"][field]

    comparator = stats["e3"]["posthoc_threshold_comparator"]
    assert comparator["threshold"] == 93.5
    assert comparator["same_acceptance_set_as_C"] is True
    assert comparator["acceptance_set_disagreements"] == 0
    assert comparator["selected_after_inspecting_the_sweep"] is True

    assert selective["C"]["abstain"] == "6"
    assert selective["T93.5"]["abstain"] == "0"
    assert selective["C"]["coverage"] == "0.9333"
    assert selective["T93.5"]["coverage"] == "1.0000"


def test_e4_uses_predeclared_safety_outcome() -> None:
    stats = json.loads((PROCESSED / "stats-tests.json").read_text(encoding="utf-8"))
    e4 = stats["e4"]

    assert e4["primary_outcome"] == "scenario_with_one_or_more_unsafe_events"
    assert all(e4["task_failure_matches_any_unsafe_event"].values())
    assert {
        contrast["outcome"] for contrast in e4["contrasts"].values()
    } == {"scenario_with_one_or_more_unsafe_events"}


def test_command_confidence_gate_changes_no_reported_outcome() -> None:
    stats = json.loads((PROCESSED / "stats-tests.json").read_text(encoding="utf-8"))
    gate = stats["e2"]["command_confidence_gate"]

    assert gate["threshold"] == 0.75
    assert gate["value_counts"] == {"0.0": 28, "0.99": 53}
    assert gate["below_threshold"] == 28
    assert gate["below_threshold_after_validation_and_state"] == 0
    assert gate["newly_rejected_by_gate"] == 0
    assert gate["B_vs_C_always_accept_execution_disagreements"] == 0

    speech = stats["e1"]["speech_confidence"]
    assert speech["below_command_threshold_0.75"] == 75
    assert speech["used_by_command_gate"] is False
    assert stats["e1"]["command_path_B_vs_C_always_accept_execution_disagreements"] == 0
