"""A step may name several reagents, and each needs a stable address.

Verification is per requirement, so the backend cannot silently work on
``requirements[0]``. Every requirement carries a ``requirement_id`` that stays
stable across draft edits, storage round-trips, and API responses.
"""

from decimal import Decimal

from labjarvis.infrastructure.documents import (
    ExtractedDocument,
    ExtractedPage,
)
from labjarvis.infrastructure.protocol_import import parse_protocol_steps


def _document(*lines: str) -> ExtractedDocument:
    body = "\n".join(lines)
    return ExtractedDocument(
        media_type="text/plain",
        sha256="0" * 64,
        pages=(ExtractedPage(number=1, text=body),),
    )


def test_every_reagent_in_one_instruction_becomes_its_own_requirement() -> None:
    steps = parse_protocol_steps(
        _document("1. Add 10 mL ethanol, then add 250 uL buffer to the tube.")
    )

    requirements = steps[0].requirements
    assert [requirement.name for requirement in requirements] == ["ethanol", "buffer"]
    assert requirements[0].quantity == Decimal("10")
    assert requirements[0].unit == "mL"
    assert requirements[1].quantity == Decimal("250")
    assert requirements[1].unit == "uL"


def test_requirement_identifiers_are_unique_and_derived_from_the_step() -> None:
    steps = parse_protocol_steps(
        _document("1. Add 10 mL ethanol, then add 250 uL buffer to the tube.")
    )

    identifiers = [requirement.requirement_id for requirement in steps[0].requirements]
    assert identifiers == ["step-1-req-1", "step-1-req-2"]
    assert len(set(identifiers)) == len(identifiers)


def test_a_step_without_a_reagent_has_no_requirements() -> None:
    steps = parse_protocol_steps(_document("1. Wait for the centrifuge to stop."))

    assert steps[0].requirements == ()
