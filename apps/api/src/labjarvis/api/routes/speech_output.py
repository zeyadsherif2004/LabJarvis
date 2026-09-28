"""Spoken responses rendered by Kokoro on this computer (ADR 0020).

The browser sends text it is about to speak and receives a WAV. Nothing is
stored or recorded: speech output is presentation, not part of the lab record.
A 409/503 tells the browser to speak with its own synthesiser instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from labjarvis.api.errors import ApiError
from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.infrastructure.speech_output import (
    MAX_SYNTHESIS_CHARACTERS,
    KokoroSpeechSynthesis,
)

router = APIRouter(prefix="/api/v1/speech-output", tags=["speech output"])


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_SYNTHESIS_CHARACTERS)
    #: Only for previewing a voice in Settings; otherwise the configured voice.
    voice: str | None = Field(default=None, pattern=r"^[a-z]{2}_[a-z]+$")


@router.post("/synthesize", response_class=Response)
def synthesize(request: Request, body: SynthesisRequest) -> Response:
    settings = request.app.state.settings
    kokoro: KokoroSpeechSynthesis = request.app.state.kokoro
    if settings.speech_output != "kokoro" and body.voice is None:
        raise ApiError(
            409, "SPEECH_OUTPUT_BROWSER", "responses are spoken by the browser's own voice"
        )
    if not kokoro.readiness().ready and body.voice is None:
        raise ApiError(503, "SPEECH_OUTPUT_UNAVAILABLE", kokoro.readiness().detail)
    try:
        speech = kokoro.synthesize(body.text, voice=body.voice)
    except ModelUnavailableError as exc:
        raise ApiError(503, "SPEECH_OUTPUT_UNAVAILABLE", str(exc)) from exc
    return Response(
        content=speech.wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "X-Audio-Duration-Ms": str(speech.duration_ms)},
    )
