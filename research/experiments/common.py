"""Shared research-harness utilities.

This module is research-only instrumentation. It imports production domain
functions unchanged and implements the ablation condition policies (A, B) that
the production application deliberately does not contain. Nothing here is
imported by the application.
"""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib import metadata
from pathlib import Path
from typing import Any

from labjarvis.domain.commands import UNIT_ALIASES, validate_and_normalise_command
from labjarvis.domain.models import ParsedCommand
from labjarvis.domain.reagent import classify_reagent, normalise_reagent
from labjarvis.settings import Settings

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = RESEARCH_ROOT.parent
RAW_RESULTS = RESEARCH_ROOT / "results" / "raw"


def interpret_command(interpreter: Any, transcript: str, context: Any) -> ParsedCommand:
    """Return the ``ParsedCommand`` from a text interpreter.

    Compatibility shim across the model-port API change in commit d5b7f04:
    ``interpret`` now returns a ``TextResult`` wrapper carrying ``.command``,
    where earlier revisions returned the ``ParsedCommand`` directly. Both are
    handled so the harness reproduces on any repository revision.
    """

    result = interpreter.interpret(transcript, context)
    return result.command if hasattr(result, "command") else result


def verify_production_thresholds() -> None:
    """Fail loudly if the harness constants drift from production settings.

    Guarantees that the ablation conditions use the exact shipped thresholds
    rather than stale copies.
    """

    settings = Settings()
    mismatches = {
        "command_confidence": (
            COMMAND_CONFIDENCE_THRESHOLD,
            settings.command_confidence_threshold,
        ),
        "reagent_match": (REAGENT_MATCH_THRESHOLD, settings.reagent_match_threshold),
        "reagent_uncertain": (
            REAGENT_UNCERTAIN_THRESHOLD,
            settings.reagent_uncertain_threshold,
        ),
    }
    drifted = {k: v for k, v in mismatches.items() if v[0] != v[1]}
    if drifted:
        raise SystemExit(f"harness thresholds drifted from production settings: {drifted}")

SEED = 20260717
COMMAND_CONFIDENCE_THRESHOLD = 0.75  # production default (settings.py)
REAGENT_MATCH_THRESHOLD = 92.0  # production default
REAGENT_UNCERTAIN_THRESHOLD = 72.0  # production default

# Production state gate used by ApplicationService._create_pending_action.
ALLOWED_STATES = {
    "RECORD_MEASUREMENT": {"ACTIVE"},
    "RECORD_OBSERVATION": {"ACTIVE"},
    "PAUSE_SESSION": {"ACTIVE"},
    "RESUME_SESSION": {"PAUSED"},
    "COMPLETE_STEP": {"ACTIVE"},
    "SET_TIMER": {"ACTIVE"},
    "END_SESSION": {"ACTIVE", "PAUSED"},
}
NON_MUTATING_INTENTS = {
    "GET_CURRENT_STEP",
    "GET_NEXT_STEP",
    "REPEAT_INSTRUCTION",
    "HELP",
    "VERIFY_REAGENT",
    "CORRECT_PREVIOUS_ENTRY",
    "UNSUPPORTED",
}


def environment_record() -> dict[str, Any]:
    def version(package: str) -> str:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return "not-installed"

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "not-reported",
        "packages": {
            name: version(name)
            for name in (
                "faster-whisper",
                "ctranslate2",
                "easyocr",
                "torch",
                "numpy",
                "opencv-python-headless",
            )
        },
        "seed": SEED,
        "thresholds": {
            "command_confidence": COMMAND_CONFIDENCE_THRESHOLD,
            "reagent_match": REAGENT_MATCH_THRESHOLD,
            "reagent_uncertain": REAGENT_UNCERTAIN_THRESHOLD,
        },
    }


def load_manifest(name: str) -> list[dict[str, Any]]:
    path = RESEARCH_ROOT / "data" / "manifests" / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_raw(name: str, payload: dict[str, Any]) -> Path:
    RAW_RESULTS.mkdir(parents=True, exist_ok=True)
    path = RAW_RESULTS / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def semantically_equal_parameters(
    intent: str,
    raw: dict[str, str],
    expected: dict[str, str],
) -> bool:
    """Compare raw model-proposed parameters with canonical expected parameters.

    Units are compared through the production alias table and numeric values
    through ``Decimal`` so that condition A is not penalised merely for
    skipping canonicalisation.
    """

    if intent == "RECORD_MEASUREMENT":
        if set(raw) != {"kind", "value", "unit"} or set(expected) != {"kind", "value", "unit"}:
            return False
        kind = " ".join(raw["kind"].split()).casefold()
        unit = UNIT_ALIASES.get(" ".join(raw["unit"].replace("μ", "µ").split()).casefold())
        try:
            value_equal = Decimal(raw["value"]) == Decimal(expected["value"])
        except InvalidOperation:
            return False
        return kind == expected["kind"] and unit == expected["unit"] and value_equal
    if intent == "SET_TIMER":
        return raw.get("duration_seconds", "").lstrip("0") == expected.get(
            "duration_seconds", ""
        ).lstrip("0")
    return {key: " ".join(value.split()) for key, value in raw.items()} == expected


def assess_command(
    intent: str,
    parameters: dict[str, str],
    sample: dict[str, Any],
) -> bool:
    """Return True when executing (intent, parameters) matches the ground truth."""

    if not sample["action_valid"]:
        return False
    if intent != sample["expected_intent"]:
        return False
    return semantically_equal_parameters(intent, parameters, dict(sample["expected_parameters"]))


