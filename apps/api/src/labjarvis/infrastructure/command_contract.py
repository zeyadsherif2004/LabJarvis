"""One intent-discriminated command contract shared by every language-model adapter.

Version 1 of this contract let ``parameters`` take any of four shapes whatever the
``intent`` was, and asked the model for a free-form ``confidence``. A 1.5B local
model then produced the right intent with the wrong parameter shape
(``RECORD_MEASUREMENT`` with ``{"observation": "temperature"}``) and a
``confidence`` of 100, and failed every valid measurement on the benchmark.

Version 2 makes the intent decide the exact legal fields:

* each intent is its own branch with the intent as a constant, only that
  intent's fields, every field required and nothing else allowed;
* units are an enum of the canonical units the validator accepts; timer
  durations are bounded integers; measurement values are decimal strings;
* ``UNSUPPORTED`` carries a closed reason (out of scope, several commands,
  incomplete, ambiguous) instead of free text;
* **there is no confidence field.** Confidence is derived afterwards from
  observable evidence (``labjarvis.domain.entity_grounding``), never taken from
  the model.

Grammar-constrained decoding makes illegal combinations impossible to generate
where a provider supports it. It does not replace validation: every output is
still parsed against the same Pydantic discriminated union here and then passes
``validate_and_normalise_command`` and the state gates inside ``LabJarvisService``.

Version 3 (hands-free) adds routing branches for the hands-free intents. Most
are parameterless on purpose: which timer to pause, which reagent to check and
which value a correction writes are parsed only by the deterministic grammar,
so the model may route an utterance to ``PAUSE_TIMER`` or
``CORRECT_PREVIOUS_ENTRY`` but can never supply the value. The two new
branches with a field (``SKIP_STEP_WITH_REASON.reason`` and
``ANSWER_PROTOCOL_QUESTION.question``) are grounded against the utterance, and
a question is only ever *routed*: the answer comes from deterministic
retrieval over the approved protocol, never from the model.

This module performs no I/O and no domain mutation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from labjarvis.domain.entity_grounding import (
    GROUNDED_MODEL_PROPOSAL,
    NO_EVIDENCE,
    ground_command,
)
from labjarvis.domain.models import ParsedCommand
from labjarvis.ports.models import CommandContext

CONTRACT_VERSION = "command-contract-v3"
MAX_UNTRUSTED_CHARACTERS = 4000
MAX_TIMER_SECONDS = 604_800

MEASUREMENT_UNITS: tuple[str, ...] = (
    "uL",
    "mL",
    "L",
    "mg",
    "g",
    "kg",
    "degC",
    "K",
    "s",
    "min",
    "h",
    "rpm",
    "pH",
    "%",
)
UNSUPPORTED_REASONS: tuple[str, ...] = (
    "out_of_scope",
    "multiple_commands",
    "incomplete",
    "ambiguous",
)
PARAMETERLESS_INTENTS: tuple[str, ...] = (
    "GET_CURRENT_STEP",
    "GET_NEXT_STEP",
    "REPEAT_INSTRUCTION",
    "VERIFY_REAGENT",
    "CORRECT_PREVIOUS_ENTRY",
    "PAUSE_SESSION",
    "RESUME_SESSION",
    "COMPLETE_STEP",
    "END_SESSION",
    "HELP",
    "GET_PROTOCOL_STATE",
    "GET_EXPECTED_REAGENTS",
    "GET_ACTIVE_TIMERS",
    "READ_LAST_ENTRY",
    "PAUSE_TIMER",
    "RESUME_TIMER",
    "CANCEL_TIMER",
    "START_SESSION",
)
_DECIMAL_PATTERN = r"^-?[0-9]{1,12}(\.[0-9]{1,6})?$"

#: Human-readable reasons recorded as ambiguities for UNSUPPORTED proposals.
UNSUPPORTED_REASON_TEXT = {
    "out_of_scope": "request is outside the supported commands",
    "multiple_commands": "multiple commands",
    "incomplete": "command is incomplete",
    "ambiguous": "command is ambiguous",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GetCurrentStep(_Strict):
    intent: Literal["GET_CURRENT_STEP"]


class GetNextStep(_Strict):
    intent: Literal["GET_NEXT_STEP"]


class RepeatInstruction(_Strict):
    intent: Literal["REPEAT_INSTRUCTION"]


class VerifyReagent(_Strict):
    intent: Literal["VERIFY_REAGENT"]


class CorrectPreviousEntry(_Strict):
    intent: Literal["CORRECT_PREVIOUS_ENTRY"]


class PauseSession(_Strict):
    intent: Literal["PAUSE_SESSION"]


class ResumeSession(_Strict):
    intent: Literal["RESUME_SESSION"]


class CompleteStep(_Strict):
    intent: Literal["COMPLETE_STEP"]


class EndSession(_Strict):
    intent: Literal["END_SESSION"]


class Help(_Strict):
    intent: Literal["HELP"]


class GetProtocolState(_Strict):
    intent: Literal["GET_PROTOCOL_STATE"]


class GetExpectedReagents(_Strict):
    intent: Literal["GET_EXPECTED_REAGENTS"]


class GetActiveTimers(_Strict):
    intent: Literal["GET_ACTIVE_TIMERS"]


class ReadLastEntry(_Strict):
    intent: Literal["READ_LAST_ENTRY"]


class PauseTimer(_Strict):
    intent: Literal["PAUSE_TIMER"]


class ResumeTimer(_Strict):
    intent: Literal["RESUME_TIMER"]


class CancelTimer(_Strict):
    intent: Literal["CANCEL_TIMER"]


class StartSession(_Strict):
    intent: Literal["START_SESSION"]


class SkipStepWithReason(_Strict):
    intent: Literal["SKIP_STEP_WITH_REASON"]
    reason: str = Field(min_length=1, max_length=500)


class AnswerProtocolQuestion(_Strict):
    intent: Literal["ANSWER_PROTOCOL_QUESTION"]
    question: str = Field(min_length=1, max_length=500)


class RecordMeasurement(_Strict):
    intent: Literal["RECORD_MEASUREMENT"]
    kind: str = Field(min_length=1, max_length=60)
    value: str = Field(pattern=_DECIMAL_PATTERN)
    unit: Literal[
        "uL", "mL", "L", "mg", "g", "kg", "degC", "K", "s", "min", "h", "rpm", "pH", "%"
    ]


class RecordObservation(_Strict):
    intent: Literal["RECORD_OBSERVATION"]
    observation: str = Field(min_length=1, max_length=500)


class SetTimer(_Strict):
    intent: Literal["SET_TIMER"]
    duration_seconds: int = Field(ge=1, le=MAX_TIMER_SECONDS)


class Unsupported(_Strict):
    intent: Literal["UNSUPPORTED"]
    reason: Literal["out_of_scope", "multiple_commands", "incomplete", "ambiguous"]


ModelCommand = Annotated[
    GetCurrentStep
    | GetNextStep
    | RepeatInstruction
    | VerifyReagent
    | CorrectPreviousEntry
    | PauseSession
    | ResumeSession
    | CompleteStep
    | EndSession
    | Help
    | GetProtocolState
    | GetExpectedReagents
    | GetActiveTimers
    | ReadLastEntry
    | PauseTimer
    | ResumeTimer
    | CancelTimer
    | StartSession
    | SkipStepWithReason
    | AnswerProtocolQuestion
    | RecordMeasurement
    | RecordObservation
    | SetTimer
    | Unsupported,
    Field(discriminator="intent"),
]
_COMMAND_ADAPTER: TypeAdapter[ModelCommand] = TypeAdapter(ModelCommand)


def _branch(
    intent: str,
    fields: dict[str, dict[str, Any]],
    *,
    use_const: bool,
    length_keywords: bool,
) -> dict[str, Any]:
    intent_schema: dict[str, Any] = {"const": intent} if use_const else {"enum": [intent]}
    intent_schema["type"] = "string"
    properties: dict[str, Any] = {"intent": intent_schema}
    for name, schema in fields.items():
        cleaned = dict(schema)
        if not length_keywords:
            cleaned.pop("minLength", None)
            cleaned.pop("maxLength", None)
        properties[name] = cleaned
    return {
        "type": "object",
        "properties": properties,
        "required": ["intent", *fields],
        "additionalProperties": False,
    }


def _branches(*, use_const: bool, length_keywords: bool) -> list[dict[str, Any]]:
    branches = [
        _branch(intent, {}, use_const=use_const, length_keywords=length_keywords)
        for intent in PARAMETERLESS_INTENTS
    ]
    branches.append(
        _branch(
            "RECORD_MEASUREMENT",
            {
                "kind": {"type": "string", "minLength": 1, "maxLength": 60},
                "value": {"type": "string", "pattern": _DECIMAL_PATTERN},
                "unit": {"type": "string", "enum": list(MEASUREMENT_UNITS)},
            },
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    branches.append(
        _branch(
            "RECORD_OBSERVATION",
            {"observation": {"type": "string", "minLength": 1, "maxLength": 500}},
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    branches.append(
        _branch(
            "SET_TIMER",
            {
                "duration_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMER_SECONDS,
                }
            },
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    branches.append(
        _branch(
            "SKIP_STEP_WITH_REASON",
            {"reason": {"type": "string", "minLength": 1, "maxLength": 500}},
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    branches.append(
        _branch(
            "ANSWER_PROTOCOL_QUESTION",
            {"question": {"type": "string", "minLength": 1, "maxLength": 500}},
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    branches.append(
        _branch(
            "UNSUPPORTED",
            {"reason": {"type": "string", "enum": list(UNSUPPORTED_REASONS)}},
            use_const=use_const,
            length_keywords=length_keywords,
        )
    )
    return branches


def grammar_command_json_schema() -> dict[str, Any]:
    """The discriminated ``oneOf`` schema for grammar-constrained decoding (Ollama)."""

    return {"oneOf": _branches(use_const=True, length_keywords=True)}


def strict_command_json_schema() -> dict[str, Any]:
    """The same union in the dialect accepted by strict hosted structured output.

    Strict hosted structured output requires an object root, so the union is
    wrapped as ``{"command": ...}``; it uses ``anyOf`` with single-value enums,
    which is equivalent here because the intent constants are disjoint.
    """

    return {
        "type": "object",
        "properties": {"command": {"anyOf": _branches(use_const=False, length_keywords=False)}},
        "required": ["command"],
        "additionalProperties": False,
    }


def schema_sha256() -> str:
    """Hash of the canonical grammar schema, recorded in provenance."""

    canonical = json.dumps(grammar_command_json_schema(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_command_payload(
    transcript: str,
    context: CommandContext,
    *,
    wake_phrase: str,
) -> str:
    """Wrap untrusted operator and protocol text as bounded, delimited JSON data.

    Both the transcript and the active protocol instruction are attacker-influenced
    from the model's point of view. They are truncated and serialised as JSON values
    so a model that follows the system instruction treats them as data to extract
    from, never as instructions to obey.
    """

    data = {
        "transcript": transcript[:MAX_UNTRUSTED_CHARACTERS],
        "session_state": context.session_state,
        "active_step_id": context.active_step_id,
        "active_instruction": (context.active_instruction or "")[:MAX_UNTRUSTED_CHARACTERS],
        "optional_wake_phrase": wake_phrase,
    }
    return "DATA=" + json.dumps(data, ensure_ascii=True)


def parse_model_command(raw: str) -> ModelCommand:
    """Parse provider text into the discriminated union, failing closed.

    Accepts the bare object and the strict-dialect ``{"command": {...}}`` wrapper.
    Raises ``ValueError`` (including Pydantic's ``ValidationError``) on anything
    else; adapters translate that into ``ModelOutputError``.
    """

    decoded = json.loads(raw)
    if isinstance(decoded, dict) and set(decoded) == {"command"} and isinstance(
        decoded["command"], dict
    ):
        decoded = decoded["command"]
    if not isinstance(decoded, dict):
        raise ValueError("command output must be a JSON object")
    return _COMMAND_ADAPTER.validate_python(decoded, strict=True)


def command_parameters(command: ModelCommand) -> dict[str, str]:
    """The domain parameter mapping for one validated model command."""

    if isinstance(command, RecordMeasurement):
        return {"kind": command.kind, "value": command.value, "unit": command.unit}
    if isinstance(command, RecordObservation):
        return {"observation": command.observation}
    if isinstance(command, SetTimer):
        return {"duration_seconds": str(command.duration_seconds)}
    if isinstance(command, SkipStepWithReason):
        return {"reason": command.reason}
    if isinstance(command, AnswerProtocolQuestion):
        return {"question": command.question}
    return {}


def to_parsed_command(command: ModelCommand, transcript: str) -> tuple[ParsedCommand, str]:
    """Convert validated model output into a proposal with derived confidence.

    Returns the proposal and a short description of the evidence behind its
    confidence. A model proposal is accepted only at ``GROUNDED_MODEL_PROPOSAL``
    and only when every consequential entity appears in the utterance.
    """

    if isinstance(command, Unsupported):
        return (
            ParsedCommand(
                intent="UNSUPPORTED",
                parameters={},
                confidence=NO_EVIDENCE,
                ambiguities=(UNSUPPORTED_REASON_TEXT[command.reason],),
            ),
            f"model abstained: {command.reason}",
        )
    parameters = command_parameters(command)
    grounding = ground_command(transcript, command.intent, parameters)
    if not grounding.grounded:
        return (
            ParsedCommand(
                intent="UNSUPPORTED",
                parameters={},
                confidence=NO_EVIDENCE,
                ambiguities=(f"ungrounded {command.intent}: {grounding.reason}",),
            ),
            f"entity grounding failed: {grounding.reason}",
        )
    return (
        ParsedCommand(
            intent=command.intent,
            parameters=parameters,
            confidence=GROUNDED_MODEL_PROPOSAL,
        ),
        f"schema-valid and grounded: {grounding.reason}",
    )


__all__ = [
    "CONTRACT_VERSION",
    "MEASUREMENT_UNITS",
    "ModelCommand",
    "ValidationError",
    "build_command_payload",
    "command_parameters",
    "grammar_command_json_schema",
    "parse_model_command",
    "schema_sha256",
    "strict_command_json_schema",
    "to_parsed_command",
]
