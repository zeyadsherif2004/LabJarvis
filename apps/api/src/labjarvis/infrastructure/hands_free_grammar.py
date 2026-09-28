"""Deterministic hands-free phrasings (grammar version 2 extension).

Consulted only for utterances the version-1 grammar did not recognise, so no
version-1 result changes. Every rule is an anchored pattern over the whole
utterance (after the wake phrase): a phrase inside a longer sentence never
matches, and anything unrecognised is left to the language model, which may
only route, never supply a consequential value it did not hear.

Values extracted here are still validated by ``validate_and_normalise_command``
and resolved against authoritative state by the application (which timer,
which reagent, which notebook entry) before anything is proposed.
"""

from __future__ import annotations

import re

from labjarvis.domain.entity_grounding import DETERMINISTIC_MATCH
from labjarvis.domain.models import ParsedCommand

_FIXED: dict[str, str] = {
    # read-only questions with a structured answer
    "how long is left": "GET_ACTIVE_TIMERS",
    "how much time is left": "GET_ACTIVE_TIMERS",
    "how long left": "GET_ACTIVE_TIMERS",
    "how long is remaining": "GET_ACTIVE_TIMERS",
    "how much time remains": "GET_ACTIVE_TIMERS",
    "how much time is remaining": "GET_ACTIVE_TIMERS",
    "how long is left on the timer": "GET_ACTIVE_TIMERS",
    "how long is left on my timer": "GET_ACTIVE_TIMERS",
    "time left": "GET_ACTIVE_TIMERS",
    "time remaining": "GET_ACTIVE_TIMERS",
    "timer status": "GET_ACTIVE_TIMERS",
    "check the timer": "GET_ACTIVE_TIMERS",
    "check the timers": "GET_ACTIVE_TIMERS",
    "what timers are running": "GET_ACTIVE_TIMERS",
    "which timers are running": "GET_ACTIVE_TIMERS",
    "list timers": "GET_ACTIVE_TIMERS",
    "list the timers": "GET_ACTIVE_TIMERS",
    "what reagents do i need": "GET_EXPECTED_REAGENTS",
    "which reagents do i need": "GET_EXPECTED_REAGENTS",
    "what reagents are needed": "GET_EXPECTED_REAGENTS",
    "what reagents": "GET_EXPECTED_REAGENTS",
    "which reagents": "GET_EXPECTED_REAGENTS",
    "what do i need": "GET_EXPECTED_REAGENTS",
    "what do i need for this step": "GET_EXPECTED_REAGENTS",
    "list the reagents": "GET_EXPECTED_REAGENTS",
    "list reagents": "GET_EXPECTED_REAGENTS",
    "expected reagents": "GET_EXPECTED_REAGENTS",
    "where am i": "GET_PROTOCOL_STATE",
    "where am i in the protocol": "GET_PROTOCOL_STATE",
    "what is my progress": "GET_PROTOCOL_STATE",
    "whats my progress": "GET_PROTOCOL_STATE",
    "progress": "GET_PROTOCOL_STATE",
    "session status": "GET_PROTOCOL_STATE",
    "protocol status": "GET_PROTOCOL_STATE",
    "status": "GET_PROTOCOL_STATE",
    "what did i just record": "READ_LAST_ENTRY",
    "what did i record": "READ_LAST_ENTRY",
    "what was the last entry": "READ_LAST_ENTRY",
    "read the last entry": "READ_LAST_ENTRY",
    "read back the last entry": "READ_LAST_ENTRY",
    "last entry": "READ_LAST_ENTRY",
    "whats next": "GET_NEXT_STEP",
    "what should i do next": "GET_NEXT_STEP",
    "what do i do next": "GET_NEXT_STEP",
    "what should i do now": "GET_CURRENT_STEP",
    "what do i do now": "GET_CURRENT_STEP",
    "what is next": "GET_NEXT_STEP",
    "what comes next": "GET_NEXT_STEP",
    "whats the current step": "GET_CURRENT_STEP",
    "what is the current step": "GET_CURRENT_STEP",
    "whats the next step": "GET_NEXT_STEP",
    "what is the next step": "GET_NEXT_STEP",
    "what step am i on": "GET_CURRENT_STEP",
    "which step am i on": "GET_CURRENT_STEP",
    # session control
    "start the session": "START_SESSION",
    "start session": "START_SESSION",
    "begin the session": "START_SESSION",
    "begin session": "START_SESSION",
    "start the protocol": "START_SESSION",
    "resume the session": "RESUME_SESSION",
    "continue the session": "RESUME_SESSION",
    "pause the session": "PAUSE_SESSION",
    "end the session": "END_SESSION",
    # Speech recognisers often hear "end" as "and"; these are robust alternatives.
    "finish the session": "END_SESSION",
    "finish session": "END_SESSION",
    "close the session": "END_SESSION",
    # step completion
    "step complete": "COMPLETE_STEP",
    "step done": "COMPLETE_STEP",
    "complete this step": "COMPLETE_STEP",
    "mark the step complete": "COMPLETE_STEP",
    "mark step complete": "COMPLETE_STEP",
    "mark this step complete": "COMPLETE_STEP",
    "finish the step": "COMPLETE_STEP",
    "finish this step": "COMPLETE_STEP",
    # corrections without a value: the application asks for the value
    "correct the last entry": "CORRECT_PREVIOUS_ENTRY",
    "correct the last measurement": "CORRECT_PREVIOUS_ENTRY",
    "correct last measurement": "CORRECT_PREVIOUS_ENTRY",
}

