"""Turn a protocol vocabulary into the context each speech backend accepts."""

from __future__ import annotations

PROMPT_PREFIX = "Laboratory protocol dictation. Terms:"
MAX_PROMPT_CHARACTERS = 600


def protocol_prompt(vocabulary: tuple[str, ...]) -> str:
    """An initial prompt / transcription prompt listing the protocol's terms."""

    prompt = f"{PROMPT_PREFIX} {', '.join(vocabulary)}."
    return prompt[:MAX_PROMPT_CHARACTERS]


def protocol_hotwords(vocabulary: tuple[str, ...]) -> str:
    """faster-whisper ``hotwords``: space-separated terms (multi-word terms kept)."""

    return " ".join(vocabulary)[:MAX_PROMPT_CHARACTERS]
