"""The deterministic command grammar: the first, and safest, text interpreter.

Version 1 is the grammar the project has shipped since July (fake profile
identity ``fake-text-rules-v1``). On the frozen 81-command benchmark it produced
0 unsafe executions after validation, and behind real Whisper it recorded both
spoken measurements correctly while the 1.5B local LLM failed both.

Version 2 (``rule-grammar-v2``, hands-free) keeps every version-1 rule and its
result unchanged and adds the hands-free phrasings - timer control and
queries, protocol questions, skipping with a reason, correcting the last
measurement, choosing a reagent by name or number, and starting the session -
**only for utterances version 1 did not match** (see ``hands_free_grammar``).
``parse_rule_command(..., hands_free=False)`` reproduces version 1 exactly for
the frozen benchmark. Everything neither version recognises is still the
language model's job in the cascade, reported separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from labjarvis.domain.entity_grounding import DETERMINISTIC_MATCH, NO_EVIDENCE
from labjarvis.domain.models import ParsedCommand
from labjarvis.domain.transcripts import spoken_quantities_to_digits
from labjarvis.infrastructure.hands_free_grammar import parse_hands_free_command
from labjarvis.ports.models import AdapterReadiness, CommandContext, TextResult

GRAMMAR_VERSION = "rule-grammar-v2"
GRAMMAR_V1 = "rule-grammar-v1"

MEASUREMENT_PATTERN = re.compile(
    r"\brecord\s+(?:the\s+)?(?P<kind>[a-z][a-z ]*?)\s+as\s+"
    r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>.+?)\s*[.!]?$",
    re.I,
)
OBSERVATION_PATTERN = re.compile(
    r"\brecord\s+(?:an\s+)?observation\s+as\s+(?P<observation>.+?)\s*[.!]?$",
    re.I,
)
TIMER_COMMAND_PATTERN = re.compile(
    r"^(?:set\s+)?(?:a\s+)?timer\s+(?:for\s+)?(?P<value>\d+)\s*"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)$",
    re.I,
)
_KNOWN_MARKERS = (
    "record ",
    "pause",
    "resume",
    "complete the step",
    "next step",
    "current step",
    "timer",
    "verify reagent",
    "end session",
    "correct previous",
)
_FIXED_PHRASES = {
    "pause": "PAUSE_SESSION",
    "pause session": "PAUSE_SESSION",
    "resume": "RESUME_SESSION",
    "resume session": "RESUME_SESSION",
    "complete the step": "COMPLETE_STEP",
    "complete step": "COMPLETE_STEP",
    "confirm step": "COMPLETE_STEP",
    "what is the current step": "GET_CURRENT_STEP",
    "current step": "GET_CURRENT_STEP",
    "what is the next step": "GET_NEXT_STEP",
    "next step": "GET_NEXT_STEP",
    "repeat": "REPEAT_INSTRUCTION",
    "repeat instruction": "REPEAT_INSTRUCTION",
    "repeat the instruction": "REPEAT_INSTRUCTION",
    "verify reagent": "VERIFY_REAGENT",
    "check reagent": "VERIFY_REAGENT",
    "correct previous entry": "CORRECT_PREVIOUS_ENTRY",
    "correct the previous entry": "CORRECT_PREVIOUS_ENTRY",
    "end session": "END_SESSION",
    "abort session": "END_SESSION",
    "help": "HELP",
}


@dataclass(frozen=True, slots=True)
class RuleParse:
    """A grammar result plus whether the grammar recognised the utterance at all.

    ``matched`` is False only when no rule applied, which is the signal for the
    cascade to consult a language model. A recognised compound command is a
    match: the grammar is certain it must be refused.
    """

    command: ParsedCommand
    matched: bool
    rule: str


def parse_rule_command(
    transcript: str,
    *,
    wake_phrase: str = "Jarvis",
    hands_free: bool = True,
) -> RuleParse:
    """Apply the fixed grammar to one utterance.

    Version-1 rules run first and win; the hands-free rules are consulted only
    when version 1 recognised nothing, so no version-1 result can change.
    """

    parse = _parse_both(transcript, wake_phrase=wake_phrase, hands_free=hands_free)
    if parse.matched:
        return parse
    # Transcribers write quantities as words as often as digits ("twenty-one
    # degrees"). Retrying with the quantities written as digits keeps those
    # commands in the deterministic grammar instead of sending them to the
    # language model, which in a real session once returned a correction
    # without its value. It runs only when nothing matched, so no existing
    # grammar result can change, and the value is still grounded against the
    # original utterance downstream.
    digits = spoken_quantities_to_digits(transcript)
    if digits == transcript:
        return parse
    retried = _parse_both(digits, wake_phrase=wake_phrase, hands_free=hands_free)
    if not retried.matched or retried.command.intent == "UNSUPPORTED":
        return parse
    return RuleParse(retried.command, matched=True, rule=f"{retried.rule}+spoken_quantity")


def _parse_both(transcript: str, *, wake_phrase: str, hands_free: bool) -> RuleParse:
    parse = _parse_v1(transcript, wake_phrase=wake_phrase)
    if parse.matched or not hands_free:
        return parse
    extension = parse_hands_free_command(transcript, wake_phrase=wake_phrase)
    if extension is None:
        return parse
    command, rule = extension
    return RuleParse(command, matched=True, rule=rule)


def _parse_v1(transcript: str, *, wake_phrase: str) -> RuleParse:
    wake_prefix = re.compile(rf"^\s*{re.escape(wake_phrase)}\s*[,;:]?\s*", flags=re.I)
    command = wake_prefix.sub("", transcript).strip()
    lowered = command.casefold()
    marker_count = sum(lowered.count(marker) for marker in _KNOWN_MARKERS)
    if " and " in lowered and marker_count > 1:
        return RuleParse(
            ParsedCommand(
                intent="UNSUPPORTED",
                parameters={},
                confidence=NO_EVIDENCE,
                ambiguities=("multiple commands",),
            ),
            matched=True,
            rule="multiple_commands",
        )

    measurement = MEASUREMENT_PATTERN.search(command)
    if measurement is not None:
        return RuleParse(
            ParsedCommand(
                intent="RECORD_MEASUREMENT",
                parameters={
                    "kind": " ".join(measurement.group("kind").split()).casefold(),
                    "value": measurement.group("value"),
                    "unit": measurement.group("unit").strip(),
                },
                confidence=DETERMINISTIC_MATCH,
            ),
            matched=True,
            rule="measurement",
        )

    observation = OBSERVATION_PATTERN.search(command)
    if observation is not None:
        return RuleParse(
            ParsedCommand(
                intent="RECORD_OBSERVATION",
                parameters={"observation": observation.group("observation").strip()},
                confidence=DETERMINISTIC_MATCH,
            ),
            matched=True,
            rule="observation",
        )

    timer = TIMER_COMMAND_PATTERN.fullmatch(command.rstrip(".!"))
    if timer is not None:
        value = int(timer.group("value"))
        unit = timer.group("unit").casefold()
        if unit.startswith(("hour", "hr")):
            value *= 3600
        elif unit.startswith(("minute", "min")):
            value *= 60
        return RuleParse(
            ParsedCommand(
                intent="SET_TIMER",
                parameters={"duration_seconds": str(value)},
                confidence=DETERMINISTIC_MATCH,
            ),
            matched=True,
            rule="timer",
        )

    # Transcribers punctuate spoken questions, so "What is the current step?"
    # must reach the same fixed phrase as the unpunctuated form.
    intent = _FIXED_PHRASES.get(lowered.rstrip(".!? "))
    if intent is not None:
        return RuleParse(
            ParsedCommand(intent=intent, parameters={}, confidence=DETERMINISTIC_MATCH),
            matched=True,
            rule="fixed_phrase",
        )

    return RuleParse(
        ParsedCommand(
            intent="UNSUPPORTED",
            parameters={},
            confidence=NO_EVIDENCE,
            ambiguities=("unsupported command",),
        ),
        matched=False,
        rule="no_match",
    )


class RuleGrammarTextUnderstanding:
    """The grammar as a stand-alone ``TextUnderstandingPort`` (the rules-only system)."""

    adapter_name = "rule-grammar"
    model_name = GRAMMAR_VERSION

    def __init__(self, *, wake_phrase: str = "Jarvis") -> None:
        self._wake_phrase = wake_phrase

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        del context
        parse = parse_rule_command(transcript, wake_phrase=self._wake_phrase)
        return TextResult(
            command=parse.command,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={
                "interpreter": "rules",
                "cascade_route": "rule" if parse.matched else "rule_no_match",
                "rule_name": parse.rule,
                "confidence_basis": (
                    "deterministic grammar match" if parse.matched else "no rule matched"
                ),
            },
        )

    def readiness(self) -> AdapterReadiness:
        return AdapterReadiness(
            ready=True,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="deterministic grammar; no model is loaded",
            dependency_installed=True,
            model_cached=True,
            loaded=True,
            inference_tested=True,
        )
