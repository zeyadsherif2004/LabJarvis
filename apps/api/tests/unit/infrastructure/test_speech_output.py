"""Kokoro local voice output: adapter, verified setup, endpoint and Settings.

A stand-in model replaces the 325 MB ONNX graph so these run anywhere; the
real model is exercised by ``scripts/setup_models.py`` and the manual check in
ADR 0020.
"""

from __future__ import annotations

import io
import threading
import wave
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from labjarvis.api.app import create_app
from labjarvis.infrastructure import speech_output
from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.infrastructure.speech_output import (
    KOKORO_MODEL,
    KOKORO_VOICES,
    KokoroSpeechSynthesis,
    ModelFile,
    download_kokoro,
)
from labjarvis.saved_configuration import (
    PRESETS,
    ModelConfiguration,
    project,
    to_settings_values,
)
from labjarvis.settings import Settings, settings_with_saved_values


class FakeKokoro:
    def __init__(self, model_path: str, voices_path: str) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.overlap = False

    def create(self, text: str, voice: str, speed: float = 1.0) -> tuple[Any, int]:
        import numpy as np

        self.active += 1
        self.overlap = self.overlap or self.active > 1
        self.calls.append((text, voice))
        self.active -= 1
        return np.linspace(-1.0, 1.0, 2400, dtype=np.float32), 24000


def install(cache: Path, voices: tuple[str, ...] = ("af_heart", "bm_george", "jf_alpha")) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / KOKORO_MODEL.name).write_bytes(b"onnx")
    with zipfile.ZipFile(cache / KOKORO_VOICES.name, "w") as archive:
        for name in voices:
            archive.writestr(f"{name}.npy", b"")


def adapter(cache: Path, **kwargs: Any) -> tuple[KokoroSpeechSynthesis, list[FakeKokoro]]:
    made: list[FakeKokoro] = []

    def factory(model: str, voices: str) -> FakeKokoro:
        made.append(FakeKokoro(model, voices))
        return made[-1]

    return KokoroSpeechSynthesis(cache_dir=cache, factory=factory, **kwargs), made


# --------------------------------------------------------------------- adapter


def test_missing_files_are_reported_and_nothing_is_loaded(tmp_path: Path) -> None:
    kokoro, made = adapter(tmp_path)

    readiness = kokoro.readiness()

    assert readiness.ready is False
    assert readiness.model_cached is False
    assert "setup_models.py kokoro" in readiness.detail
    with pytest.raises(ModelUnavailableError):
        kokoro.synthesize("Hello.")
    assert made == []


def test_synthesizes_a_mono_16_bit_wav_and_loads_once(tmp_path: Path) -> None:
    install(tmp_path)
    kokoro, made = adapter(tmp_path, voice="af_heart")

    assert kokoro.readiness().ready is True
    first = kokoro.synthesize("Step 3 is complete.")
    kokoro.synthesize("Say next.", voice="bm_george")

    with wave.open(io.BytesIO(first.wav)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        assert audio.getnframes() == 2400
    assert first.duration_ms == 100
    assert len(made) == 1
    assert made[0].calls == [("Step 3 is complete.", "af_heart"), ("Say next.", "bm_george")]
    assert kokoro.readiness().loaded is True


def test_only_english_voices_are_offered_and_unknown_voices_refused(tmp_path: Path) -> None:
    install(tmp_path)
    kokoro, _ = adapter(tmp_path, voice="zz_nobody")

    assert kokoro.voices() == ["af_heart", "bm_george"]
    assert kokoro.readiness().ready is False
    assert "no voice named zz_nobody" in kokoro.readiness().detail
    with pytest.raises(ModelUnavailableError):
        kokoro.synthesize("Hello.", voice="jf_alpha")


def test_one_synthesis_at_a_time(tmp_path: Path) -> None:
    install(tmp_path)
    kokoro, made = adapter(tmp_path)
    threads = [threading.Thread(target=kokoro.synthesize, args=(f"s{i}.",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(made[0].calls) == 8
    assert made[0].overlap is False


# ---------------------------------------------------------------------- setup


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, _: int) -> list[bytes]:
        return [self.body]


def test_download_installs_only_files_matching_the_pinned_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    import httpx

    good = b"genuine model"
    pinned = ModelFile("model.onnx", hashlib.sha256(good).hexdigest(), len(good))
    monkeypatch.setattr(speech_output, "KOKORO_FILES", (pinned,))
    served = {"body": b"tampered model"}
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeResponse(served["body"]))

    with pytest.raises(RuntimeError, match="failed verification"):
        download_kokoro(tmp_path, progress=lambda _: None)
    assert list(tmp_path.iterdir()) == []

    served["body"] = good
    download_kokoro(tmp_path, progress=lambda _: None)
    assert (tmp_path / "model.onnx").read_bytes() == good
    assert not (tmp_path / "model.onnx.part").exists()


# ------------------------------------------------------------- API and Settings


def app_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        app_env="test",
        data_dir=tmp_path,
        model_cache_dir=tmp_path / "models",
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'voice.db').as_posix()}",
        **overrides,
    )


