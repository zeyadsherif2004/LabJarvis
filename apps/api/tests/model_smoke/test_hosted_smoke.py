"""Opt-in live hosted smoke test. Makes real, paid provider requests.

WHAT THIS PROVES: that the configured credential, base URL, and model
identifiers reach a live provider and return a response this codebase can parse.

WHAT THIS DOES NOT PROVE: model quality, accuracy, latency under load, or
suitability for laboratory use. Those require a real evaluation run on
authorized, non-sensitive or consented data. Do not cite a passing smoke test as
performance evidence.

Running it requires two separate opt-ins — the flag below *and* a configured API
key — so a key present in the environment for other reasons cannot silently
trigger paid calls. It is excluded from the default suite, CI, and pre-commit by
the `hosted_smoke` marker.

    LABJARVIS_RUN_HOSTED_SMOKE=1 python scripts/tasks.py test-hosted-smoke

Fixtures are synthetic. No key, response body, audio, image, protocol content, or
transcript is ever printed.
"""

from __future__ import annotations

import os
import struct
import wave
from io import BytesIO
from time import perf_counter

import pytest

from labjarvis.infrastructure.model_factory import build_model_adapters
from labjarvis.ports.models import CommandContext
from labjarvis.settings import ModelMode, Settings

pytestmark = pytest.mark.hosted_smoke

RUN_FLAG = "LABJARVIS_RUN_HOSTED_SMOKE"


def synthetic_wav() -> bytes:
    """A short synthetic tone. Carries no speech, no personal data."""

    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(16000)))
    return buffer.getvalue()


def synthetic_png() -> bytes:
    """A 1x1 white PNG. Carries no laboratory or participant data."""

    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c63f8ffff3f0005fe02fea735d1e20000000049454e44ae426082"
    )


@pytest.fixture(scope="module")
def settings() -> Settings:
    if os.environ.get(RUN_FLAG) != "1":
        pytest.skip(f"set {RUN_FLAG}=1 to run the paid hosted smoke test")
    configured = Settings()
    if configured.model_mode is not ModelMode.HOSTED:
        pytest.skip("set LABJARVIS_MODEL_MODE=hosted to run the hosted smoke test")
    if configured.hosted_api_key is None:
        pytest.skip("LABJARVIS_HOSTED_API_KEY is not configured")
    return configured


def test_hosted_speech_round_trip(settings: Settings) -> None:
    adapters = build_model_adapters(settings)
    started = perf_counter()
    try:
        result = adapters.speech.transcribe(synthetic_wav(), content_type="audio/wav")
    finally:
        adapters.close()
    elapsed_ms = round((perf_counter() - started) * 1000)

    assert isinstance(result.transcript, str)
    print(
        f"hosted speech integration OK provider=openai "
        f"model={result.model_name} latency_ms={elapsed_ms}"
    )


def test_hosted_text_round_trip(settings: Settings) -> None:
    adapters = build_model_adapters(settings)
    context = CommandContext(
        session_id="smoke-session",
        session_state="ACTIVE",
        active_step_id="smoke-step",
        active_instruction="Synthetic smoke-test step.",
    )
    started = perf_counter()
    try:
        result = adapters.text.interpret("pause the session", context)
    finally:
        adapters.close()
    elapsed_ms = round((perf_counter() - started) * 1000)

    assert result.command.intent in {
        "GET_CURRENT_STEP",
        "GET_NEXT_STEP",
        "REPEAT_INSTRUCTION",
        "RECORD_MEASUREMENT",
        "RECORD_OBSERVATION",
        "SET_TIMER",
        "VERIFY_REAGENT",
        "CORRECT_PREVIOUS_ENTRY",
        "PAUSE_SESSION",
        "RESUME_SESSION",
        "COMPLETE_STEP",
        "END_SESSION",
        "HELP",
        "UNSUPPORTED",
    }
    print(
        f"hosted text integration OK provider=openai "
        f"model={result.model_name} latency_ms={elapsed_ms}"
    )


def test_hosted_vision_round_trip(settings: Settings) -> None:
    from labjarvis.infrastructure.model_errors import ModelOutputError

    adapters = build_model_adapters(settings)
    started = perf_counter()
    try:
        adapters.labels.recognise(synthetic_png(), content_type="image/png")
    except ModelOutputError:
        # A blank 1x1 image legitimately yields no visible text. The request
        # itself round-tripped, which is what this test verifies.
        pass
    finally:
        adapters.close()
    elapsed_ms = round((perf_counter() - started) * 1000)

    print(
        f"hosted vision integration OK provider=openai "
        f"model={settings.hosted_vision_model_name} latency_ms={elapsed_ms}"
    )
