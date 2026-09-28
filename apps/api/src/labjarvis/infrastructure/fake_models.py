"""Deterministic adapters for offline tests and development demonstrations."""

from __future__ import annotations

from labjarvis.infrastructure.model_errors import ModelInputError
from labjarvis.infrastructure.rule_grammar import parse_rule_command
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    LabelResult,
    SpeechResult,
    TextResult,
)


def _fixture_text(data: bytes) -> str:
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelInputError("fake media must be UTF-8 text prefixed with FAKE:") from exc
    if not decoded.startswith("FAKE:"):
        raise ModelInputError("fake media must use the explicit FAKE: fixture prefix")
    value = decoded.removeprefix("FAKE:").strip()
    if not value:
        raise ModelInputError("FAKE: fixture value cannot be blank")
    return value


class FakeSpeechToText:
    """A deterministic transcript read straight from the fixture text.

    Passing ``verification=True`` builds the second, cross-checking reader. It
    behaves identically except that a fixture may supply a deliberately
    different second reading using a ``||`` separator, for example
    ``FAKE:record 5 mL||record 50 mL``. That makes the disagreement path - the
    one that must never silently pick a winner - fully testable offline.
    """

    def __init__(self, *, verification: bool = False) -> None:
        self._verification = verification
        self.adapter_name = (
            "deterministic-fake-speech-verification"
            if verification
            else "deterministic-fake-speech"
        )
        self.model_name = (
            "fake-speech-verification-v1" if verification else "fake-speech-v1"
        )

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        vocabulary: tuple[str, ...] = (),
    ) -> SpeechResult:
        del content_type, vocabulary
        fixture = _fixture_text(audio)
        primary, separator, secondary = fixture.partition("||")
        transcript = secondary.strip() if (self._verification and separator) else primary.strip()
        if not transcript:
            raise ModelInputError("FAKE: fixture value cannot be blank")
        return SpeechResult(
            transcript=transcript,
            confidence=1.0,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={"runtime": "deterministic-fixture"},
        )

    def readiness(self) -> AdapterReadiness:
        """Deterministic fixtures need nothing loaded, so every stage is satisfied."""

        return AdapterReadiness(
            ready=True,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="deterministic fixture; no model is loaded",
            dependency_installed=True,
            model_cached=True,
            loaded=True,
            inference_tested=True,
        )


class FakeLabelRecognition:
    adapter_name = "deterministic-fake-label"
    model_name = "fake-label-v1"

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        del content_type
        return LabelResult(
            visible_text=_fixture_text(image),
            confidence=1.0,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={"runtime": "deterministic-fixture"},
        )

    def readiness(self) -> AdapterReadiness:
        """Deterministic fixtures need nothing loaded, so every stage is satisfied."""

        return AdapterReadiness(
            ready=True,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="deterministic fixture; no model is loaded",
            dependency_installed=True,
            model_cached=True,
            loaded=True,
            inference_tested=True,
        )


class FakeTextUnderstanding:
    """The deterministic rule grammar, presented as the fake profile's text model.

    The grammar itself lives in ``rule_grammar`` so the local cascade uses exactly
    the same parser; this class keeps the fake profile's historical identity
    (``fake-text-rules-v1``) so earlier provenance stays comparable.
    """

    adapter_name = "deterministic-rules"
    model_name = "fake-text-rules-v1"

    def __init__(self, wake_phrase: str = "Jarvis") -> None:
        self._wake_phrase = wake_phrase

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        del context
        return TextResult(
            command=parse_rule_command(transcript, wake_phrase=self._wake_phrase).command,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={"runtime": "deterministic-rules"},
        )

    def readiness(self) -> AdapterReadiness:
        """Deterministic fixtures need nothing loaded, so every stage is satisfied."""

        return AdapterReadiness(
            ready=True,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="deterministic rules; no model is loaded",
            dependency_installed=True,
            model_cached=True,
            loaded=True,
            inference_tested=True,
        )
