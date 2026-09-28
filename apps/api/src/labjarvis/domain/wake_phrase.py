"""Decide whether an always-listening utterance was addressed to the assistant.

In hands-free listening the browser's voice-activity detector sends every
speech segment it hears. Only segments that *begin* with the wake phrase are
treated as commands; everything else - colleagues talking, the assistant's own
voice leaking back, radio - is discarded without interpretation, retention or
a notebook entry. Push-to-talk never needs the wake phrase.
"""

from __future__ import annotations

import re

_LEADING_FILLERS = ("hey", "ok", "okay", "hi", "um", "uh")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold())


def addressed_to_assistant(transcript: str, wake_phrase: str) -> bool:
    """True when the utterance starts with the wake phrase (after a filler word)."""

    wake = _words(wake_phrase)
    if not wake:
        return False
    words = _words(transcript)
    while words and words[0] in _LEADING_FILLERS and words[: len(wake)] != wake:
        words = words[1:]
    return words[: len(wake)] == wake and len(words) > len(wake)


def strip_wake_phrase(transcript: str, wake_phrase: str) -> str | None:
    """The words after the wake phrase, or ``None`` when it was not addressed.

    Only a *leading* wake phrase counts: "tell Jarvis to confirm" is not
    addressed to the assistant, and an echo of a prompt that merely mentions the
    wake phrase in the middle is not either.
    """

    if not addressed_to_assistant(transcript, wake_phrase):
        return None
    wake = _words(wake_phrase)
    words = _words(transcript)
    while words and words[0] in _LEADING_FILLERS and words[: len(wake)] != wake:
        words = words[1:]
    return " ".join(words[len(wake) :])
