"""The tool registry is the single allowlist every entry point must pass through.

These tests pin the safety-relevant invariants: read-only tools never mutate,
record-writing tools always need a human, and the realtime surface is a strict
subset that cannot carry a destructive replay.
"""

import pytest

from labjarvis.application.tools import (
    TOOLS,
    TOOLS_BY_INTENT,
    ConfirmationPolicy,
    ToolKind,
    ToolSpec,
    get_tool,
    realtime_tool_schemas,
    tool_for_intent,
)
from labjarvis.domain.models import SessionState

RECORD_WRITING = {
    "record_measurement",
    "record_observation",
    "complete_step",
    "skip_step_with_reason",
    "verify_reagent",
    "end_session",
}


def test_an_unknown_tool_name_is_not_resolvable() -> None:
    assert get_tool("drop_database") is None
    assert get_tool("") is None
    assert tool_for_intent("ARBITRARY_INTENT") is None


def test_read_only_tools_never_require_confirmation_and_never_mutate() -> None:
    read_only = [spec for spec in TOOLS.values() if spec.kind is ToolKind.READ_ONLY]
    assert read_only, "the registry must expose read-only tools"
    for spec in read_only:
        assert spec.confirmation is ConfirmationPolicy.NONE
        assert spec.idempotent
        assert not spec.requires_confirmation(model_proposed=True)
        assert not spec.requires_confirmation(model_proposed=False)


def test_every_tool_that_writes_an_experimental_record_always_needs_a_human() -> None:
    for name in RECORD_WRITING:
        spec = TOOLS[name]
        assert spec.confirmation is ConfirmationPolicy.ALWAYS, name
        assert spec.requires_confirmation(model_proposed=True), name
        # A direct button press is not a substitute for confirming a record.
        assert spec.requires_confirmation(model_proposed=False), name


def test_reversible_controls_confirm_model_proposals_but_not_direct_actions() -> None:
    spec = TOOLS["pause_session"]

    assert spec.confirmation is ConfirmationPolicy.MODEL_PROPOSALS_ONLY
    assert spec.requires_confirmation(model_proposed=True)
    assert not spec.requires_confirmation(model_proposed=False)


def test_legal_states_are_enforced_per_tool() -> None:
    assert TOOLS["resume_session"].permits(SessionState.PAUSED)
    assert not TOOLS["resume_session"].permits(SessionState.ACTIVE)
    assert TOOLS["complete_step"].permits(SessionState.ACTIVE)
    assert not TOOLS["complete_step"].permits(SessionState.COMPLETED)
    assert not TOOLS["record_measurement"].permits(SessionState.ABORTED)


def test_no_tool_is_legal_in_a_terminal_session() -> None:
    for spec in TOOLS.values():
        assert not spec.permits(SessionState.COMPLETED), spec.name
        assert not spec.permits(SessionState.ABORTED), spec.name


def test_intent_mapping_is_one_to_one_so_the_prompt_contract_cannot_drift() -> None:
    intents = [spec.intent for spec in TOOLS.values() if spec.intent is not None]
    assert len(intents) == len(set(intents))
    for intent, spec in TOOLS_BY_INTENT.items():
        assert tool_for_intent(intent) is spec


def test_realtime_schemas_are_a_strict_subset_of_the_registry() -> None:
    schemas = realtime_tool_schemas()
    exposed = {schema["name"] for schema in schemas}

    assert exposed < set(TOOLS)
    assert "verify_reagent" not in exposed, "binary uploads never travel as function arguments"
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["parameters"]["additionalProperties"] is False
        assert set(schema["parameters"]["required"]) <= set(schema["parameters"]["properties"])


def test_realtime_schemas_declare_every_required_parameter() -> None:
    for schema in realtime_tool_schemas():
        spec = TOOLS[schema["name"]]
        assert set(schema["parameters"]["required"]) == set(spec.required_parameters)


def test_a_read_only_tool_may_not_declare_a_confirmation_policy() -> None:
    with pytest.raises(ValueError, match="cannot require confirmation"):
        ToolSpec(
            name="bad_read",
            version="1",
            kind=ToolKind.READ_ONLY,
            confirmation=ConfirmationPolicy.ALWAYS,
            summary="",
            parameters={},
            legal_session_states=frozenset(),
            audit_event="x",
            idempotent=True,
            success_template="",
            failure_template="",
        )


def test_a_mutating_tool_must_declare_a_confirmation_policy() -> None:
    with pytest.raises(ValueError, match="must declare a confirmation policy"):
        ToolSpec(
            name="bad_write",
            version="1",
            kind=ToolKind.MUTATING,
            confirmation=ConfirmationPolicy.NONE,
            summary="",
            parameters={},
            legal_session_states=frozenset(),
            audit_event="x",
            idempotent=False,
            success_template="",
            failure_template="",
        )


def test_required_parameters_must_exist_in_the_schema() -> None:
    with pytest.raises(ValueError, match="undeclared parameters"):
        ToolSpec(
            name="bad_params",
            version="1",
            kind=ToolKind.MUTATING,
            confirmation=ConfirmationPolicy.ALWAYS,
            summary="",
            parameters={},
            required_parameters=("missing",),
            legal_session_states=frozenset(),
            audit_event="x",
            idempotent=False,
            success_template="",
            failure_template="",
        )


def test_every_tool_declares_an_audit_event_and_operator_messages() -> None:
    for spec in TOOLS.values():
        assert spec.audit_event.strip(), spec.name
        assert spec.success_template.strip(), spec.name
        assert spec.failure_template.strip(), spec.name
        assert spec.version.strip(), spec.name
