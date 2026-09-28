"""Author the end-to-end orchestration scenario dataset.

Scenarios are scripted event sequences over a synthetic three-step protocol.
Some command transcripts are deliberately corrupted to simulate transcription
errors; these are controlled synthetic perturbations, clearly flagged with
``perturbed: true``, not recordings of real model output.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "manifests" / "scenarios.json"


def command(
    transcript: str,
    intent: str,
    parameters: dict[str, str],
    *,
    valid: bool = True,
    perturbed: bool = False,
    note: str = "",
) -> dict[str, object]:
    return {
        "type": "command",
        "transcript": transcript,
        "expected_intent": intent,
        "expected_parameters": parameters,
        "action_valid": valid,
        "perturbed": perturbed,
        "note": note,
    }


def label(observed: str, expected: str, truth: str, *, note: str = "") -> dict[str, object]:
    return {
        "type": "label",
        "observed_text": observed,
        "expected_reagent": expected,
        "truth": truth,
        "note": note,
    }


TEMP = ("RECORD_MEASUREMENT", {"kind": "temperature", "value": "37", "unit": "degC"})
OBS = ("RECORD_OBSERVATION", {"observation": "the solution is clear"})


def build() -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []

    def add(
        scenario_id: str,
        family: str,
        events: list[dict[str, object]],
        *,
        expected_final_state: str,
        note: str = "",
    ) -> None:
        scenarios.append(
            {
                "id": scenario_id,
                "family": family,
                "steps": 3,
                "events": events,
                "expected_final_state": expected_final_state,
                "note": note,
            }
        )

    # Happy paths.
    add("scn-01", "happy", [
        command("Record the temperature as 37 degrees Celsius", *TEMP),
        command("complete the step", "COMPLETE_STEP", {}),
        command("Record an observation as the solution is clear", *OBS),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-02", "happy", [
        command("set a timer for 5 minutes", "SET_TIMER", {"duration_seconds": "300"}),
        command("Record the volume as 250 milliliters", "RECORD_MEASUREMENT",
                {"kind": "volume", "value": "250", "unit": "mL"}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("pause", "PAUSE_SESSION", {}),
        command("resume", "RESUME_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-03", "happy", [
        command("Record the mass as 5 grams", "RECORD_MEASUREMENT",
                {"kind": "mass", "value": "5", "unit": "g"}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("end session", "END_SESSION", {}),
    ], expected_final_state="ABORTED")
    add("scn-04", "happy", [
        label("ETHANOL", "ethanol", "match"),
        command("Record an observation as no bubbles are visible", "RECORD_OBSERVATION",
                {"observation": "no bubbles are visible"}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-05", "happy", [
        command("Record the ph as 7.4 ph", "RECORD_MEASUREMENT",
                {"kind": "ph", "value": "7.4", "unit": "pH"}),
        command("set a timer for 90 seconds", "SET_TIMER", {"duration_seconds": "90"}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-06", "happy", [
        label("SODIUM CHLORIDE", "sodium chloride", "match"),
        command("Record the concentration as 70 percent", "RECORD_MEASUREMENT",
                {"kind": "concentration", "value": "70", "unit": "%"}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    # Simulated transcription corruption: parseable but wrong content.
    for index, (heard, true_params) in enumerate([
        ("Record the temperature as 73 degrees Celsius", TEMP[1]),
        ("Record the volume as 25 milliliters",
         {"kind": "volume", "value": "250", "unit": "mL"}),
        ("Record the mass as 50 grams", {"kind": "mass", "value": "5", "unit": "g"}),
    ], start=7):
        add(f"scn-{index:02d}", "corrupted_value", [
            command(heard, "RECORD_MEASUREMENT", true_params, valid=False, perturbed=True,
                    note="simulated digit transposition; recorded value would be wrong"),
            command("complete the step", "COMPLETE_STEP", {}),
            command("complete the step", "COMPLETE_STEP", {}),
            command("complete the step", "COMPLETE_STEP", {}),
        ], expected_final_state="COMPLETED")

    # Out-of-state commands.
    add("scn-10", "state_conflict", [
        command("resume", "RESUME_SESSION", {}, valid=False,
                note="session already active"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-11", "state_conflict", [
        command("pause", "PAUSE_SESSION", {}),
        command("Record the temperature as 37 degrees Celsius", TEMP[0], TEMP[1], valid=False,
                note="recording while paused is not allowed"),
        command("resume", "RESUME_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-12", "state_conflict", [
        command("pause", "PAUSE_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}, valid=False,
                note="completing while paused is not allowed"),
        command("resume", "RESUME_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    # Invalid units/values.
    add("scn-13", "invalid_value", [
        command("Record the temperature as -300 degrees Celsius", "UNSUPPORTED", {},
                valid=False, note="below absolute zero"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-14", "invalid_value", [
        command("record the volume as 50 gallons", "UNSUPPORTED", {}, valid=False,
                note="unit not allowlisted"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-15", "invalid_value", [
        command("timer for 200 hours", "UNSUPPORTED", {}, valid=False,
                note="exceeds seven-day bound"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    # Label mismatches and uncertainty.
    add("scn-16", "label_mismatch", [
        label("METHANOL", "ethanol", "mismatch",
              note="visually similar wrong reagent"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-17", "label_mismatch", [
        label("SODIUM CHLORATE", "sodium chloride", "mismatch",
              note="one-letter chemical confusion"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-18", "label_mismatch", [
        label("", "ethanol", "unreadable", note="no readable text"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-19", "label_uncertain", [
        label("ETHAN0L", "ethanol", "mismatch", note="digit-for-letter defacement"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-20", "label_uncertain", [
        label("ISOPROPANOL 70 12", "isopropanol", "match",
              note="matching label with OCR clutter"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    # Multi-command and out-of-scope requests.
    add("scn-21", "unsupported", [
        command("Record the temperature as 37 degrees Celsius and complete the step",
                "UNSUPPORTED", {}, valid=False, note="two actions in one utterance"),
        command("Record the temperature as 37 degrees Celsius", *TEMP),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-22", "unsupported", [
        command("skip all remaining steps", "UNSUPPORTED", {}, valid=False),
        command("delete the notebook records", "UNSUPPORTED", {}, valid=False),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    # Mixed long scenarios.
    add("scn-23", "mixed", [
        label("ETHANOL", "ethanol", "match"),
        command("Record the temperature as 73 degrees Celsius", "RECORD_MEASUREMENT",
                TEMP[1], valid=False, perturbed=True, note="digit transposition"),
        command("Record the temperature as 37 degrees Celsius", *TEMP),
        command("complete the step", "COMPLETE_STEP", {}),
        command("resume", "RESUME_SESSION", {}, valid=False, note="already active"),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")
    add("scn-24", "mixed", [
        label("METHANOL", "ethanol", "mismatch"),
        command("record the volume as 50 gallons", "UNSUPPORTED", {}, valid=False),
        command("Record the volume as 250 milliliters", "RECORD_MEASUREMENT",
                {"kind": "volume", "value": "250", "unit": "mL"}),
        command("pause", "PAUSE_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}, valid=False,
                note="completing while paused"),
        command("resume", "RESUME_SESSION", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
        command("complete the step", "COMPLETE_STEP", {}),
    ], expected_final_state="COMPLETED")

    return scenarios


def main() -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    scenarios = build()
    MANIFEST.write_text(json.dumps(scenarios, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(scenarios)} scenarios to {MANIFEST}")


if __name__ == "__main__":
    main()