_UNIT = r"(?P<unit>[a-zµμ°%][a-zµμ°% ]*?)"
_VALUE = r"(?P<value>-?\d+(?:\.\d+)?)"

_SKIP = re.compile(
    r"^skip\s+(?:this|the\s+current|the)?\s*step"
    r"(?:\s*(?:,|because|as|since|reason|the\s+reason\s+is)\s*[:,]?\s*(?P<reason>.+))?$",
    re.I,
)
_CORRECT = re.compile(
    r"^(?:correct|change|fix|amend|update)\s+(?:the\s+)?"
    r"(?:(?:last|previous|latest)\s+)?(?P<target>[a-z][a-z ]*?)?\s*"
    rf"(?:to|as|should\s+be|is)\s+{_VALUE}\s*{_UNIT}$",
    re.I,
)
_TIMER_CONTROL = re.compile(
    r"^(?P<action>pause|resume|continue|restart|cancel|delete|clear)\s+(?:the\s+|my\s+)?"
    r"(?P<ref>[a-z0-9 \-]*?)\s*timers?"
    r"(?:\s+(?:for|on|of)\s+(?P<ref2>[a-z0-9 ]+))?$",
    re.I,
)
_TIMER_START = re.compile(
    r"^(?:start|set|begin)\s+(?:a\s+|an\s+)?(?P<value>\d+)[\s-]*"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+timer$",
    re.I,
)
_REAGENT = re.compile(
    r"^(?:check|verify|scan|read)\s+(?:the\s+|this\s+)?(?P<target>.+?)"
    r"(?:\s+(?:bottle|label|reagent))?$",
    re.I,
)
_OBSERVATION = re.compile(
    r"^(?:record|note|log)\s+(?:an\s+|the\s+)?observation\s*[:,]?\s+(?:that\s+)?(?P<obs>.+)$",
    re.I,
)
#: Open (wh-) questions only. Yes/no forms ("is this the right bottle") are
#: left to the language model, which knows VERIFY_REAGENT; a transcript's
#: question mark is not evidence (speech recognisers add them inconsistently).
_QUESTION = re.compile(
    r"^(?:what|whats|what's|how|which|when|where|why|at\s+what|for\s+how|in\s+what)\b.+",
    re.I,
)
#: A wh-question about the assistant itself ("what can you do", "what can I
#: ask you") is a request for help, not a protocol question.
_ABOUT_ASSISTANT = re.compile(
    r"\b(?:can\s+you|could\s+you|do\s+you|are\s+you|you\s+(?:do|know|help|answer)|"
    r"ask\s+you|help\s+me\s+with|your\s+(?:skills|abilities))\b",
    re.I,
)
_TIMER_ACTIONS = {
    "pause": "PAUSE_TIMER",
    "resume": "RESUME_TIMER",
    "continue": "RESUME_TIMER",
    "restart": "RESUME_TIMER",
    "cancel": "CANCEL_TIMER",
    "delete": "CANCEL_TIMER",
    "clear": "CANCEL_TIMER",
}
_TIMER_FACTORS = {"s": 1, "m": 60, "h": 3600}
_REAGENT_EXCLUSIONS = frozenset(
    {"timer", "timers", "time", "step", "progress", "status", "alarm", "session"}
)
_COMPOUND_MARKERS = (
    "record ",
    "skip ",
    "correct ",
    "cancel ",
    "pause ",
    "resume ",
    "timer",
    "check ",
    "start ",
    "end ",
)


