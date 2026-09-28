"""E4: end-to-end orchestration scenarios over the real domain state machine.

Conditions share identical interpreter proposals per event. Condition A
executes proposals without deterministic gating (illegal transitions are
counted and forced, emulating an unconstrained executor); conditions B and C
use the production transition functions, which raise ``DomainError`` for
illegal transitions. Condition C adds the confidence gate and human
confirmation under two operator models (attentive, always-accept).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    ALLOWED_STATES,
    COMMAND_CONFIDENCE_THRESHOLD,
    RESEARCH_ROOT,
    assess_command,
    environment_record,
    interpret_command,
    reagent_decisions,
    verify_production_thresholds,
    write_raw,
)

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.models import (
    DomainError,
    LabSession,
    SessionState,
    SessionStep,
    StepState,
)
from labjarvis.domain.state_machine import (
    ALLOWED_SESSION_TRANSITIONS,
    complete_active_step,
    transition_session,
)
from labjarvis.infrastructure.fake_models import FakeTextUnderstanding
from labjarvis.ports.models import CommandContext

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
CONDITIONS = ("A", "B", "C_attentive", "C_always_accept")


def new_session(step_count: int) -> LabSession:
    steps = tuple(
        SessionStep(step_id=f"step-{index}", state=StepState.NOT_STARTED)
        for index in range(1, step_count + 1)
    )
    session = LabSession(
        id="research-scenario",
        protocol_version_id="research-protocol-v1",
        protocol_approved=True,
        state=SessionState.READY,
        steps=steps,
        active_step_id=None,
        version=0,
        created_at=NOW,
    )
    return transition_session(session, SessionState.ACTIVE, at=NOW)


INTENT_TARGET_STATE = {
    "PAUSE_SESSION": SessionState.PAUSED,
    "RESUME_SESSION": SessionState.ACTIVE,
    "END_SESSION": SessionState.ABORTED,
}


class ConditionRun:
    def __init__(self, name: str, step_count: int) -> None:
        self.name = name
        self.session = new_session(step_count)
        self.correct_records = 0
        self.incorrect_records = 0
        self.invalid_transitions_executed = 0
        self.blocked_events = 0
        self.confirmations = 0
        self.label_false_accepts = 0
        self.label_abstentions = 0
        self.label_correct = 0
        self.missed_valid = 0

    def apply_state_intent(self, intent: str, *, forced: bool) -> None:
        if intent == "COMPLETE_STEP":
            if self.session.state is SessionState.ACTIVE and self.session.active_step_id:
                self.session = complete_active_step(self.session, at=NOW)
            elif forced:
                self.invalid_transitions_executed += 1
                # An unconstrained executor marks the step done regardless.
                if self.session.active_step_id:
                    steps = tuple(
                        replace(step, state=StepState.COMPLETED)
                        if step.step_id == self.session.active_step_id
                        else step
                        for step in self.session.steps
                    )
                    self.session = replace(self.session, steps=steps, active_step_id=None)
            else:
                raise DomainError("complete not allowed")
            return
        target = INTENT_TARGET_STATE.get(intent)
        if target is None:
            return
        if target in ALLOWED_SESSION_TRANSITIONS[self.session.state]:
            self.session = transition_session(self.session, target, at=NOW)
        elif forced:
            self.invalid_transitions_executed += 1
            self.session = replace(self.session, state=target)
        else:
            raise DomainError("transition not allowed")


def process_command(run: ConditionRun, event: dict, proposal, canonical) -> None:
    condition = run.name
    if condition == "A":
        executes = proposal.intent not in {
            "UNSUPPORTED", "GET_CURRENT_STEP", "GET_NEXT_STEP", "REPEAT_INSTRUCTION",
            "HELP", "VERIFY_REAGENT", "CORRECT_PREVIOUS_ENTRY",
        }
        if not executes:
            if event["action_valid"]:
                run.missed_valid += 1
            return
        correct = assess_command(proposal.intent, dict(proposal.parameters), event)
        if proposal.intent in ("RECORD_MEASUREMENT", "RECORD_OBSERVATION", "SET_TIMER"):
            if run.session.state is not SessionState.ACTIVE:
                run.invalid_transitions_executed += 1
            if correct:
                run.correct_records += 1
            else:
                run.incorrect_records += 1
        else:
            run.apply_state_intent(proposal.intent, forced=True)
            if not correct and event["action_valid"] is False:
                pass  # invalid transition already counted when forced
        return

    # B and C share deterministic validation and the production state gate.
    executable = (
        canonical.intent not in {
            "UNSUPPORTED", "GET_CURRENT_STEP", "GET_NEXT_STEP", "REPEAT_INSTRUCTION",
            "HELP", "VERIFY_REAGENT", "CORRECT_PREVIOUS_ENTRY",
        }
        and run.session.state.value in ALLOWED_STATES.get(canonical.intent, set())
    )
    if condition.startswith("C"):
        executable = executable and canonical.confidence >= COMMAND_CONFIDENCE_THRESHOLD
    if not executable:
        run.blocked_events += 1
        if event["action_valid"]:
            run.missed_valid += 1
        return
    correct = assess_command(canonical.intent, dict(canonical.parameters), event)
    if condition == "C_attentive":
        run.confirmations += 1
        if not correct:
            return  # operator rejects the incorrect proposal
    elif condition == "C_always_accept":
        run.confirmations += 1
    if canonical.intent in ("RECORD_MEASUREMENT", "RECORD_OBSERVATION", "SET_TIMER"):
        if correct:
            run.correct_records += 1
        else:
            run.incorrect_records += 1
    else:
        try:
            run.apply_state_intent(canonical.intent, forced=False)
        except DomainError:
            run.blocked_events += 1


def process_label(run: ConditionRun, event: dict) -> None:
    decisions = reagent_decisions(event["observed_text"], event["expected_reagent"])
    truth = event["truth"]
    if run.name == "A":
        accepted = decisions["A_accepts"]
    elif run.name == "B":
        accepted = decisions["B_accepts"]
    else:
        outcome = decisions["C_outcome"]
        if outcome == "UNCERTAIN":
            run.label_abstentions += 1
            run.confirmations += 1
            accepted = truth == "match" if run.name == "C_attentive" else True
        else:
            run.confirmations += 1
            accepted = outcome == "MATCH"
    if accepted and truth != "match":
        run.label_false_accepts += 1
    elif accepted == (truth == "match"):
        run.label_correct += 1


def main() -> None:
    verify_production_thresholds()
    scenarios = json.loads(
        (RESEARCH_ROOT / "data" / "manifests" / "scenarios.json").read_text(encoding="utf-8")
    )
    interpreter = FakeTextUnderstanding()
    results = []
    for scenario in scenarios:
        per_condition: dict[str, dict[str, object]] = {}
        for condition in CONDITIONS:
            run = ConditionRun(condition, scenario["steps"])
            for event in scenario["events"]:
                if event["type"] == "label":
                    process_label(run, event)
                    continue
                context = CommandContext(
                    session_id="research-scenario",
                    session_state=run.session.state.value,
                    active_step_id=run.session.active_step_id,
                    active_instruction="Follow the reviewed step.",
                )
                proposal = interpret_command(interpreter, event["transcript"], context)
                canonical = validate_and_normalise_command(proposal)
                process_command(run, event, proposal, canonical)
            expected_records = sum(
                1
                for event in scenario["events"]
                if event["type"] == "command"
                and event["action_valid"]
                and event["expected_intent"]
                in ("RECORD_MEASUREMENT", "RECORD_OBSERVATION", "SET_TIMER")
            )
            unsafe = (
                run.incorrect_records
                + run.invalid_transitions_executed
                + run.label_false_accepts
            )
            per_condition[condition] = {
                "final_state": run.session.state.value,
                "final_state_correct": run.session.state.value
                == scenario["expected_final_state"],
                "correct_records": run.correct_records,
                "expected_records": expected_records,
                "incorrect_records": run.incorrect_records,
                "invalid_transitions_executed": run.invalid_transitions_executed,
                "label_false_accepts": run.label_false_accepts,
                "label_abstentions": run.label_abstentions,
                "blocked_events": run.blocked_events,
                "confirmations": run.confirmations,
                "missed_valid": run.missed_valid,
                "unsafe_events": unsafe,
                "task_success": (
                    run.session.state.value == scenario["expected_final_state"]
                    and unsafe == 0
                    and run.correct_records == expected_records
                ),
            }
        results.append(
            {
                "scenario_id": scenario["id"],
                "family": scenario["family"],
                "conditions": per_condition,
            }
        )
    payload = {
        "experiment_id": "E4-scenarios",
        "environment": environment_record(),
        "scenario_count": len(results),
        "records": results,
    }
    path = write_raw("e4-scenario-results.json", payload)
    print(path)


if __name__ == "__main__":
    main()
