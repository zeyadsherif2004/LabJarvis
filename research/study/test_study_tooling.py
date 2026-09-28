"""Tests for the study tooling. All data here are synthetic test fixtures, not evidence."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from labjarvis.infrastructure.study_mode import StudyScript
from research.study.analyze_study import analyse, markdown, raw_tlx, sus_score, wilson
from research.study.extract_trials import trials_from_export
from research.study.generate_sessions import plan, write


def test_wilson_interval_known_values() -> None:
    low, high = wilson(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(3, 3)[1] == pytest.approx(1.0)


def test_sus_and_raw_tlx_scoring() -> None:
    neutral = {f"sus_{i}": "3" for i in range(1, 11)}
    best = {f"sus_{i}": ("5" if i % 2 else "1") for i in range(1, 11)}
    assert sus_score(neutral) == 50.0
    assert sus_score(best) == 100.0
    tlx = dict.fromkeys(
        ["tlx_mental", "tlx_physical", "tlx_temporal", "tlx_performance", "tlx_effort", "tlx_frustration"],
        "30",
    )
    assert raw_tlx(tlx) == 30.0
    with pytest.raises(ValueError):
        sus_score({**neutral, "sus_1": "9"})


def test_sessions_are_counterbalanced_and_scripts_are_valid(tmp_path: Path) -> None:
    allocation = plan(6, trials=10, seeded=3, seed=7, study_id="unit")
    orders = [tuple(person["order"]) for person in allocation["allocation"]]
    assert orders.count(("tap", "readback")) == orders.count(("readback", "tap")) == 3
    for person in allocation["allocation"]:
        for block in person["blocks"]:
            seeded = [i for i in block["injections"] if i["kind"] == "measurement_value"]
            assert len(seeded) == 3
            assert all(i["trial"] >= 2 for i in seeded)
    write(allocation, tmp_path)
    for script in tmp_path.glob("P*-block*.json"):
        StudyScript.model_validate(json.loads(script.read_text(encoding="utf-8")))
    assert "deliberately be wrong" in (tmp_path / "session-sheets.md").read_text(encoding="utf-8")


def test_same_seed_gives_the_same_plan() -> None:
    assert plan(4, trials=10, seeded=2, seed=1, study_id="x") == plan(
        4, trials=10, seeded=2, seed=1, study_id="x"
    )


def _synthetic_export() -> dict[str, object]:
    inference = lambda i, injected: {  # noqa: E731
        "id": f"inf-{i}",
        "output": {
            "study_id": "unit",
            "study_participant": "P01",
            "study_condition": "tap",
            "study_block": "1",
            "study_trial": str(i),
            "study_injected": "true" if injected else "false",
        },
    }
    action = lambda i, status: {  # noqa: E731
        "id": f"a{i}",
        "intent": "RECORD_MEASUREMENT",
        "status": status,
        "created_at": "2026-09-26T10:00:00+00:00",
        "resolved_at": "2026-09-26T10:00:03.500000+00:00",
        "model_inference_id": f"inf-{i}",
        "source": "USER_CONFIRMED_SPEECH",
    }
    return {
        "inferences": [inference(1, False), inference(2, True), inference(3, False)],
        "pending_actions": [action(1, "CONFIRMED"), action(2, "REJECTED"), action(3, "REJECTED")],
    }


def test_trials_are_extracted_with_decision_and_timing() -> None:
    rows = trials_from_export(_synthetic_export())

    assert [row["trial"] for row in rows] == [1, 2, 3]
    assert rows[1]["injected"] is True and rows[1]["decision"] == "rejected"
    assert rows[0]["confirmation_ms"] == 3500


def test_analysis_is_descriptive_and_counts_detections(tmp_path: Path) -> None:
    rows = trials_from_export(_synthetic_export())
    with (tmp_path / "trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = analyse(tmp_path)

    tap = summary["conditions"]["tap"]
    assert tap["seeded_detected"] == 1 and tap["seeded_errors"] == 1
    assert tap["false_rejections"] == 1 and tap["genuine_trials"] == 2
    assert "no significance tests" in summary["analysis"]
    assert "Seeded errors caught" in markdown(summary)
