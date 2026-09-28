from labjarvis.domain.models import ParsedCommand
from research.experiments.common import component_outcomes_for_command


def sample(*, valid: bool = True, state: str = "ACTIVE") -> dict[str, object]:
    return {
        "action_valid": valid,
        "expected_intent": "RECORD_MEASUREMENT" if valid else "UNSUPPORTED",
        "expected_parameters": (
            {"kind": "temperature", "value": "37", "unit": "degC"} if valid else {}
        ),
        "session_state": state,
    }


def proposal(*, unit: str = "degrees Celsius", confidence: float = 0.99) -> ParsedCommand:
    return ParsedCommand(
        intent="RECORD_MEASUREMENT",
        parameters={"kind": "temperature", "value": "37", "unit": unit},
        confidence=confidence,
    )


def test_validation_gate_isolated_from_state_and_confidence() -> None:
    outcomes = component_outcomes_for_command(
        proposal(unit="gallons"), sample(valid=False), session_state="ACTIVE"
    )

    assert outcomes["proposal"]["executed"] is True
    assert outcomes["validation"]["executed"] is False
    assert outcomes["state"]["executed"] is False


def test_state_gate_isolated_after_successful_validation() -> None:
    outcomes = component_outcomes_for_command(
        proposal(), sample(state="PAUSED"), session_state="PAUSED"
    )

    assert outcomes["validation"]["executed"] is True
    assert outcomes["state"]["executed"] is False


def test_confidence_gate_isolated_after_validation_and_state() -> None:
    outcomes = component_outcomes_for_command(
        proposal(confidence=0.50), sample(), session_state="ACTIVE"
    )

    assert outcomes["state"]["executed"] is True
    assert outcomes["confidence"]["executed"] is False
    assert outcomes["confirm_attentive"]["confirmations"] == 0


def test_confirmation_scripts_diverge_only_after_a_pending_action() -> None:
    incorrect = sample(valid=False)
    incorrect["expected_intent"] = "RECORD_MEASUREMENT"
    outcomes = component_outcomes_for_command(
        proposal(), incorrect, session_state="ACTIVE"
    )

    assert outcomes["confidence"]["executed"] is True
    assert outcomes["confirm_attentive"]["executed"] is False
    assert outcomes["confirm_always_accept"]["executed"] is True
    assert outcomes["confirm_attentive"]["confirmations"] == 1
    assert outcomes["confirm_always_accept"]["confirmations"] == 1