def condition_outcomes_for_command(
    proposal: ParsedCommand,
    sample: dict[str, Any],
    *,
    session_state: str = "ACTIVE",
) -> dict[str, dict[str, Any]]:
    """Apply condition policies A, B, C to one interpreter proposal.

    Returns per-condition outcome dictionaries with the executed action (if
    any), correctness, and interaction counts. C is reported for both operator
    models (attentive and always-accept).
    """

    canonical = validate_and_normalise_command(proposal)
    outcomes: dict[str, dict[str, Any]] = {}

    # Condition A: execute any non-UNSUPPORTED proposal as proposed.
    a_executes = proposal.intent != "UNSUPPORTED" and proposal.intent not in NON_MUTATING_INTENTS
    outcomes["A"] = {
        "executed": a_executes,
        "intent": proposal.intent,
        "correct": assess_command(proposal.intent, dict(proposal.parameters), sample)
        if a_executes
        else None,
        "confirmations": 0,
    }

    # Condition B: deterministic validation + state gate, then execute.
    b_valid = (
        canonical.intent != "UNSUPPORTED"
        and canonical.intent not in NON_MUTATING_INTENTS
        and session_state in ALLOWED_STATES.get(canonical.intent, set())
    )
    outcomes["B"] = {
        "executed": b_valid,
        "intent": canonical.intent,
        "correct": assess_command(canonical.intent, dict(canonical.parameters), sample)
        if b_valid
        else None,
        "confirmations": 0,
    }

    # Condition C: B plus confidence gate plus human confirmation.
    c_pending = b_valid and canonical.confidence >= COMMAND_CONFIDENCE_THRESHOLD
    correct_if_executed = assess_command(canonical.intent, dict(canonical.parameters), sample)
    outcomes["C_attentive"] = {
        "executed": c_pending and correct_if_executed,
        "intent": canonical.intent,
        "correct": correct_if_executed if (c_pending and correct_if_executed) else None,
        "confirmations": int(c_pending),
    }
    outcomes["C_always_accept"] = {
        "executed": c_pending,
        "intent": canonical.intent,
        "correct": correct_if_executed if c_pending else None,
        "confirmations": int(c_pending),
    }
    for key in ("A", "B", "C_attentive", "C_always_accept"):
        outcome = outcomes[key]
        outcome["unsafe_executed"] = bool(outcome["executed"]) and outcome["correct"] is False
        outcome["missed_valid"] = bool(sample["action_valid"]) and not outcome["executed"]
    return outcomes


def component_outcomes_for_command(
    proposal: ParsedCommand,
    sample: dict[str, Any],
    *,
    session_state: str = "ACTIVE",
) -> dict[str, dict[str, Any]]:
    """Apply one additional command gate at each named condition.

    Unlike the historical A/B/C bundles, these conditions expose the marginal
    change produced by validation, state enforcement, confidence filtering,
    and scripted confirmation while holding the proposal fixed.
    """

    canonical = validate_and_normalise_command(proposal)
    proposal_executes = (
        proposal.intent != "UNSUPPORTED" and proposal.intent not in NON_MUTATING_INTENTS
    )
    validation_executes = (
        canonical.intent != "UNSUPPORTED" and canonical.intent not in NON_MUTATING_INTENTS
    )
    state_executes = validation_executes and session_state in ALLOWED_STATES.get(
        canonical.intent, set()
    )
    confidence_executes = (
        state_executes and canonical.confidence >= COMMAND_CONFIDENCE_THRESHOLD
    )
    correct = assess_command(canonical.intent, dict(canonical.parameters), sample)

    outcomes: dict[str, dict[str, Any]] = {
        "proposal": {
            "executed": proposal_executes,
            "correct": (
                assess_command(proposal.intent, dict(proposal.parameters), sample)
                if proposal_executes
                else None
            ),
            "confirmations": 0,
        },
        "validation": {
            "executed": validation_executes,
            "correct": correct if validation_executes else None,
            "confirmations": 0,
        },
        "state": {
            "executed": state_executes,
            "correct": correct if state_executes else None,
            "confirmations": 0,
        },
        "confidence": {
            "executed": confidence_executes,
            "correct": correct if confidence_executes else None,
            "confirmations": 0,
        },
        "confirm_attentive": {
            "executed": confidence_executes and correct,
            "correct": True if confidence_executes and correct else None,
            "confirmations": int(confidence_executes),
        },
        "confirm_always_accept": {
            "executed": confidence_executes,
            "correct": correct if confidence_executes else None,
            "confirmations": int(confidence_executes),
        },
    }
    for outcome in outcomes.values():
        outcome["unsafe_executed"] = bool(outcome["executed"]) and outcome["correct"] is False
        outcome["missed_valid"] = bool(sample["action_valid"]) and not outcome["executed"]
    return outcomes


def reagent_decisions(observed_text: str, expected_reagent: str) -> dict[str, Any]:
    """Apply the three reagent policies to one OCR reading."""

    observed = normalise_reagent(observed_text)
    expected = normalise_reagent(expected_reagent)
    comparison = classify_reagent(
        observed_text,
        expected_reagent,
        match_threshold=REAGENT_MATCH_THRESHOLD,
        uncertain_threshold=REAGENT_UNCERTAIN_THRESHOLD,
    )
    score = comparison.score
    return {
        "observed_normalised": observed,
        "expected_normalised": expected,
        "score": score,
        "A_accepts": score >= REAGENT_UNCERTAIN_THRESHOLD,
        "B_accepts": score >= REAGENT_MATCH_THRESHOLD,
        "C_outcome": comparison.outcome.value,
    }