def _command(intent: str, **parameters: str) -> ParsedCommand:
    return ParsedCommand(intent=intent, parameters=parameters, confidence=DETERMINISTIC_MATCH)


def _clean(text: str) -> str:
    return " ".join(text.replace("?", " ").split()).strip(" ,.;:!")


def parse_hands_free_command(
    transcript: str, *, wake_phrase: str = "Jarvis"
) -> tuple[ParsedCommand, str] | None:
    """Recognise one hands-free phrasing, or ``None`` to leave it to the model."""

    wake_prefix = re.compile(
        rf"^\s*(?:hey\s+|ok\s+|okay\s+)?{re.escape(wake_phrase)}\s*[,;:.!]?\s*", re.I
    )
    body = _clean(wake_prefix.sub("", transcript))
    if not body:
        return None
    lowered = body.casefold()
    key = re.sub(r"[^a-z0-9 ]", "", lowered)

    if " and " in lowered and sum(lowered.count(marker) for marker in _COMPOUND_MARKERS) > 1:
        # Possibly two commands: not this grammar's call. The model decides
        # (and is itself told to refuse multiple commands).
        return None

    fixed = _FIXED.get(key)
    if fixed is not None:
        return _command(fixed), "hands_free_fixed_phrase"

    skip = _SKIP.fullmatch(body)
    if skip is not None:
        reason = _clean(skip.group("reason") or "")
        if reason:
            return _command("SKIP_STEP_WITH_REASON", reason=reason), "hands_free_skip"
        return _command("SKIP_STEP_WITH_REASON"), "hands_free_skip_without_reason"

    correction = _CORRECT.fullmatch(body)
    if correction is not None:
        target = " ".join((correction.group("target") or "").split()).casefold()
        if target in {"measurement", "entry", "value", "reading", "one", "record", ""}:
            target = ""
        return (
            _command(
                "CORRECT_PREVIOUS_ENTRY",
                target=target,
                value=correction.group("value"),
                unit=correction.group("unit").strip(),
            ),
            "hands_free_correction",
        )

    started = _TIMER_START.fullmatch(body)
    if started is not None:
        seconds = int(started.group("value")) * _TIMER_FACTORS[started.group("unit")[0].lower()]
        return _command("SET_TIMER", duration_seconds=str(seconds)), "hands_free_timer_start"

    timer = _TIMER_CONTROL.fullmatch(body)
    if timer is not None:
        reference = " ".join(
            part for part in (timer.group("ref") or "", timer.group("ref2") or "") if part.strip()
        )
        reference = " ".join(reference.split())
        intent = _TIMER_ACTIONS[timer.group("action").casefold()]
        if reference:
            return _command(intent, timer=reference), "hands_free_timer_control"
        return _command(intent), "hands_free_timer_control"

    observation = _OBSERVATION.fullmatch(body)
    if observation is not None:
        return (
            _command("RECORD_OBSERVATION", observation=_clean(observation.group("obs"))),
            "hands_free_observation",
        )

    reagent = _REAGENT.fullmatch(body)
    if reagent is not None:
        target = " ".join(reagent.group("target").split()).casefold()
        target = re.sub(r"^(?:reagent|bottle|label)\s+(?:number\s+)?", "", target).strip()
        if target and not (set(target.split()) & _REAGENT_EXCLUSIONS):
            return _command("VERIFY_REAGENT", reagent=target), "hands_free_reagent_selection"

    if _QUESTION.match(body) and _ABOUT_ASSISTANT.search(body):
        return _command("HELP"), "hands_free_help_question"
    if _QUESTION.match(body):
        return _command("ANSWER_PROTOCOL_QUESTION", question=body[:500]), "hands_free_question"
    return None
