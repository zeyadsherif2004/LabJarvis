"""Explicit per-model inference settings for language-model interpreters.

One global timeout was wrong for every model at once: ``qwen3:4b`` is a
*thinking* variant and all 23 benchmark requests timed out at the global 45 s
limit, while the non-thinking 1.5B model needs about a second. A profile makes
thinking, timeout, temperature, context size, output budget and the
structured-output capability explicit for the configured model, and the
resolved profile is recorded with every inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StructuredOutput = Literal["json_schema", "json_object", "prompt_only"]
ThinkSetting = Literal["auto", "on", "off"]

#: Tag prefixes of local models known to think by default. Thinking is switched
#: off for command extraction: it multiplies latency without improving a
#: one-line structured answer, and the output is validated deterministically.
_THINKING_PREFIXES = ("qwen3", "deepseek-r1", "gpt-oss", "magistral", "phi4-reasoning")


@dataclass(frozen=True, slots=True)
class TextModelProfile:
    """Resolved inference settings for one text model."""

    model_name: str
    think: bool | None
    timeout_seconds: float
    temperature: float
    context_tokens: int
    max_output_tokens: int
    structured_output: StructuredOutput
    few_shot: bool

    def provenance(self) -> dict[str, str]:
        return {
            "think": "default" if self.think is None else str(self.think).lower(),
            "timeout_seconds": f"{self.timeout_seconds:g}",
            "temperature": f"{self.temperature:g}",
            "context_tokens": str(self.context_tokens),
            "max_output_tokens": str(self.max_output_tokens),
            "structured_output": self.structured_output,
            "few_shot": str(self.few_shot).lower(),
        }


def is_thinking_model(model_name: str) -> bool:
    lowered = model_name.casefold()
    return lowered.startswith(_THINKING_PREFIXES)


def resolve_text_profile(
    model_name: str,
    *,
    think: ThinkSetting = "auto",
    timeout_seconds: float = 20.0,
    temperature: float = 0.0,
    context_tokens: int = 4096,
    max_output_tokens: int = 128,
    structured_output: StructuredOutput = "json_schema",
    few_shot: bool = True,
) -> TextModelProfile:
    """Resolve ``think='auto'`` from the model family and freeze the settings."""

    resolved_think: bool | None
    if think == "on":
        resolved_think = True
    elif think == "off":
        resolved_think = False
    else:
        resolved_think = False if is_thinking_model(model_name) else None
    return TextModelProfile(
        model_name=model_name,
        think=resolved_think,
        timeout_seconds=timeout_seconds,
        temperature=temperature,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        structured_output=structured_output,
        few_shot=few_shot,
    )
