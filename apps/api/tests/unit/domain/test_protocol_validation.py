"""Approved protocols satisfy domain invariants even outside the HTTP boundary."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from labjarvis.domain.models import (
    ExtractionFlag,
    ProtocolRequirement,
    ProtocolStepDraft,
    SourceReference,
)
from labjarvis.domain.protocol_validation import (
    PLACEHOLDER_REQUIREMENT_NAME,
    PLACEHOLDER_STEP_INSTRUCTION,
    PLACEHOLDER_STEP_TITLE,
    ProtocolValidationError,
    validate_protocol_for_approval,
    validate_protocol_steps,
)


def valid_steps() -> tuple[ProtocolStepDraft, ...]:
    return (
        ProtocolStepDraft(
            id="step-1",
            number=1,
            title="Prepare sample",
            instruction="Add ethanol.",
            requirements=(
                ProtocolRequirement(
                    requirement_id="step-1-req-1",
                    name="ethanol",
                    quantity=Decimal("10"),
                    unit="mL",
                    aliases=("EtOH",),
                ),
            ),
            duration_seconds=60,
            safety_notes=("Wear eye protection.",),
            source=SourceReference(page=1, section="Preparation", excerpt="Add ethanol."),
            parallel_allowed=False,
        ),
    )


def duplicate_requirement_id(steps: tuple[ProtocolStepDraft, ...]) -> tuple[ProtocolStepDraft, ...]:
    requirement = steps[0].requirements[0]
    return (
        steps[0],
        replace(
            steps[0],
            id="step-2",
            number=2,
            requirements=(replace(requirement, name="buffer"),),
        ),
    )


def nonfinite_quantity(steps: tuple[ProtocolStepDraft, ...]) -> tuple[ProtocolStepDraft, ...]:
    requirement = replace(steps[0].requirements[0], quantity=Decimal("NaN"))
    return (replace(steps[0], requirements=(requirement,)),)


def quantity_without_unit(steps: tuple[ProtocolStepDraft, ...]) -> tuple[ProtocolStepDraft, ...]:
    requirement = replace(steps[0].requirements[0], unit=None)
    return (replace(steps[0], requirements=(requirement,)),)


def blank_source_excerpt(steps: tuple[ProtocolStepDraft, ...]) -> tuple[ProtocolStepDraft, ...]:
    return (replace(steps[0], source=replace(steps[0].source, excerpt="  ")),)


@pytest.mark.parametrize(
    ("mutation", "code", "field"),
    [
        (
            duplicate_requirement_id,
            "DUPLICATE_REQUIREMENT_ID",
            "steps[1].requirements[0].requirement_id",
        ),
        (nonfinite_quantity, "INVALID_REQUIREMENT_QUANTITY", "steps[0].requirements[0].quantity"),
        (quantity_without_unit, "INCOMPLETE_REQUIREMENT_QUANTITY", "steps[0].requirements[0].unit"),
        (blank_source_excerpt, "SOURCE_EXCERPT_REQUIRED", "steps[0].source.excerpt"),
    ],
)
def test_validator_rejects_invalid_approved_data(mutation, code: str, field: str) -> None:
    with pytest.raises(ProtocolValidationError) as caught:
        validate_protocol_steps(mutation(valid_steps()))

    assert caught.value.code == code
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("changed", "code"),
    [
        (lambda step: replace(step, id="invalid step id"), "PROTOCOL_STEP_ID_INVALID"),
        (lambda step: replace(step, title="x" * 251), "PROTOCOL_STEP_TITLE_TOO_LONG"),
        (lambda step: replace(step, instruction="x" * 5001), "PROTOCOL_STEP_INSTRUCTION_TOO_LONG"),
        (lambda step: replace(step, duration_seconds=0), "PROTOCOL_STEP_DURATION_INVALID"),
        (
            lambda step: replace(
                step,
                requirements=(replace(step.requirements[0], quantity=Decimal("1000000001")),),
            ),
            "INVALID_REQUIREMENT_QUANTITY",
        ),
        (
            lambda step: replace(
                step,
                requirements=(replace(step.requirements[0], aliases=("EtOH", " etoh ")),),
            ),
            "DUPLICATE_REQUIREMENT_ALIAS",
        ),
        (lambda step: replace(step, safety_notes=(" ",)), "SAFETY_NOTE_REQUIRED"),
        (
            lambda step: replace(step, source=replace(step.source, excerpt="x" * 1001)),
            "SOURCE_EXCERPT_TOO_LONG",
        ),
    ],
)
def test_validator_enforces_practical_bounds(changed, code: str) -> None:
    with pytest.raises(ProtocolValidationError) as caught:
        validate_protocol_steps((changed(valid_steps()[0]),))

    assert caught.value.code == code


def test_validator_returns_canonical_trimmed_protocol() -> None:
    step = valid_steps()[0]
    requirement = replace(
        step.requirements[0],
        requirement_id=" step-1-req-1 ",
        name=" ethanol ",
        unit=" mL ",
        aliases=(" EtOH ",),
    )
    untrimmed = replace(
        step,
        id=" step-1 ",
        title=" Prepare sample ",
        instruction=" Add ethanol. ",
        requirements=(requirement,),
        safety_notes=(" Wear eye protection. ",),
        source=SourceReference(page=1, section=" Preparation ", excerpt=" Add ethanol. "),
    )

    canonical = validate_protocol_steps((untrimmed,))[0]

    assert canonical.id == "step-1"
    assert canonical.title == "Prepare sample"
    assert canonical.instruction == "Add ethanol."
    assert canonical.requirements[0].requirement_id == "step-1-req-1"
    assert canonical.requirements[0].name == "ethanol"
    assert canonical.requirements[0].unit == "mL"
    assert canonical.requirements[0].aliases == ("EtOH",)
    assert canonical.safety_notes == ("Wear eye protection.",)
    assert canonical.source == SourceReference(
        page=1,
        section="Preparation",
        excerpt="Add ethanol.",
    )


# ------------------------------------------------------------ approval gate


def with_flag(*, acknowledged: bool) -> tuple[ProtocolStepDraft, ...]:
    flag = ExtractionFlag(
        code="QUANTITY_RANGE",
        message="The source gives a range, so no quantity was set.",
        excerpt="1-2 mL ethanol",
        acknowledged=acknowledged,
    )
    return (replace(valid_steps()[0], flags=(flag,)),)


def test_drafts_may_carry_unreviewed_flags_but_approval_may_not() -> None:
    steps = with_flag(acknowledged=False)

    assert validate_protocol_steps(steps)[0].flags == steps[0].flags
    with pytest.raises(ProtocolValidationError) as error:
        validate_protocol_for_approval(steps)

    assert error.value.code == "PROTOCOL_FLAGS_UNRESOLVED"
    assert error.value.field == "steps[0].flags[0]"


def test_acknowledged_flags_do_not_block_approval() -> None:
    steps = with_flag(acknowledged=True)

    assert validate_protocol_for_approval(steps)[0].flags[0].acknowledged is True


@pytest.mark.parametrize(
    ("changed", "field"),
    [
        ({"title": PLACEHOLDER_STEP_TITLE}, "steps[0].title"),
        ({"instruction": f"  {PLACEHOLDER_STEP_INSTRUCTION} "}, "steps[0].instruction"),
    ],
)
def test_placeholder_step_text_cannot_be_approved(changed: dict[str, str], field: str) -> None:
    steps = (replace(valid_steps()[0], **changed),)

    validate_protocol_steps(steps)  # a draft may still hold it while being edited
    with pytest.raises(ProtocolValidationError) as error:
        validate_protocol_for_approval(steps)

    assert error.value.code == "PROTOCOL_PLACEHOLDER_UNRESOLVED"
    assert error.value.field == field


def test_placeholder_requirement_cannot_be_approved() -> None:
    step = valid_steps()[0]
    placeholder = replace(
        step.requirements[0],
        name=PLACEHOLDER_REQUIREMENT_NAME.upper(),
        quantity=None,
        unit=None,
    )
    steps = (replace(step, requirements=(placeholder,)),)

    with pytest.raises(ProtocolValidationError) as error:
        validate_protocol_for_approval(steps)

    assert error.value.code == "PROTOCOL_PLACEHOLDER_UNRESOLVED"
    assert error.value.field == "steps[0].requirements[0].name"


def test_flag_fields_are_bounded() -> None:
    flag = ExtractionFlag(code="bad code!", message="message")
    with pytest.raises(ProtocolValidationError) as error:
        validate_protocol_steps((replace(valid_steps()[0], flags=(flag,)),))

    assert error.value.code == "EXTRACTION_FLAG_CODE_INVALID"
