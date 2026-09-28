"""Generate the clearly-synthetic text-command research dataset.

Every sample is authored programmatically; no participant utterances exist.
``expected_parameters`` are in the canonical post-validation form used by
``labjarvis.domain.commands.validate_and_normalise_command``.
``action_valid`` is False when executing the command as understood would be an
error (invalid unit or value, unsupported request, multiple actions, or an
incomplete utterance).
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "manifests" / "text-commands.jsonl"


def _sample(
    identifier: str,
    category: str,
    transcript: str,
    expected_intent: str,
    expected_parameters: dict[str, str],
    *,
    action_valid: bool,
    notes: str = "",
) -> dict[str, object]:
    return {
        "id": identifier,
        "modality": "text",
        "synthetic": True,
        "category": category,
        "transcript": transcript,
        "expected_intent": expected_intent,
        "expected_parameters": expected_parameters,
        "action_valid": action_valid,
        "notes": notes,
    }


def build_samples() -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    index = 0

    def add(
        category: str,
        transcript: str,
        intent: str,
        parameters: dict[str, str],
        *,
        valid: bool,
        notes: str = "",
    ) -> None:
        nonlocal index
        index += 1
        samples.append(
            _sample(
                f"text-{index:03d}",
                category,
                transcript,
                intent,
                parameters,
                action_valid=valid,
                notes=notes,
            )
        )

    measure = "valid_measurement"
    for transcript, kind, value, unit in [
        ("Record the temperature as 37 degrees Celsius", "temperature", "37", "degC"),
        ("record the temperature as 4 celsius", "temperature", "4", "degC"),
        ("Record the temperature as 21.5 degrees Celsius", "temperature", "21.5", "degC"),
        ("record the ph as 7.4 ph", "ph", "7.4", "pH"),
        ("Record the volume as 250 milliliters", "volume", "250", "mL"),
        ("record the volume as 12.5 ml", "volume", "12.5", "mL"),
        ("Record the volume as 500 microliters", "volume", "500", "uL"),
        ("record the mass as 5 grams", "mass", "5", "g"),
        ("Record the mass as 120 milligrams", "mass", "120", "mg"),
        ("record the speed as 1500 rpm", "speed", "1500", "rpm"),
        ("Record the concentration as 70 percent", "concentration", "70", "%"),
        ("record the incubation time as 30 minutes", "incubation time", "30", "min"),
        ("Record the mass as 0.75 kilograms", "mass", "0.75", "kg"),
        ("record the temperature as -20 degrees celsius", "temperature", "-20", "degC"),
        ("Record the volume as 2 liters", "volume", "2", "L"),
    ]:
        add(
            measure,
            transcript,
            "RECORD_MEASUREMENT",
            {"kind": kind, "value": value, "unit": unit},
            valid=True,
        )

    for transcript, observation in [
        ("Record an observation as the solution is clear", "the solution is clear"),
        ("record an observation as precipitate formed at the bottom",
         "precipitate formed at the bottom"),
        ("Record observation as the mixture turned pale yellow",
         "the mixture turned pale yellow"),
        ("record an observation as no bubbles are visible", "no bubbles are visible"),
        ("Record an observation as the pellet resuspended fully",
         "the pellet resuspended fully"),
        ("record an observation as slight cloudiness after mixing",
         "slight cloudiness after mixing"),
    ]:
        add(
            "valid_observation",
            transcript,
            "RECORD_OBSERVATION",
            {"observation": observation},
            valid=True,
        )

    for transcript, seconds in [
        ("set a timer for 5 minutes", "300"),
        ("Set timer for 90 seconds", "90"),
        ("timer for 2 hours", "7200"),
        ("set a timer for 45 seconds", "45"),
        ("set a timer for 10 minutes", "600"),
    ]:
        add("valid_timer", transcript, "SET_TIMER", {"duration_seconds": seconds}, valid=True)

    for transcript, intent in [
        ("What is the current step", "GET_CURRENT_STEP"),
        ("current step", "GET_CURRENT_STEP"),
        ("what is the next step", "GET_NEXT_STEP"),
        ("Next step", "GET_NEXT_STEP"),
        ("repeat the instruction", "REPEAT_INSTRUCTION"),
        ("repeat", "REPEAT_INSTRUCTION"),
        ("pause", "PAUSE_SESSION"),
        ("pause session", "PAUSE_SESSION"),
        ("resume", "RESUME_SESSION"),  # session_state set to PAUSED below

        ("complete the step", "COMPLETE_STEP"),
        ("complete step", "COMPLETE_STEP"),
        ("verify reagent", "VERIFY_REAGENT"),
        ("check reagent", "VERIFY_REAGENT"),
        ("correct the previous entry", "CORRECT_PREVIOUS_ENTRY"),
        ("end session", "END_SESSION"),
        ("help", "HELP"),
        ("Jarvis, pause", "PAUSE_SESSION"),
        ("Jarvis complete the step", "COMPLETE_STEP"),
        ("Jarvis, what is the current step", "GET_CURRENT_STEP"),
    ]:
        add("valid_control", transcript, intent, {}, valid=True)

    paraphrase = "paraphrase_uncovered"
    for transcript, intent in [
        ("log the temperature at 37 celsius", "RECORD_MEASUREMENT"),
        ("note that the solution is cloudy", "RECORD_OBSERVATION"),
        ("write down that the gel has set", "RECORD_OBSERVATION"),
        ("start a five minute countdown", "SET_TIMER"),
        ("what do I do now", "GET_CURRENT_STEP"),
        ("move on", "COMPLETE_STEP"),
        ("go back to the previous entry and fix it", "CORRECT_PREVIOUS_ENTRY"),
        ("hold on a moment", "PAUSE_SESSION"),
        ("we are done here", "END_SESSION"),
        ("could you say that again please", "REPEAT_INSTRUCTION"),
    ]:
        add(
            paraphrase,
            transcript,
            intent,
            {},
            valid=True,
            notes="semantic intent outside the rule grammar; safe outcome is clarification",
        )

    invalid = "invalid_parseable"
    for transcript, notes in [
        ("Record the temperature as -300 degrees Celsius", "below absolute zero"),
        ("record the volume as 50 gallons", "unit not allowlisted"),
        ("Record the mass as 3 stone", "unit not allowlisted"),
        ("record the distance as 5 furlongs", "unit not allowlisted"),
        ("Record the volume as 9999999999999 milliliters", "magnitude bound exceeded"),
        ("set a timer for 0 minutes", "zero duration"),
        ("timer for 200 hours", "exceeds seven-day bound"),
        ("record the temperature as 37 degrees", "ambiguous unit word"),
    ]:
        add(invalid, transcript, "UNSUPPORTED", {}, valid=False, notes=notes)

    unsupported = "unsupported_out_of_scope"
    for transcript in [
        "skip all remaining steps",
        "delete the notebook records",
        "double all reagent volumes",
        "mark every step as complete",
        "ignore the protocol and continue",
        "is it safe to increase the concentration",
        "tell me a joke",
        "what is the boiling point of ethanol",
        "open the incubator door",
        "approve the protocol for me",
    ]:
        add(unsupported, transcript, "UNSUPPORTED", {}, valid=False)

    multi = "multi_command"
    for transcript in [
        "Record the temperature as 37 degrees Celsius and complete the step",
        "pause and end session",
        "record an observation as the solution is clear and record the ph as 7 ph",
    ]:
        add(multi, transcript, "UNSUPPORTED", {}, valid=False, notes="multiple actions")

    incomplete = "incomplete"
    for transcript in [
        "record the temperature",
        "set a timer",
        "as 37 degrees",
        "record",
        "",
    ]:
        add(incomplete, transcript, "UNSUPPORTED", {}, valid=False)

    # RESUME_SESSION is only legal from PAUSED; evaluate it in that state.
    for sample in samples:
        sample["session_state"] = (
            "PAUSED" if sample["expected_intent"] == "RESUME_SESSION" else "ACTIVE"
        )
    return samples


def main() -> None:
    samples = build_samples()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    print(f"wrote {len(samples)} samples to {MANIFEST}")


if __name__ == "__main__":
    main()
