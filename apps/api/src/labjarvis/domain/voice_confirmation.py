"""Voice-only confirmation: value-bearing answers through wake-phrase listening.

With push-to-talk, the operator's key press says "this recording is my answer".
In voice-only mode there is no key: an always-on listener hears everything,
including colleagues, a radio, and LabJarvis' own read-back. So a voice-only
confirmation must carry more evidence than a push-to-talk one:

1. it must be **addressed** - it starts with the wake phrase ("Jarvis, ...");
2. it must **restate the content being confirmed** - the value and unit of a
   measurement or correction, the timer duration, the step number, the session
   or timer control, or the reagent name - so a bare "yes", "confirm" or "OK"
   never commits anything;
3. it must restate **exactly one** candidate value - "confirm 10 or 50
   millilitres" is not a confirmation of either - and must not name a
   different action ("confirm pause timer" never confirms a session pause);
4. it must not sound like the assistant's own prompt. Spoken prompts never
   contain the wake phrase (so an echo fails rule 1), and an utterance that
   still contains the prompt's "say ..." wording is treated as an echo.

Observations are the one exception to rule 2, and only when opted in: free
text cannot be restated reliably, so LabJarvis reads the text back and the
operator says the fixed phrase "confirm observation"; this is accepted only
when both transcriptions of the dictation agreed. It is weaker than a value
read-back and documented as such (ADR 0018).

Everything here is ordinary deterministic code. A refusal ("Jarvis, reject") is
always allowed by voice, because rejecting records nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from labjarvis.domain.confirmations import (
    UNIT_READBACK_TOKENS,
    ConfirmationIntent,
    normalise_number,
    read_confirmation,
)
from labjarvis.domain.transcripts import salient_tokens
from labjarvis.domain.wake_phrase import strip_wake_phrase


class ReadbackKind(StrEnum):
    MEASUREMENT = "measurement"
    DURATION = "duration"
    STEP = "step"
    STEP_SKIP = "step_skip"
    SESSION_CONTROL = "session_control"
    TIMER_CONTROL = "timer_control"
    REAGENT = "reagent"
    OBSERVATION = "observation"


#: Pending-action intents that can be confirmed voice-only, and what the answer
#: must restate. Anything absent must be confirmed with push-to-talk or on
#: screen. A correction restates its *new* value and unit; skipping restates
#: the step number; cancelling a timer restates its duration.
VOICE_ONLY_READBACK: dict[str, ReadbackKind] = {
    "RECORD_MEASUREMENT": ReadbackKind.MEASUREMENT,
    "CORRECT_PREVIOUS_ENTRY": ReadbackKind.MEASUREMENT,
    "SET_TIMER": ReadbackKind.DURATION,
    "COMPLETE_STEP": ReadbackKind.STEP,
    "SKIP_STEP_WITH_REASON": ReadbackKind.STEP_SKIP,
    "PAUSE_SESSION": ReadbackKind.SESSION_CONTROL,
    "RESUME_SESSION": ReadbackKind.SESSION_CONTROL,
    "END_SESSION": ReadbackKind.SESSION_CONTROL,
    "START_SESSION": ReadbackKind.SESSION_CONTROL,
    "PAUSE_TIMER": ReadbackKind.TIMER_CONTROL,
    "RESUME_TIMER": ReadbackKind.TIMER_CONTROL,
    "CANCEL_TIMER": ReadbackKind.TIMER_CONTROL,
}

#: Payload marker set by the application when both transcriptions of a
#: dictated observation agreed. Without it an observation is never voice-only.
DUAL_AGREED = "dual_agreed"

_SESSION_KEYWORDS = {
    "PAUSE_SESSION": ("pause", "pause"),
    "RESUME_SESSION": ("resume", "resume session"),
    "END_SESSION": ("end", "end session"),
    "START_SESSION": ("start", "start session"),
}
_TIMER_KEYWORDS = {
    "PAUSE_TIMER": "pause",
    "RESUME_TIMER": "resume",
    "CANCEL_TIMER": "cancel",
}
#: Action words that must not appear in a reply confirming a *different* action.
_ACTION_WORDS = frozenset(
    {"pause", "resume", "end", "finish", "start", "cancel", "skip", "complete"}
)
#: Accepted alternatives for a session keyword: "end" is often heard as "and"
#: by speech recognisers, so "confirm finish session" confirms an early end too.
_SESSION_ALTERNATES = {"END_SESSION": frozenset({"finish"})}
_OBSERVATION_FILLER = frozenset({"confirm", "confirmed", "yes", "observation", "this", "that"})

_UNIT_TOKENS = UNIT_READBACK_TOKENS
_ALL_UNIT_TOKENS = frozenset().union(*_UNIT_TOKENS.values())
_UNIT_WORDS = {"uL": "microlitres", "mL": "millilitres", "L": "litres", "mg": "milligrams",
               "g": "grams", "kg": "kilograms", "degC": "degrees Celsius", "K": "kelvin",
               "s": "seconds", "min": "minutes", "h": "hours", "rpm": "rpm", "pH": "pH",
               "%": "percent"}  # fmt: skip
_TIME_UNITS = {"s": 1, "min": 60, "h": 3600}
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
#: Words that only appear when the assistant's own prompt is re-captured.
_ECHO_MARKERS = frozenset({"say"})


def unit_words(unit: str) -> str:
    """The spoken form of a canonical unit ("mL" -> "millilitres")."""

    return _UNIT_WORDS.get(unit, unit)


@dataclass(frozen=True, slots=True)
class ExpectedReadback:
    """What a voice-only confirmation of one proposal must restate."""

    kind: ReadbackKind
    intent: str
    value: str = ""
    unit: str = ""
    #: Accepted spellings of a reagent (the expected label and its aliases).
    names: tuple[str, ...] = ()

    def phrase(self) -> str:
        """The exact words to say after the wake phrase (never includes it)."""

        if self.kind is ReadbackKind.MEASUREMENT:
            return f"confirm {self.value} {unit_words(self.unit)}".strip()
        if self.kind is ReadbackKind.DURATION:
            return f"confirm {spoken_duration(int(self.value))}"
        if self.kind is ReadbackKind.STEP:
            return f"confirm step {self.value}"
        if self.kind is ReadbackKind.STEP_SKIP:
            return f"confirm skip step {self.value}"
        if self.kind is ReadbackKind.SESSION_CONTROL:
            return f"confirm {_SESSION_KEYWORDS[self.intent][1]}"
        if self.kind is ReadbackKind.TIMER_CONTROL:
            keyword = _TIMER_KEYWORDS[self.intent]
            if self.value:
                return f"confirm {keyword} {adjectival_duration(int(self.value))} timer"
            return f"confirm {keyword} timer"
        if self.kind is ReadbackKind.OBSERVATION:
            return "confirm observation"
        return f"confirm {self.names[0] if self.names else 'the reagent name'}"


class VoiceDecision(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    AMBIGUOUS = "ambiguous"
    #: Not addressed to LabJarvis: ignored entirely, nothing recorded.
    NOT_ADDRESSED = "not_addressed"
    #: This proposal cannot be confirmed voice-only; use push-to-talk or screen.
    NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True, slots=True)
class VoiceVerdict:
    decision: VoiceDecision
    reasons: tuple[str, ...] = ()


def expected_readback(
    intent: str,
    payload: dict[str, str],
    *,
    step_number: int | None = None,
    voice_observations: bool = False,
) -> ExpectedReadback | None:
    """What a voice-only confirmation of this pending action must restate.

    ``None`` means the proposal cannot be confirmed voice-only.
    """

    if intent == "RECORD_OBSERVATION":
        if voice_observations and payload.get("heard_agreement") == DUAL_AGREED:
            return ExpectedReadback(ReadbackKind.OBSERVATION, intent)
        return None
    kind = VOICE_ONLY_READBACK.get(intent)
    if kind is None:
        return None
    if kind is ReadbackKind.MEASUREMENT:
        return ExpectedReadback(
            kind, intent, value=payload.get("value", ""), unit=payload.get("unit", "")
        )
    if kind is ReadbackKind.DURATION:
        return ExpectedReadback(kind, intent, value=payload.get("duration_seconds", ""))
    if kind in {ReadbackKind.STEP, ReadbackKind.STEP_SKIP}:
        if step_number is None:
            return None
        return ExpectedReadback(kind, intent, value=str(step_number))
    if kind is ReadbackKind.TIMER_CONTROL:
        if intent != "CANCEL_TIMER":
            return ExpectedReadback(kind, intent)
        duration = payload.get("timer_duration_seconds", "")
        if not duration.isdigit():
            return None
        return ExpectedReadback(kind, intent, value=duration)
    return ExpectedReadback(kind, intent)


def reagent_readback(expected_label: str, aliases: tuple[str, ...] = ()) -> ExpectedReadback:
    return ExpectedReadback(
        ReadbackKind.REAGENT,
        "VERIFY_REAGENT",
        names=tuple(name for name in (expected_label, *aliases) if name.strip()),
    )


def judge_voice_only_confirmation(
    transcript: str,
    *,
    wake_phrase: str,
    expected: ExpectedReadback | None,
) -> VoiceVerdict:
    """Decide what one wake-phrase utterance means for a waiting proposal."""

    body = strip_wake_phrase(transcript, wake_phrase)
    if body is None:
        return VoiceVerdict(
            VoiceDecision.NOT_ADDRESSED, ("the reply did not start with the wake phrase",)
        )
    tokens = salient_tokens(body)
    if _ECHO_MARKERS & set(tokens):
        return VoiceVerdict(
            VoiceDecision.AMBIGUOUS,
            ("that sounded like the assistant's own prompt, not an answer",),
        )
    reading = read_confirmation(_without_restated_cancel(body, tokens, expected))
    if reading.intent is ConfirmationIntent.REJECT:
        return VoiceVerdict(VoiceDecision.REJECT)
    if reading.intent is ConfirmationIntent.AMBIGUOUS:
        return VoiceVerdict(VoiceDecision.AMBIGUOUS, reading.reasons)
    if expected is None:
        return VoiceVerdict(
            VoiceDecision.NOT_SUPPORTED,
            ("this proposal cannot be confirmed voice-only; use push-to-talk or the screen",),
        )
    reasons = _readback_problems(tokens, expected)
    if reasons:
        return VoiceVerdict(VoiceDecision.AMBIGUOUS, reasons)
    return VoiceVerdict(VoiceDecision.CONFIRM)


def _without_restated_cancel(
    body: str, tokens: tuple[str, ...], expected: ExpectedReadback | None
) -> str:
    """Set aside a restated "cancel" in "confirm cancel 10 minute timer".

    "cancel" is a refusal word, so on its own ("Jarvis, cancel") it still
    rejects. Only when a timer cancellation is waiting and the reply both
    affirms and names the timer is the action word removed before the yes/no
    reading; the read-back check still sees it.
    """

    if (
        expected is None
        or expected.intent != "CANCEL_TIMER"
        or "timer" not in tokens
        or not ({"confirm", "confirmed", "yes"} & set(tokens))
    ):
        return body
    return re.sub(r"\bcancel\b", " ", body, count=1, flags=re.IGNORECASE)


def _other_actions(tokens: tuple[str, ...], allowed: set[str]) -> set[str]:
    return set(_ACTION_WORDS - allowed) & set(tokens)


def _numbers(tokens: tuple[str, ...]) -> list[str]:
    joined: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            _NUMBER.match(token)
            and index + 2 < len(tokens)
            and tokens[index + 1] in {"point", "decimal"}
            and _NUMBER.match(tokens[index + 2])
        ):
            joined.append(f"{token}.{tokens[index + 2]}")
            index += 3
            continue
        if _NUMBER.match(token):
            joined.append(token)
        index += 1
    return [value for value in (normalise_number(token) for token in joined) if value is not None]


def _readback_problems(tokens: tuple[str, ...], expected: ExpectedReadback) -> tuple[str, ...]:
    numbers = _numbers(tokens)
    distinct = set(numbers)
    say = f"say: {expected.phrase()}"

    if expected.kind is ReadbackKind.MEASUREMENT:
        wanted = normalise_number(expected.value.strip())
        if wanted is None:
            return ("the proposed value is not a number, so it cannot be confirmed by voice",)
        if not distinct:
            return (f"the reply did not repeat the value; {say}",)
        if len(distinct) > 1:
            values = ", ".join(sorted(distinct))
            return (f"the reply contained more than one value ({values}); {say}",)
        if wanted not in distinct:
            return (
                f"the value repeated back ({numbers[0]}) is not the value proposed "
                f"({wanted}); {say}",
            )
        spoken_units = {token for token in tokens if token in _ALL_UNIT_TOKENS}
        wanted_units = _UNIT_TOKENS.get(expected.unit, frozenset({expected.unit.casefold()}))
        if not spoken_units:
            return (f"the reply did not repeat the unit; {say}",)
        if spoken_units - wanted_units:
            return (f"the unit repeated back is not the unit proposed ({expected.unit}); {say}",)
        return ()

    if expected.kind is ReadbackKind.DURATION:
        seconds, problem = _spoken_seconds(tokens)
        if problem:
            return (f"{problem}; {say}",)
        if seconds != int(expected.value):
            return (f"the duration repeated back is not the proposed duration; {say}",)
        return ()

    if expected.kind in {ReadbackKind.STEP, ReadbackKind.STEP_SKIP}:
        if "step" not in tokens:
            return (f"the reply did not name the step; {say}",)
        skipping = expected.kind is ReadbackKind.STEP_SKIP
        if skipping and "skip" not in tokens:
            return (f"the reply did not say skip; {say}",)
        if _other_actions(tokens, {"skip" if skipping else "complete"}) or "timer" in tokens:
            return (f"the reply named a different action; {say}",)
        if len(distinct) != 1 or normalise_number(expected.value) not in distinct:
            return (f"the step number repeated back is not the step proposed; {say}",)
        return ()

    if expected.kind is ReadbackKind.SESSION_CONTROL:
        keywords = {_SESSION_KEYWORDS[expected.intent][0]} | set(
            _SESSION_ALTERNATES.get(expected.intent, frozenset())
        )
        if not keywords & set(tokens):
            return (f"the reply did not name the action; {say}",)
        if _other_actions(tokens, keywords) or "timer" in tokens or distinct:
            return (f"the reply named a different action or a value; {say}",)
        return ()

    if expected.kind is ReadbackKind.TIMER_CONTROL:
        keyword = _TIMER_KEYWORDS[expected.intent]
        if keyword not in tokens or "timer" not in tokens:
            return (f"the reply did not name the timer action; {say}",)
        if _other_actions(tokens, {keyword}) or "session" in tokens or "step" in tokens:
            return (f"the reply named a different action; {say}",)
        if not expected.value:
            if distinct:
                return (f"the reply contained a value that was not proposed; {say}",)
            return ()
        seconds, problem = _spoken_seconds(tokens)
        if problem:
            return (f"{problem}; {say}",)
        if seconds != int(expected.value):
            return (f"the timer repeated back is not the timer proposed; {say}",)
        return ()

    if expected.kind is ReadbackKind.OBSERVATION:
        if "observation" not in tokens:
            return (f"the reply did not say observation; {say}",)
        if (set(tokens) - _OBSERVATION_FILLER) or distinct:
            return (
                "an observation is confirmed with the fixed phrase only, because free "
                f"text cannot be restated reliably; {say}",
            )
        return ()

    # Reagent: every word of the expected name (or of one alias) must be said.
    spoken = set(tokens)
    for name in expected.names:
        words = [w for w in salient_tokens(name) if w]
        if words and all(word in spoken for word in words):
            return ()
    return (f"the reply did not name the expected reagent; {say}",)


def _spoken_seconds(tokens: tuple[str, ...]) -> tuple[int, str]:
    """Total seconds from "10 minutes", "1 hour 30 minutes"; problems otherwise."""

    total = 0.0
    pairs = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _NUMBER.match(token):
            unit = tokens[index + 1] if index + 1 < len(tokens) else ""
            if unit not in _TIME_UNITS:
                return 0, "a number in the reply was not followed by seconds, minutes or hours"
            total += float(token) * _TIME_UNITS[unit]
            pairs += 1
            index += 2
            continue
        if token in _TIME_UNITS and pairs == 0:
            return 0, "the reply did not repeat the duration"
        index += 1
    if pairs == 0:
        return 0, "the reply did not repeat the duration"
    return round(total), ""


def adjectival_duration(seconds: int) -> str:
    """"10 minute", "1 hour 30 minute" - the form used before the word timer."""

    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} hour"] if hours else []
    if minutes:
        parts.append(f"{minutes} minute")
    if secs or not parts:
        parts.append(f"{secs} second")
    return " ".join(parts)


def spoken_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs or not parts:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts)


# Backwards-compatible private alias used by earlier callers.
_spoken_duration = spoken_duration
