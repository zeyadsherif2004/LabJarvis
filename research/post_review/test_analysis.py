from __future__ import annotations

import math

from research.post_review.analyze import (
    holm,
    interpreter_summary,
    mcnemar_exact,
    wilson,
)


def test_wilson_interval_matches_the_original_analysis_implementation() -> None:
    estimate, low, high = wilson(8, 81)

    assert math.isclose(estimate, 8 / 81)
    assert math.isclose(low, 0.0509, abs_tol=0.0001)
    assert math.isclose(high, 0.1830, abs_tol=0.0001)


def test_mcnemar_exact_returns_two_sided_binomial_p_value() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(1, 0) == 1.0
    assert mcnemar_exact(2, 0) == 0.5
    assert mcnemar_exact(3, 0) == 0.25


def test_holm_adjustment_preserves_monotonicity() -> None:
    adjusted = holm({"a": 0.01, "b": 0.04, "c": 0.20})

    assert adjusted["a"] <= adjusted["b"] <= adjusted["c"]
    assert adjusted["c"] == 0.20


def test_interpreter_summary_preserves_failures_and_counts_components() -> None:
    candidate = {
        "adapter": "test-adapter",
        "model_name": "test-model",
        "sample_count": 2,
        "failure_count": 1,
        "metrics": {
            "intent_accuracy": 0.5,
            "exact_command_accuracy": 0.5,
            "slot_precision": 1.0,
            "slot_recall": 1.0,
            "slot_f1": 1.0,
            "mean_latency_ms": 20.0,
        },
        "component_summary": {
            "proposal": {
                "executed": 2,
                "unsafe_executed": 1,
                "missed_valid": 0,
                "confirmations": 0,
            },
            "validation": {
                "executed": 1,
                "unsafe_executed": 0,
                "missed_valid": 1,
                "confirmations": 0,
            },
        },
        "records": [
            {
                "sample_id": "text-1",
                "status": "failed",
                "latency_ms": 10,
            },
            {
                "status": "succeeded",
                "latency_ms": 30,
                "components": {
                    "proposal": {
                        "executed": True,
                        "unsafe_executed": True,
                        "missed_valid": False,
                        "confirmations": 0,
                    },
                    "validation": {
                        "executed": True,
                        "unsafe_executed": False,
                        "missed_valid": True,
                        "confirmations": 0,
                    },
                },
            },
        ],
    }

    summary = interpreter_summary(
        candidate,
        "test-model",
        sample_by_id={"text-1": {"action_valid": False}},
    )

    assert summary["schema_valid_rate"] == 0.5
    assert summary["latency_ms_p50"] == 30
    assert summary["latency_ms_p95"] == 30
    assert summary["component_summary"]["proposal"]["unsafe_executed"] == 1
    assert summary["component_summary"]["validation"]["missed_valid"] == 1
    assert summary["component_summary"]["proposal"]["missed_valid"] == 0
