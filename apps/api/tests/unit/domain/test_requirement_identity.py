"""Requirement identity survives storage and stays addressable."""

from decimal import Decimal

from labjarvis.domain.models import ProtocolRequirement, ProtocolStepDraft, SourceReference
from labjarvis.infrastructure.database import _protocol_steps, _steps_json


def _step(*requirements: ProtocolRequirement) -> ProtocolStepDraft:
    return ProtocolStepDraft(
        id="step-1",
        number=1,
        title="Add reagents",
        instruction="Add 10 mL ethanol and 250 uL buffer.",
        requirements=requirements,
        duration_seconds=None,
        safety_notes=(),
        source=SourceReference(page=1, section=None, excerpt="Add reagents"),
    )


def test_requirement_identifiers_round_trip_through_storage() -> None:
    step = _step(
        ProtocolRequirement(
            requirement_id="step-1-req-1",
            name="ethanol",
            quantity=Decimal("10"),
            unit="mL",
        ),
        ProtocolRequirement(requirement_id="step-1-req-2", name="buffer"),
    )

    restored = _protocol_steps(_steps_json((step,)))

    assert [item.requirement_id for item in restored[0].requirements] == [
        "step-1-req-1",
        "step-1-req-2",
    ]
    assert restored[0].requirements[0].quantity == Decimal("10")


def test_legacy_rows_without_an_identifier_are_backfilled_by_position() -> None:
    legacy = (
        '[{"duration_seconds":null,"id":"step-1","instruction":"Add 10 mL ethanol.",'
        '"number":1,"parallel_allowed":false,'
        '"requirements":[{"aliases":[],"name":"ethanol","quantity":"10","unit":"mL"},'
        '{"aliases":[],"name":"buffer","quantity":null,"unit":null}],'
        '"safety_notes":[],'
        '"source":{"excerpt":"Add 10 mL ethanol.","page":1,"section":null},'
        '"title":"Add reagents"}]'
    )

    restored = _protocol_steps(legacy)

    assert [item.requirement_id for item in restored[0].requirements] == [
        "step-1-req-1",
        "step-1-req-2",
    ]
