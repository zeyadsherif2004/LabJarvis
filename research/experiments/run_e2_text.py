"""E2: deterministic rule-grammar interpretation + condition policies on text commands.

The interpreter under test is the shipped deterministic rule grammar
(``FakeTextUnderstanding``). Its results are evidence about a rule-based
interpreter, never about an LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    condition_outcomes_for_command,
    environment_record,
    interpret_command,
    load_manifest,
    verify_production_thresholds,
    write_raw,
)

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.evaluation.metrics import slot_counts
from labjarvis.infrastructure.fake_models import FakeTextUnderstanding
from labjarvis.ports.models import CommandContext


def main() -> None:
    verify_production_thresholds()
    interpreter = FakeTextUnderstanding()
    samples = load_manifest("text-commands.jsonl")
    records = []
    for sample in samples:
        context = CommandContext(
            session_id="research-session",
            session_state=sample.get("session_state", "ACTIVE"),
            active_step_id="research-step",
            active_instruction="Add 250 mL of buffer to the flask.",
        )
        started = perf_counter()
        proposal = interpret_command(interpreter, sample["transcript"], context)
        latency_ms = round((perf_counter() - started) * 1000, 3)
        canonical = validate_and_normalise_command(proposal)
        tp, fp, fn = slot_counts(sample["expected_parameters"], dict(canonical.parameters))
        records.append(
            {
                "sample_id": sample["id"],
                "category": sample["category"],
                "transcript": sample["transcript"],
                "expected_intent": sample["expected_intent"],
                "action_valid": sample["action_valid"],
                "proposal": {
                    "intent": proposal.intent,
                    "parameters": dict(proposal.parameters),
                    "confidence": proposal.confidence,
                    "ambiguities": list(proposal.ambiguities),
                },
                "canonical": {
                    "intent": canonical.intent,
                    "parameters": dict(canonical.parameters),
                },
                "intent_correct": int(canonical.intent == sample["expected_intent"]),
                "slot_tp": tp,
                "slot_fp": fp,
                "slot_fn": fn,
                "latency_ms": latency_ms,
                "conditions": condition_outcomes_for_command(
                    proposal, sample, session_state=sample.get("session_state", "ACTIVE")
                ),
            }
        )
    payload = {
        "experiment_id": "E2-text",
        "interpreter": {
            "adapter": interpreter.adapter_name,
            "model_name": interpreter.model_name,
            "note": "deterministic rule grammar; not an LLM",
        },
        "environment": environment_record(),
        "sample_count": len(records),
        "records": records,
    }
    path = write_raw("e2-text-results.json", payload)
    print(path)


if __name__ == "__main__":
    main()
