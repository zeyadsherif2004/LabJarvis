"""The live-scan stability rule, against vectors shared with the browser."""

import json
from pathlib import Path
from typing import Any

import pytest

from labjarvis.domain.live_scan import (
    RULE_VERSION,
    ProbeReading,
    ScanRule,
    ScanStatus,
    evaluate_scan,
)

VECTORS = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "live_scan_vectors.json").read_text(encoding="utf-8")
)
RULE = ScanRule(**VECTORS["rule"])


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["name"])
def test_shared_vectors(case: dict[str, Any]) -> None:
    readings = [ProbeReading(outcome, text) for outcome, text in case["readings"]]
    state = evaluate_scan(readings, elapsed_ms=case["elapsed_ms"], rule=RULE)
    expected = case["expected"]
    assert state.status.value == expected["status"]
    assert state.frames_examined == expected["frames_examined"]
    for key in ("outcome", "normalised", "reason", "prompt"):
        if key in expected:
            assert getattr(state, key) == expected[key]
    if "frame_indexes" in expected:
        assert list(state.frame_indexes) == expected["frame_indexes"]


def test_rule_bounds_and_frame_budget() -> None:
    assert RULE_VERSION == VECTORS["rule_version"] == "stable-asymmetric-v3"
    assert ScanRule(required_consecutive=3, interval_ms=400, timeout_ms=4000).max_frames == 10
    with pytest.raises(ValueError, match="required_consecutive"):
        ScanRule(required_consecutive=1)
    with pytest.raises(ValueError, match="interval_ms"):
        ScanRule(interval_ms=10)
    with pytest.raises(ValueError, match="timeout_ms"):
        ScanRule(timeout_ms=10)


def test_no_state_is_ever_a_verification() -> None:
    """The rule has no 'verified' outcome: stable only selects frames."""

    assert {status.value for status in ScanStatus} == {"scanning", "stable", "timeout"}


def test_submitted_frames_always_share_one_outcome() -> None:
    """The verification afterwards is unanimous, so it must never get a mix."""

    readings = [
        ProbeReading("MATCH", "sodium chloride"),
        ProbeReading("MISMATCH", "sodiuw chloride"),
        ProbeReading("UNREADABLE", ""),
        ProbeReading("MATCH", "sodium chloride"),
        ProbeReading("MATCH", "sodium chloride"),
    ]
    state = evaluate_scan(readings, elapsed_ms=2000, rule=RULE)

    assert state.status is ScanStatus.STABLE
    assert {readings[index].outcome for index in state.frame_indexes} == {"MATCH"}
