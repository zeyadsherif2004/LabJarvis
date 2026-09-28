"""One typed tool registry shared by typed commands, UI actions, and realtime calls.

Every way of asking LabJarvis to do something - a typed command, a button in the
web UI, or a realtime model function call - resolves to an entry in this registry
before the application layer will act on it. That is what keeps the realtime
layer a *shell*: it can only propose names that already exist here, with schemas
that are validated deterministically, and it inherits exactly the same
confirmation policy and legal-state checks as a button press.

The registry is data, not behaviour. Execution lives in
:class:`labjarvis.application.service.LabJarvisService`; this module only states
what may be attempted, in which session states, and whether a human has to
confirm it first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from labjarvis.domain.models import SessionState

TOOL_CONTRACT_VERSION = "tools-v1"

ACTIVE = frozenset({SessionState.ACTIVE})
ACTIVE_OR_PAUSED = frozenset({SessionState.ACTIVE, SessionState.PAUSED})
PAUSED = frozenset({SessionState.PAUSED})
READY = frozenset({SessionState.READY})
INTERACTIVE = frozenset({SessionState.READY, SessionState.ACTIVE, SessionState.PAUSED})


class ToolKind(StrEnum):
    """Whether invoking the tool can change authoritative state."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"


class ConfirmationPolicy(StrEnum):
    """When a human decision is required before a mutation commits."""

    #: Read-only; nothing to confirm.
    NONE = "none"
    #: Always requires an explicit confirmation, whatever proposed it. Used for
    #: anything that writes an experimental record or ends the session.
    ALWAYS = "always"
    #: A model proposal must be confirmed; a direct operator action (a button) is
    #: itself the confirmation and commits immediately. Used for reversible
    #: control actions where a second click adds friction without adding safety.
    MODEL_PROPOSALS_ONLY = "model_proposals_only"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The complete contract for one invocable action."""

    name: str
    version: str
    kind: ToolKind
    confirmation: ConfirmationPolicy
    summary: str
    parameters: Mapping[str, Any]
    legal_session_states: frozenset[SessionState]
    audit_event: str
    idempotent: bool
    success_template: str
    failure_template: str
    #: Legacy intent emitted by the text-understanding adapters. Keeping the
    #: mapping here means the model prompt contract and the tool registry cannot
    #: drift apart silently.
    intent: str | None = None
    #: Exposed to the realtime model. Anything whose replay could be destructive,
    #: or that needs a bounded binary upload, stays off the realtime surface.
    realtime_exposed: bool = True
    required_parameters: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if self.kind is ToolKind.READ_ONLY and self.confirmation is not ConfirmationPolicy.NONE:
            raise ValueError(f"read-only tool {self.name} cannot require confirmation")
        if self.kind is ToolKind.MUTATING and self.confirmation is ConfirmationPolicy.NONE:
            raise ValueError(f"mutating tool {self.name} must declare a confirmation policy")
        unknown = set(self.required_parameters) - set(self.parameters)
        if unknown:
            raise ValueError(f"tool {self.name} requires undeclared parameters: {sorted(unknown)}")

    def requires_confirmation(self, *, model_proposed: bool) -> bool:
        if self.confirmation is ConfirmationPolicy.NONE:
            return False
        if self.confirmation is ConfirmationPolicy.ALWAYS:
            return True
        return model_proposed

    def permits(self, state: SessionState) -> bool:
        return state in self.legal_session_states


def _text(description: str, *, max_length: int = 250) -> dict[str, Any]:
    return {"type": "string", "description": description, "maxLength": max_length}


_SPECS: tuple[ToolSpec, ...] = (
    # ---------------- read-only ----------------
    ToolSpec(
        name="get_protocol_state",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Report the session state, active step number, and progress.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_protocol_state",
        idempotent=True,
        success_template="Reported the current protocol state.",
        failure_template="The protocol state could not be read.",
        intent="GET_PROTOCOL_STATE",
    ),
    ToolSpec(
        name="get_current_step",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Read the active approved protocol step verbatim.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_current_step",
        idempotent=True,
        success_template="Read the current step.",
        failure_template="There is no active protocol step.",
        intent="GET_CURRENT_STEP",
    ),
    ToolSpec(
        name="get_next_step",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Preview the next approved protocol step without advancing.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_next_step",
        idempotent=True,
        success_template="Read the next step.",
        failure_template="There is no later protocol step.",
        intent="GET_NEXT_STEP",
    ),
    ToolSpec(
        name="repeat_instruction",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Repeat the active step instruction.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_repeat_instruction",
        idempotent=True,
        success_template="Repeated the instruction.",
        failure_template="There is no active instruction to repeat.",
        intent="REPEAT_INSTRUCTION",
    ),
    ToolSpec(
        name="get_active_timers",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="List running and paused timers with their remaining time.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_timers",
        idempotent=True,
        success_template="Listed the active timers.",
        failure_template="Timers could not be read.",
        intent="GET_ACTIVE_TIMERS",
    ),
    ToolSpec(
        name="get_expected_reagents",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="List the reagents the active approved step requires.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_expected_reagents",
        idempotent=True,
        success_template="Listed the expected reagents.",
        failure_template="Expected reagents could not be read.",
        intent="GET_EXPECTED_REAGENTS",
    ),
    ToolSpec(
        name="answer_protocol_question",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary=(
            "Answer a question using only the approved protocol, quoting the "
            "passage and its location. Says so plainly when the protocol does "
            "not address the question."
        ),
        parameters={"question": _text("The operator's question, verbatim.", max_length=500)},
        required_parameters=("question",),
        legal_session_states=INTERACTIVE,
        audit_event="tool_answered_protocol_question",
        idempotent=True,
        success_template="Answered from the approved protocol.",
        failure_template="The protocol question could not be answered.",
        intent="ANSWER_PROTOCOL_QUESTION",
    ),
    ToolSpec(
        name="read_last_entry",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Read back the most recent notebook record of this session.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="tool_read_last_entry",
        idempotent=True,
        success_template="Read the last entry.",
        failure_template="The last entry could not be read.",
        intent="READ_LAST_ENTRY",
        realtime_exposed=False,
    ),
    ToolSpec(
        # Silencing an alarm writes nothing to the experimental record. The
        # acknowledgement is kept only as an audit event, which is what lets an
        # alarm survive a reload until someone has actually heard it.
        name="acknowledge_timer_alarm",
        version="1",
        kind=ToolKind.READ_ONLY,
        confirmation=ConfirmationPolicy.NONE,
        summary="Silence the alarm of every elapsed, unacknowledged timer.",
        parameters={},
        legal_session_states=INTERACTIVE,
        audit_event="timer_alarm_acknowledged",
        idempotent=True,
        success_template="Alarm stopped.",
        failure_template="The alarm could not be stopped.",
        intent="ACKNOWLEDGE_TIMER_ALARM",
        realtime_exposed=False,
    ),
    # ---------------- mutating: experimental record ----------------
    ToolSpec(
        name="correct_previous_entry",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary=(
            "Append a correction to the most recent (or named) measurement. The "
            "original entry is preserved and linked. Always confirmed by a human."
        ),
        parameters={
            "entry_id": _text("The measurement entry being corrected.", max_length=64),
            "value": _text("The corrected numeric value.", max_length=40),
            "unit": _text("The corrected unit.", max_length=30),
        },
        required_parameters=("entry_id", "value", "unit"),
        legal_session_states=ACTIVE_OR_PAUSED,
        audit_event="notebook_entry_corrected",
        idempotent=False,
        success_template="Corrected {kind} to {value} {unit}.",
        failure_template="The correction was not recorded.",
        intent="CORRECT_PREVIOUS_ENTRY",
        realtime_exposed=False,
    ),
    ToolSpec(
        name="record_measurement",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="Propose a measurement for the active step. Always confirmed by a human.",
        parameters={
            "kind": _text(
                "What was measured, such as volume, mass, pH, or temperature.",
                max_length=80,
            ),
            "value": _text("The numeric value exactly as stated.", max_length=40),
            "unit": _text("The unit exactly as stated.", max_length=30),
        },
        required_parameters=("kind", "value", "unit"),
        legal_session_states=ACTIVE,
        audit_event="measurement_recorded",
        idempotent=False,
        success_template="Recorded {value} {unit} for {kind}.",
        failure_template="The measurement was not recorded.",
        intent="RECORD_MEASUREMENT",
    ),
    ToolSpec(
        name="record_observation",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="Propose a free-text observation for the active step.",
        parameters={"observation": _text("The observation, verbatim.", max_length=2000)},
        required_parameters=("observation",),
        legal_session_states=ACTIVE,
        audit_event="observation_recorded",
        idempotent=False,
        success_template="Recorded the observation.",
        failure_template="The observation was not recorded.",
        intent="RECORD_OBSERVATION",
    ),
    ToolSpec(
        name="complete_step",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="Mark the active step complete and advance. Always confirmed by a human.",
        parameters={},
        legal_session_states=ACTIVE,
        audit_event="step_completed",
        idempotent=False,
        success_template="Completed the step.",
        failure_template="The step was not completed.",
        intent="COMPLETE_STEP",
    ),
    ToolSpec(
        name="skip_step_with_reason",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="Skip the active step, recording why. Always confirmed by a human.",
        parameters={"reason": _text("Why the step is being skipped.", max_length=500)},
        required_parameters=("reason",),
        legal_session_states=ACTIVE,
        audit_event="step_skipped_with_reason",
        idempotent=False,
        success_template="Skipped the step with a recorded reason.",
        failure_template="The step was not skipped.",
        intent="SKIP_STEP_WITH_REASON",
    ),
    ToolSpec(
        name="verify_reagent",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="Compare a photographed label against one expected reagent requirement.",
        parameters={"requirement_id": _text("Which requirement to verify.", max_length=120)},
        legal_session_states=ACTIVE,
        audit_event="reagent_verification_proposed",
        idempotent=False,
        success_template="Proposed a label comparison for review.",
        failure_template="The label could not be compared.",
        intent="VERIFY_REAGENT",
        # The realtime layer may prompt the operator to use the camera, but the
        # image itself arrives through the bounded upload endpoint, never as a
        # function-call argument.
        realtime_exposed=False,
    ),
    # ---------------- mutating: reversible controls ----------------
    ToolSpec(
        name="set_timer",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Start a durable timer for the active step.",
        parameters={
            "duration_seconds": {
                "type": "integer",
                "description": "Whole seconds, between 1 and 604800.",
                "minimum": 1,
                "maximum": 604800,
            },
            "label": _text("What the timer is for.", max_length=120),
        },
        required_parameters=("duration_seconds",),
        legal_session_states=ACTIVE,
        audit_event="timer_started",
        idempotent=False,
        success_template="Started a {duration_seconds} second timer.",
        failure_template="The timer was not started.",
        intent="SET_TIMER",
    ),
    ToolSpec(
        name="pause_timer",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Pause a running timer, freezing its remaining time.",
        parameters={"timer_id": _text("Which timer to pause.", max_length=36)},
        required_parameters=("timer_id",),
        legal_session_states=ACTIVE_OR_PAUSED,
        audit_event="timer_paused",
        idempotent=True,
        success_template="Paused the timer.",
        failure_template="The timer was not paused.",
        intent="PAUSE_TIMER",
    ),
    ToolSpec(
        name="resume_timer",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Resume a paused timer from its frozen remaining time.",
        parameters={"timer_id": _text("Which timer to resume.", max_length=36)},
        required_parameters=("timer_id",),
        legal_session_states=ACTIVE,
        audit_event="timer_resumed",
        idempotent=True,
        success_template="Resumed the timer.",
        failure_template="The timer was not resumed.",
        intent="RESUME_TIMER",
    ),
    ToolSpec(
        name="cancel_timer",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Cancel a timer without claiming its interval completed.",
        parameters={"timer_id": _text("Which timer to cancel.", max_length=36)},
        required_parameters=("timer_id",),
        legal_session_states=ACTIVE_OR_PAUSED,
        audit_event="timer_cancelled",
        idempotent=True,
        success_template="Cancelled the timer.",
        failure_template="The timer was not cancelled.",
        intent="CANCEL_TIMER",
    ),
    ToolSpec(
        name="start_session",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Start an approved session that has not started yet.",
        parameters={},
        legal_session_states=READY,
        audit_event="session_started",
        idempotent=True,
        success_template="Session started. The first step is active.",
        failure_template="The session was not started.",
        intent="START_SESSION",
        realtime_exposed=False,
    ),
    ToolSpec(
        name="pause_session",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Pause the session.",
        parameters={},
        legal_session_states=ACTIVE,
        audit_event="session_paused",
        idempotent=True,
        success_template="Paused the session.",
        failure_template="The session was not paused.",
        intent="PAUSE_SESSION",
    ),
    ToolSpec(
        name="resume_session",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.MODEL_PROPOSALS_ONLY,
        summary="Resume a paused session.",
        parameters={},
        legal_session_states=PAUSED,
        audit_event="session_resumed",
        idempotent=True,
        success_template="Resumed the session.",
        failure_template="The session was not resumed.",
        intent="RESUME_SESSION",
    ),
    ToolSpec(
        name="end_session",
        version="1",
        kind=ToolKind.MUTATING,
        confirmation=ConfirmationPolicy.ALWAYS,
        summary="End the session early. Terminal and always confirmed by a human.",
        parameters={},
        legal_session_states=ACTIVE_OR_PAUSED,
        audit_event="session_ended_early",
        idempotent=False,
        success_template="Ended the session early.",
        failure_template="The session was not ended.",
        intent="END_SESSION",
    ),
)

TOOLS: Mapping[str, ToolSpec] = MappingProxyType({spec.name: spec for spec in _SPECS})
TOOLS_BY_INTENT: Mapping[str, ToolSpec] = MappingProxyType(
    {spec.intent: spec for spec in _SPECS if spec.intent is not None}
)


def get_tool(name: str) -> ToolSpec | None:
    """Return the tool contract, or ``None`` when the name is not allowlisted."""

    return TOOLS.get(name)


def tool_for_intent(intent: str) -> ToolSpec | None:
    """Resolve a text adapter's intent to its tool contract."""

    return TOOLS_BY_INTENT.get(intent)


def realtime_tool_schemas() -> tuple[dict[str, Any], ...]:
    """Render the realtime-exposed subset as provider function definitions.

    Only tools marked ``realtime_exposed`` appear, so the realtime model cannot
    name an action outside this list. Every name it does produce is re-validated
    against the same spec before anything executes.
    """

    definitions: list[dict[str, Any]] = []
    for spec in _SPECS:
        if not spec.realtime_exposed:
            continue
        definitions.append(
            {
                "type": "function",
                "name": spec.name,
                "description": spec.summary,
                "parameters": {
                    "type": "object",
                    "properties": dict(spec.parameters),
                    "required": list(spec.required_parameters),
                    "additionalProperties": False,
                },
            }
        )
    return tuple(definitions)
