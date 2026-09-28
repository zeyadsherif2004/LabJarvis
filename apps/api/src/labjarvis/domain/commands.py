"""Deterministic validation for model-proposed command values."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from labjarvis.domain.models import ParsedCommand

PARAMETERLESS_INTENTS = frozenset(
    {
        "GET_CURRENT_STEP",
        "GET_NEXT_STEP",
        "REPEAT_INSTRUCTION",
        "GET_PROTOCOL_STATE",
        "GET_EXPECTED_REAGENTS",
        "GET_ACTIVE_TIMERS",
        "READ_LAST_ENTRY",
        "START_SESSION",
        "PAUSE_SESSION",
        "RESUME_SESSION",
        "COMPLETE_STEP",
        "END_SESSION",
        "HELP",
        "UNSUPPORTED",
    }
)

UNIT_ALIASES = {
    "ul": "uL",
    "µl": "uL",
    "microliter": "uL",
    "microliters": "uL",
    "microlitre": "uL",
    "microlitres": "uL",
    "ml": "mL",
    "milliliter": "mL",
    "milliliters": "mL",
    "millilitre": "mL",
    "millilitres": "mL",
    "l": "L",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
    "mg": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "°c": "degC",
    "degc": "degC",
    "degree celsius": "degC",
    "degrees celsius": "degC",
    "celsius": "degC",
    "k": "K",
    "kelvin": "K",
    "s": "s",
    "sec": "s",
    "secs": "s",
    "second": "s",
    "seconds": "s",
    "min": "min",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "h": "h",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "rpm": "rpm",
    "ph": "pH",
    "%": "%",
    "percent": "%",
    "percentage": "%",
}


def _reject(command: ParsedCommand, reason: str) -> ParsedCommand:
    return ParsedCommand(
        intent="UNSUPPORTED",
        parameters={},
        confidence=0.0,
        ambiguities=(*command.ambiguities, reason),
    )


def _normalise_decimal(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def validate_and_normalise_command(command: ParsedCommand) -> ParsedCommand:
    """Return a canonical allowlisted command or a non-mutating ``UNSUPPORTED`` result."""

    parameters = dict(command.parameters)
    if command.intent in PARAMETERLESS_INTENTS:
        if parameters:
            return _reject(command, "unexpected command parameters")
        return command

    if command.intent == "RECORD_MEASUREMENT":
        if set(parameters) != {"kind", "value", "unit"}:
            return _reject(command, "measurement fields are incomplete")
        kind = " ".join(parameters["kind"].split()).casefold()
        value = _normalise_decimal(parameters["value"])
        unit_key = " ".join(parameters["unit"].replace("μ", "µ").split()).casefold()
        unit = UNIT_ALIASES.get(unit_key)
        if not kind or value is None or unit is None or abs(value) > Decimal("1e12"):
            return _reject(command, "measurement value or unit is invalid")
        if "temperature" in kind and unit == "degC" and value < Decimal("-273.15"):
            return _reject(command, "temperature is below absolute zero")
        return replace(
            command,
            parameters={"kind": kind, "value": _decimal_text(value), "unit": unit},
        )

    if command.intent == "RECORD_OBSERVATION":
        if set(parameters) != {"observation"}:
            return _reject(command, "observation field is incomplete")
        observation = " ".join(parameters["observation"].split())
        if not observation or len(observation) > 2000:
            return _reject(command, "observation is blank or too long")
        return replace(command, parameters={"observation": observation})

    if command.intent == "SET_TIMER":
        if set(parameters) != {"duration_seconds"}:
            return _reject(command, "timer duration is incomplete")
        if re.fullmatch(r"[1-9]\d*", parameters["duration_seconds"].strip()) is None:
            return _reject(command, "timer duration is invalid")
        duration = int(parameters["duration_seconds"])
        if duration > 604800:
            return _reject(command, "timer duration exceeds seven days")
        return replace(command, parameters={"duration_seconds": str(duration)})

    if command.intent in _OPTIONAL_TEXT_FIELD:
        # Hands-free references: which reagent, which timer, why a step is
        # skipped. Absent means "ask the operator" (or "the only one"), never a
        # default chosen silently; the application resolves them against state.
        field = _OPTIONAL_TEXT_FIELD[command.intent]
        if not parameters:
            return command
        if set(parameters) != {field}:
            return _reject(command, f"{field} field is invalid")
        text = " ".join(parameters[field].split())
        if not text or len(text) > _TEXT_LIMITS[field]:
            return _reject(command, f"{field} is blank or too long")
        return replace(command, parameters={field: text})

    if command.intent == "ANSWER_PROTOCOL_QUESTION":
        if set(parameters) != {"question"}:
            return _reject(command, "question field is incomplete")
        question = " ".join(parameters["question"].split())
        if not question or len(question) > 500:
            return _reject(command, "question is blank or too long")
        return replace(command, parameters={"question": question})

    if command.intent == "CORRECT_PREVIOUS_ENTRY":
        if not parameters:
            # "Correct the previous entry" without a value: the application
            # asks for the new value rather than guessing one.
            return command
        if set(parameters) != {"target", "value", "unit"}:
            return _reject(command, "correction fields are incomplete")
        target = " ".join(parameters["target"].split()).casefold()
        measured = validate_and_normalise_command(
            ParsedCommand(
                intent="RECORD_MEASUREMENT",
                parameters={
                    "kind": target or "measurement",
                    "value": parameters["value"],
                    "unit": parameters["unit"],
                },
                confidence=command.confidence,
            )
        )
        if measured.intent == "UNSUPPORTED":
            return _reject(command, "correction value or unit is invalid")
        return replace(
            command,
            parameters={
                "target": target,
                "value": measured.parameters["value"],
                "unit": measured.parameters["unit"],
            },
        )

    return _reject(command, "intent is not allowlisted")


#: Intents that take one optional free-text reference.
_OPTIONAL_TEXT_FIELD = {
    "VERIFY_REAGENT": "reagent",
    "PAUSE_TIMER": "timer",
    "RESUME_TIMER": "timer",
    "CANCEL_TIMER": "timer",
    "SKIP_STEP_WITH_REASON": "reason",
}
_TEXT_LIMITS = {"reagent": 120, "timer": 120, "reason": 500}
