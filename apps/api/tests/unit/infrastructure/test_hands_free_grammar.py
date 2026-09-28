"""Grammar v2 (hands-free): new phrasings, and version-1 results provably unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.infrastructure.rule_grammar import GRAMMAR_VERSION, parse_rule_command

ROOT = Path(__file__).parents[5]


@pytest.mark.parametrize(
    ("utterance", "intent", "parameters"),
    [
        ("Jarvis, how long is left", "GET_ACTIVE_TIMERS", {}),
        ("Jarvis, how much time is left?", "GET_ACTIVE_TIMERS", {}),
        ("Jarvis, what reagents do I need", "GET_EXPECTED_REAGENTS", {}),
        ("Jarvis, where am I", "GET_PROTOCOL_STATE", {}),
        ("Jarvis, what did I just record", "READ_LAST_ENTRY", {}),
        ("Jarvis, what's next", "GET_NEXT_STEP", {}),
        # Transcribed questions carry a question mark; they must not fall
        # through to protocol retrieval, which cannot answer "which step".
        ("Jarvis, what is the current step?", "GET_CURRENT_STEP", {}),
        ("What is the current step?", "GET_CURRENT_STEP", {}),
        ("Jarvis, what is the next step?", "GET_NEXT_STEP", {}),
        ("Jarvis, what's the next step?", "GET_NEXT_STEP", {}),
        ("Jarvis, what's the current step?", "GET_CURRENT_STEP", {}),
        ("Repeat the instruction?", "REPEAT_INSTRUCTION", {}),
        # Quantities the transcriber spelled out stay in the deterministic grammar.
        (
            "Jarvis, record the temperature as twenty-one degrees Celsius.",
            "RECORD_MEASUREMENT",
            {"kind": "temperature", "value": "21", "unit": "degC"},
        ),
        (
            "Jarvis, correct the last measurement to twenty-two degrees Celsius.",
            "CORRECT_PREVIOUS_ENTRY",
            {"target": "", "value": "22", "unit": "degC"},
        ),
        ("Jarvis, set a timer for one minute.", "SET_TIMER", {"duration_seconds": "60"}),
        ("Jarvis, start the session", "START_SESSION", {}),
        ("Jarvis, resume the session", "RESUME_SESSION", {}),
        ("Jarvis, step complete", "COMPLETE_STEP", {}),
        ("Jarvis, pause the timer", "PAUSE_TIMER", {}),
        ("Jarvis, resume the timer", "RESUME_TIMER", {}),
        ("Jarvis, cancel the 10 minute timer", "CANCEL_TIMER", {"timer": "10 minute"}),
        ("Jarvis, cancel the timer for step 2", "CANCEL_TIMER", {"timer": "step 2"}),
        ("Jarvis, pause the incubation timer", "PAUSE_TIMER", {"timer": "incubation"}),
        ("Jarvis, cancel the 10-minute timer.", "CANCEL_TIMER", {"timer": "10-minute"}),
        ("Jarvis, finish the session", "END_SESSION", {}),
        ("Jarvis, start a 5 minute timer", "SET_TIMER", {"duration_seconds": "300"}),
        (
            "Jarvis, skip this step because the reagent is unavailable",
            "SKIP_STEP_WITH_REASON",
            {"reason": "the reagent is unavailable"},
        ),
        ("Jarvis, skip this step", "SKIP_STEP_WITH_REASON", {}),
        (
            "Jarvis, correct the last measurement to 12 millilitres",
            "CORRECT_PREVIOUS_ENTRY",
            {"target": "", "value": "12", "unit": "mL"},
        ),
        (
            "Jarvis, correct the volume to 12 mL",
            "CORRECT_PREVIOUS_ENTRY",
            {"target": "volume", "value": "12", "unit": "mL"},
        ),
        ("Jarvis, check the ethanol", "VERIFY_REAGENT", {"reagent": "ethanol"}),
        ("Jarvis, check reagent two", "VERIFY_REAGENT", {"reagent": "two"}),
        ("Jarvis, verify the buffer A bottle", "VERIFY_REAGENT", {"reagent": "buffer a"}),
        (
            "Jarvis, record observation the solution turned cloudy",
            "RECORD_OBSERVATION",
            {"observation": "the solution turned cloudy"},
        ),
        (
            "Jarvis, what temperature should I incubate at?",
            "ANSWER_PROTOCOL_QUESTION",
            {"question": "what temperature should I incubate at"},
        ),
        ("Jarvis, what can you do", "HELP", {}),
    ],
)
def test_hands_free_phrasings(utterance: str, intent: str, parameters: dict[str, str]) -> None:
    parsed = validate_and_normalise_command(parse_rule_command(utterance).command)
    assert parsed.intent == intent
    assert dict(parsed.parameters) == parameters


@pytest.mark.parametrize(
    "utterance",
    [
        "Jarvis, is this the right bottle",  # yes/no: the model's job
        "Jarvis, stop the centrifuge and check the timer",  # possibly two commands
        "Jarvis, check the timer and record the volume",
        "Jarvis, check the progress",
    ],
)
def test_ambiguous_phrasings_are_left_to_the_model_or_refused(utterance: str) -> None:
    parse = parse_rule_command(utterance)
    assert parse.command.intent not in {"VERIFY_REAGENT", "CANCEL_TIMER"}


def test_grammar_version_is_recorded() -> None:
    assert GRAMMAR_VERSION == "rule-grammar-v2"


def _rows(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(
    "manifest",
    [
        "research/data/manifests/text-commands.jsonl",
        "research/data/heldout/human-paraphrases.jsonl",
        "research/data/heldout/human-paraphrases-B.jsonl",
    ],
)
def test_every_version_1_result_is_unchanged(manifest: str) -> None:
    path = ROOT / manifest
    if not path.exists():  # pragma: no cover - the manifests ship with the repo
        pytest.skip(f"{manifest} is not present")
    for row in _rows(path):
        text = row.get("transcript") or row.get("text") or row.get("utterance") or ""
        v1 = parse_rule_command(text, hands_free=False)
        v2 = parse_rule_command(text)
        if v1.matched:
            assert v2.command == v1.command, text
        # Nothing the new rules add is ever a state change the label did not ask for.
        if not v1.matched and v2.matched:
            assert v2.command.intent in {
                "ANSWER_PROTOCOL_QUESTION",
                "HELP",
                "GET_NEXT_STEP",
                "GET_CURRENT_STEP",
                "GET_ACTIVE_TIMERS",
                "GET_EXPECTED_REAGENTS",
                "GET_PROTOCOL_STATE",
                "READ_LAST_ENTRY",
                "VERIFY_REAGENT",
                row.get("expected_intent"),
            }, text
