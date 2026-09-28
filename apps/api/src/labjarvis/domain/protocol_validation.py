"""Canonical protocol validation independent of transport and persistence."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal

from labjarvis.domain.models import (
    ExtractionFlag,
    ProtocolRequirement,
    ProtocolStepDraft,
    SourceReference,
)

MAX_STEPS = 500
MAX_REQUIREMENTS_PER_STEP = 100
MAX_ALIASES_PER_REQUIREMENT = 50
MAX_SAFETY_NOTES_PER_STEP = 100
MAX_REQUIREMENT_QUANTITY = Decimal("1000000000")
MAX_DURATION_SECONDS = 604800
MAX_FLAGS_PER_STEP = 50

# Text the review screen inserts for content the operator still has to write.
# It is valid while a draft is edited and autosaved, and never approvable: a step
# that reads "replace this instruction" must not reach a running session.
PLACEHOLDER_STEP_TITLE = "Operator-added step"
PLACEHOLDER_STEP_INSTRUCTION = "Review and replace this instruction before approval."
PLACEHOLDER_REQUIREMENT_NAME = "review required"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ProtocolValidationError(ValueError):
    """One stable protocol invariant failure addressable by a client field."""

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _fail(code: str, field: str, message: str) -> None:
    raise ProtocolValidationError(code, field, message)


def _required(value: str, *, code: str, field: str, label: str) -> str:
    canonical = value.strip()
    if not canonical:
        _fail(code, field, f"{label} is required")
    return canonical


def _bounded(value: str, *, maximum: int, code: str, field: str, label: str) -> str:
    if len(value) > maximum:
        _fail(code, field, f"{label} must be at most {maximum} characters")
    return value


def _identifier(value: str, *, maximum: int, code: str, field: str) -> str:
    canonical = value.strip()
    if (
        not canonical
        or len(canonical) > maximum
        or _IDENTIFIER.fullmatch(canonical) is None
    ):
        _fail(
            code,
            field,
            "identifier must use letters, numbers, dot, underscore, colon, or hyphen",
        )
    return canonical


def _validate_requirement(
    requirement: ProtocolRequirement,
    *,
    field: str,
) -> ProtocolRequirement:
    requirement_id = _identifier(
        requirement.requirement_id,
        maximum=120,
        code="PROTOCOL_REQUIREMENT_ID_INVALID",
        field=f"{field}.requirement_id",
    )
    name = _bounded(
        _required(
            requirement.name,
            code="REQUIREMENT_NAME_REQUIRED",
            field=f"{field}.name",
            label="requirement name",
        ),
        maximum=250,
        code="REQUIREMENT_NAME_TOO_LONG",
        field=f"{field}.name",
        label="requirement name",
    )
    unit: str | None = None
    if requirement.unit is not None:
        unit = _bounded(
            _required(
                requirement.unit,
                code="REQUIREMENT_UNIT_REQUIRED",
                field=f"{field}.unit",
                label="requirement unit",
            ),
            maximum=30,
            code="REQUIREMENT_UNIT_TOO_LONG",
            field=f"{field}.unit",
            label="requirement unit",
        )
    if (requirement.quantity is None) != (unit is None):
        missing = "quantity" if requirement.quantity is None else "unit"
        _fail(
            "INCOMPLETE_REQUIREMENT_QUANTITY",
            f"{field}.{missing}",
            "quantity and unit must either both be present or both be absent",
        )
    if requirement.quantity is not None and (
        not requirement.quantity.is_finite()
        or requirement.quantity < 0
        or requirement.quantity > MAX_REQUIREMENT_QUANTITY
    ):
        _fail(
            "INVALID_REQUIREMENT_QUANTITY",
            f"{field}.quantity",
            f"quantity must be finite and between 0 and {MAX_REQUIREMENT_QUANTITY}",
        )
    if len(requirement.aliases) > MAX_ALIASES_PER_REQUIREMENT:
        _fail(
            "REQUIREMENT_ALIAS_COUNT_EXCEEDED",
            f"{field}.aliases",
            f"a requirement may have at most {MAX_ALIASES_PER_REQUIREMENT} aliases",
        )
    aliases: list[str] = []
    seen_aliases: set[str] = set()
    for index, alias in enumerate(requirement.aliases):
        alias_field = f"{field}.aliases[{index}]"
        canonical = _bounded(
            _required(
                alias,
                code="REQUIREMENT_ALIAS_REQUIRED",
                field=alias_field,
                label="requirement alias",
            ),
            maximum=250,
            code="REQUIREMENT_ALIAS_TOO_LONG",
            field=alias_field,
            label="requirement alias",
        )
        alias_key = canonical.casefold()
        if alias_key in seen_aliases:
            _fail(
                "DUPLICATE_REQUIREMENT_ALIAS",
                alias_field,
                "requirement aliases must be unique after normalization",
            )
        seen_aliases.add(alias_key)
        aliases.append(canonical)
    return replace(
        requirement,
        requirement_id=requirement_id,
        name=name,
        unit=unit,
        aliases=tuple(aliases),
    )


def _validate_flag(flag: ExtractionFlag, *, field: str) -> ExtractionFlag:
    code = _identifier(
        flag.code,
        maximum=60,
        code="EXTRACTION_FLAG_CODE_INVALID",
        field=f"{field}.code",
    )
    message = _bounded(
        _required(
            flag.message,
            code="EXTRACTION_FLAG_MESSAGE_REQUIRED",
            field=f"{field}.message",
            label="flag message",
        ),
        maximum=500,
        code="EXTRACTION_FLAG_MESSAGE_TOO_LONG",
        field=f"{field}.message",
        label="flag message",
    )
    excerpt = flag.excerpt.strip() if flag.excerpt is not None else None
    if excerpt:
        _bounded(
            excerpt,
            maximum=1000,
            code="EXTRACTION_FLAG_EXCERPT_TOO_LONG",
            field=f"{field}.excerpt",
            label="flag excerpt",
        )
    return replace(flag, code=code, message=message, excerpt=excerpt or None)


def validate_protocol_steps(
    steps: tuple[ProtocolStepDraft, ...],
) -> tuple[ProtocolStepDraft, ...]:
    """Validate and return a canonical immutable approved step sequence."""

    if not steps:
        _fail("PROTOCOL_STEPS_REQUIRED", "steps", "protocol requires at least one step")
    if len(steps) > MAX_STEPS:
        _fail(
            "PROTOCOL_STEP_COUNT_EXCEEDED",
            "steps",
            f"protocol may have at most {MAX_STEPS} steps",
        )
    if [step.number for step in steps] != list(range(1, len(steps) + 1)):
        _fail(
            "PROTOCOL_STEP_ORDER_INVALID",
            "steps",
            "step numbering must be contiguous and start at one",
        )

    canonical_steps: list[ProtocolStepDraft] = []
    step_ids: set[str] = set()
    requirement_ids: set[str] = set()
    for step_index, step in enumerate(steps):
        field = f"steps[{step_index}]"
        step_id = _identifier(
            step.id,
            maximum=80,
            code="PROTOCOL_STEP_ID_INVALID",
            field=f"{field}.id",
        )
        if step_id in step_ids:
            _fail("DUPLICATE_STEP_ID", f"{field}.id", "step IDs must be unique")
        step_ids.add(step_id)
        title = _bounded(
            _required(
                step.title,
                code="PROTOCOL_STEP_TITLE_REQUIRED",
                field=f"{field}.title",
                label="step title",
            ),
            maximum=250,
            code="PROTOCOL_STEP_TITLE_TOO_LONG",
            field=f"{field}.title",
            label="step title",
        )
        instruction = _bounded(
            _required(
                step.instruction,
                code="PROTOCOL_STEP_INSTRUCTION_REQUIRED",
                field=f"{field}.instruction",
                label="step instruction",
            ),
            maximum=5000,
            code="PROTOCOL_STEP_INSTRUCTION_TOO_LONG",
            field=f"{field}.instruction",
            label="step instruction",
        )
        if len(step.requirements) > MAX_REQUIREMENTS_PER_STEP:
            _fail(
                "PROTOCOL_REQUIREMENT_COUNT_EXCEEDED",
                f"{field}.requirements",
                f"a step may have at most {MAX_REQUIREMENTS_PER_STEP} requirements",
            )
        requirements: list[ProtocolRequirement] = []
        for requirement_index, requirement in enumerate(step.requirements):
            requirement_field = f"{field}.requirements[{requirement_index}]"
            canonical_requirement = _validate_requirement(
                requirement,
                field=requirement_field,
            )
            if canonical_requirement.requirement_id in requirement_ids:
                _fail(
                    "DUPLICATE_REQUIREMENT_ID",
                    f"{requirement_field}.requirement_id",
                    "requirement IDs must be unique across the protocol version",
                )
            requirement_ids.add(canonical_requirement.requirement_id)
            requirements.append(canonical_requirement)
        if step.duration_seconds is not None and not (
            1 <= step.duration_seconds <= MAX_DURATION_SECONDS
        ):
            _fail(
                "PROTOCOL_STEP_DURATION_INVALID",
                f"{field}.duration_seconds",
                f"duration must be between 1 and {MAX_DURATION_SECONDS} seconds",
            )
        if len(step.safety_notes) > MAX_SAFETY_NOTES_PER_STEP:
            _fail(
                "SAFETY_NOTE_COUNT_EXCEEDED",
                f"{field}.safety_notes",
                f"a step may have at most {MAX_SAFETY_NOTES_PER_STEP} safety notes",
            )
        safety_notes = tuple(
            _bounded(
                _required(
                    note,
                    code="SAFETY_NOTE_REQUIRED",
                    field=f"{field}.safety_notes[{index}]",
                    label="safety note",
                ),
                maximum=1000,
                code="SAFETY_NOTE_TOO_LONG",
                field=f"{field}.safety_notes[{index}]",
                label="safety note",
            )
            for index, note in enumerate(step.safety_notes)
        )
        if step.source.page < 1:
            _fail("SOURCE_PAGE_INVALID", f"{field}.source.page", "source page must be positive")
        section: str | None = None
        if step.source.section is not None:
            section = _bounded(
                _required(
                    step.source.section,
                    code="SOURCE_SECTION_REQUIRED",
                    field=f"{field}.source.section",
                    label="source section",
                ),
                maximum=250,
                code="SOURCE_SECTION_TOO_LONG",
                field=f"{field}.source.section",
                label="source section",
            )
        excerpt = _bounded(
            _required(
                step.source.excerpt,
                code="SOURCE_EXCERPT_REQUIRED",
                field=f"{field}.source.excerpt",
                label="source excerpt",
            ),
            maximum=1000,
            code="SOURCE_EXCERPT_TOO_LONG",
            field=f"{field}.source.excerpt",
            label="source excerpt",
        )
        if len(step.flags) > MAX_FLAGS_PER_STEP:
            _fail(
                "EXTRACTION_FLAG_COUNT_EXCEEDED",
                f"{field}.flags",
                f"a step may have at most {MAX_FLAGS_PER_STEP} extraction flags",
            )
        flags = tuple(
            _validate_flag(flag, field=f"{field}.flags[{index}]")
            for index, flag in enumerate(step.flags)
        )
        canonical_steps.append(
            replace(
                step,
                flags=flags,
                id=step_id,
                title=title,
                instruction=instruction,
                requirements=tuple(requirements),
                safety_notes=safety_notes,
                source=SourceReference(
                    page=step.source.page,
                    section=section,
                    excerpt=excerpt,
                    ocr_derived=step.source.ocr_derived,
                ),
            )
        )
    return tuple(canonical_steps)


def _is_placeholder(value: str, placeholder: str) -> bool:
    return value.strip().casefold() == placeholder.casefold()


def validate_protocol_for_approval(
    steps: tuple[ProtocolStepDraft, ...],
) -> tuple[ProtocolStepDraft, ...]:
    """Validate steps and also refuse anything a reviewer has not yet settled.

    Draft saves need only the structural checks, because a half-reviewed draft
    is exactly what autosave exists to keep. Approval is where the reviewer
    asserts the steps *are* the protocol, so it additionally requires every
    placeholder to be replaced and every extraction flag to be acknowledged.
    """

    canonical = validate_protocol_steps(steps)
    for step_index, step in enumerate(canonical):
        field = f"steps[{step_index}]"
        if _is_placeholder(step.title, PLACEHOLDER_STEP_TITLE):
            _fail(
                "PROTOCOL_PLACEHOLDER_UNRESOLVED",
                f"{field}.title",
                f"step {step.number} still has the placeholder title",
            )
        if _is_placeholder(step.instruction, PLACEHOLDER_STEP_INSTRUCTION):
            _fail(
                "PROTOCOL_PLACEHOLDER_UNRESOLVED",
                f"{field}.instruction",
                f"step {step.number} still has the placeholder instruction",
            )
        for requirement_index, requirement in enumerate(step.requirements):
            if _is_placeholder(requirement.name, PLACEHOLDER_REQUIREMENT_NAME):
                _fail(
                    "PROTOCOL_PLACEHOLDER_UNRESOLVED",
                    f"{field}.requirements[{requirement_index}].name",
                    f"step {step.number} has a requirement still named "
                    f"'{PLACEHOLDER_REQUIREMENT_NAME}'",
                )
        for flag_index, flag in enumerate(step.flags):
            if not flag.acknowledged:
                _fail(
                    "PROTOCOL_FLAGS_UNRESOLVED",
                    f"{field}.flags[{flag_index}]",
                    f"step {step.number} has an extraction flag nobody has reviewed: "
                    f"{flag.message}",
                )
    return canonical