def test_browser_voice_is_the_default_and_the_endpoint_says_so(tmp_path: Path) -> None:
    with TestClient(create_app(app_settings(tmp_path), create_schema=True)) as client:
        ready = client.get("/health/ready").json()["speech_output"]
        refused = client.post("/api/v1/speech-output/synthesize", json={"text": "Hello."})

    assert ready == {"engine": "browser", "voice": "", "ready": True, "detail": ""}
    assert refused.status_code == 409
    assert refused.json()["code"] == "SPEECH_OUTPUT_BROWSER"


def test_kokoro_without_its_model_reports_it_and_tells_the_browser_to_speak(
    tmp_path: Path,
) -> None:
    settings = app_settings(tmp_path, speech_output="kokoro")
    with TestClient(create_app(settings, create_schema=True)) as client:
        ready = client.get("/health/ready").json()["speech_output"]
        refused = client.post("/api/v1/speech-output/synthesize", json={"text": "Hello."})

    assert ready["engine"] == "kokoro" and ready["ready"] is False
    assert refused.status_code == 503
    assert refused.json()["code"] == "SPEECH_OUTPUT_UNAVAILABLE"


def test_kokoro_speaks_through_the_api_and_refuses_anything_but_text(
    tmp_path: Path,
) -> None:
    install(tmp_path / "models" / "kokoro")
    settings = app_settings(tmp_path, speech_output="kokoro", kokoro_voice="bm_george")
    app = create_app(settings, create_schema=True)
    fake, _ = adapter(tmp_path / "models" / "kokoro", voice="bm_george")
    app.state.kokoro = fake
    with TestClient(app) as client:
        assert client.get("/health/ready").json()["speech_output"]["ready"] is True
        spoken = client.post("/api/v1/speech-output/synthesize", json={"text": "Step 3."})
        preview = client.post(
            "/api/v1/speech-output/synthesize", json={"text": "Hi.", "voice": "af_heart"}
        )
        extra = client.post(
            "/api/v1/speech-output/synthesize", json={"text": "x", "path": "C:/model.onnx"}
        )
        too_long = client.post("/api/v1/speech-output/synthesize", json={"text": "x" * 1001})

    assert spoken.status_code == 200
    assert spoken.headers["content-type"] == "audio/wav"
    assert spoken.headers["cache-control"] == "no-store"
    assert spoken.content[:4] == b"RIFF"
    assert preview.status_code == 200
    assert extra.status_code == 422
    assert too_long.status_code == 422


def test_saved_configuration_recommends_kokoro_and_maps_to_startup_settings(
    tmp_path: Path,
) -> None:
    configuration = ModelConfiguration(profile="local", **PRESETS["local"])  # type: ignore[arg-type]
    assert configuration.voice_output == "kokoro"
    assert configuration.voice == "af_heart"

    chosen = configuration.model_copy(update={"voice_output": "kokoro", "voice": "bf_emma"})
    loaded = settings_with_saved_values(to_settings_values(chosen))

    assert (loaded.speech_output, loaded.kokoro_voice) == ("kokoro", "bf_emma")
    assert project(loaded)["voice_output"] == "kokoro"
    assert project(loaded)["voice"] == "bf_emma"
    assert Settings().speech_output == "browser"  # outside Settings: unchanged default
