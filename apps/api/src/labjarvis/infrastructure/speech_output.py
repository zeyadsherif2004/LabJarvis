"""Spoken responses: Kokoro neural text-to-speech, run locally (ADR 0020).

LabJarvis speaks its outcomes so a gloved researcher need not look at the
screen. Kokoro-82M (Apache-2.0) runs here through ONNX Runtime on the CPU,
about three times faster than real time on the project laptop, with no hosted
API and no network after setup. The browser's own speech synthesiser remains
the fallback whenever Kokoro is not installed or fails.

Speech output is presentation, not a record: nothing synthesised is stored,
and no provenance row is written. The text is what the screen already shows.
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import wave
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

from labjarvis.infrastructure.model_errors import ModelUnavailableError
from labjarvis.ports.models import AdapterReadiness

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    sha256: str
    size: int


#: Full precision: on this CPU it is ~3.5x faster than the int8 export
#: (median 1.0 s vs 3.6 s per message, RTF 0.36 vs 1.26; see ADR 0020).
KOKORO_MODEL = ModelFile(
    "kokoro-v1.0.onnx",
    "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    325_532_387,
)
KOKORO_VOICES = ModelFile(
    "voices-v1.0.bin",
    "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    28_214_398,
)
KOKORO_FILES = (KOKORO_MODEL, KOKORO_VOICES)
#: English voices offered in Settings (American ``a*``, British ``b*``).
ENGLISH_VOICE_PREFIXES = ("af_", "am_", "bf_", "bm_")
DEFAULT_KOKORO_VOICE = "af_heart"
MAX_SYNTHESIS_CHARACTERS = 1000


@dataclass(frozen=True, slots=True)
class SynthesizedSpeech:
    wav: bytes
    sample_rate: int
    duration_ms: int


class SpeechSynthesisPort(Protocol):
    engine: str
    model_name: str
    voice: str

    def readiness(self) -> AdapterReadiness: ...

    def voices(self) -> list[str]: ...

    def synthesize(self, text: str, *, voice: str | None = None) -> SynthesizedSpeech: ...

    def warm_up(self) -> None: ...

    def close(self) -> None: ...


def _default_kokoro_factory(model_path: str, voices_path: str) -> Any:
    from kokoro_onnx import Kokoro

    return Kokoro(model_path, voices_path)


def _wav(samples: Any, sample_rate: int) -> bytes:
    import numpy as np

    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
    return buffer.getvalue()


class KokoroSpeechSynthesis:
    """Kokoro through ONNX Runtime, loaded lazily and used one request at a time."""

    engine = "kokoro"
    adapter_name = "kokoro-onnx"

    def __init__(
        self,
        *,
        cache_dir: Path,
        voice: str = DEFAULT_KOKORO_VOICE,
        speed: float = 1.0,
        factory: Callable[[str, str], Any] = _default_kokoro_factory,
    ) -> None:
        self.model_name = KOKORO_MODEL.name
        self.voice = voice
        self._speed = speed
        self._cache_dir = cache_dir
        self._factory = factory
        self._model: Any = None
        # One synthesis at a time: ONNX Runtime already uses every core, and
        # queueing keeps each utterance's latency predictable.
        self._lock = threading.Lock()

    def _path(self, file: ModelFile) -> Path:
        return self._cache_dir / file.name

    def _cached(self) -> bool:
        return all(self._path(file).is_file() for file in KOKORO_FILES)

    def readiness(self) -> AdapterReadiness:
        installed = find_spec("kokoro_onnx") is not None
        cached = self._cached()
        voice_known = cached and self.voice in self.voices()
        if not installed:
            detail = (
                "Kokoro is not installed; the browser voice is used. Run "
                "`python scripts/tasks.py models` to install it."
            )
        elif not cached:
            detail = (
                "Kokoro model files are missing; the browser voice is used. Run "
                "`python scripts/setup_models.py kokoro` to download them."
            )
        elif not voice_known:
            detail = f"Kokoro has no voice named {self.voice}; choose another in Settings."
        else:
            detail = "Kokoro speaks responses on this computer."
        ready = installed and voice_known
        return AdapterReadiness(
            ready=ready,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=detail,
            dependency_installed=installed,
            model_cached=cached,
            loaded=self._model is not None,
        )

    def voices(self) -> list[str]:
        """Voice names in the voices file (an npz archive), without loading it."""

        try:
            with zipfile.ZipFile(self._path(KOKORO_VOICES)) as archive:
                names = [name.removesuffix(".npy") for name in archive.namelist()]
        except (OSError, zipfile.BadZipFile):
            return []
        return sorted(name for name in names if name.startswith(ENGLISH_VOICE_PREFIXES))

    def _load(self) -> Any:
        if self._model is None:
            if not self._cached():
                raise ModelUnavailableError("Kokoro model files are not installed")
            try:
                self._model = self._factory(
                    str(self._path(KOKORO_MODEL)), str(self._path(KOKORO_VOICES))
                )
            except ImportError as exc:
                raise ModelUnavailableError("Kokoro is not installed") from exc
        return self._model

    def warm_up(self) -> None:
        with self._lock:
            model = self._load()
            model.create("Ready.", voice=self.voice, speed=self._speed)

    def synthesize(self, text: str, *, voice: str | None = None) -> SynthesizedSpeech:
        chosen = voice or self.voice
        if chosen not in self.voices():
            raise ModelUnavailableError(f"Kokoro has no voice named {chosen}")
        with self._lock:
            samples, sample_rate = self._load().create(
                text[:MAX_SYNTHESIS_CHARACTERS], voice=chosen, speed=self._speed
            )
        return SynthesizedSpeech(
            wav=_wav(samples, sample_rate),
            sample_rate=sample_rate,
            duration_ms=round(len(samples) * 1000 / sample_rate),
        )

    def close(self) -> None:
        self._model = None


class BrowserSpeechOutput:
    """The browser speaks; the server synthesises nothing."""

    engine = "browser"
    model_name = "browser speech synthesis"
    voice = ""

    def readiness(self) -> AdapterReadiness:
        return AdapterReadiness(
            ready=True,
            adapter="browser",
            model_name=self.model_name,
            detail="The browser's own speech synthesiser speaks responses.",
            configured=True,
        )

    def voices(self) -> list[str]:
        return []

    def synthesize(self, text: str, *, voice: str | None = None) -> SynthesizedSpeech:
        raise ModelUnavailableError("server speech synthesis is not enabled")

    def warm_up(self) -> None:
        return None

    def close(self) -> None:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_kokoro(cache_dir: Path, *, progress: Callable[[str], None] = print) -> None:
    """Fetch the pinned model files, verifying size and SHA-256 before use.

    A partial or tampered download never replaces a good file: data goes to a
    ``.part`` file that is renamed only after its digest matches the pin.
    """

    import httpx

    cache_dir.mkdir(parents=True, exist_ok=True)
    for file in KOKORO_FILES:
        target = cache_dir / file.name
        if target.is_file() and _sha256(target) == file.sha256:
            progress(f"{file.name}: already present and verified")
            continue
        partial = target.with_name(target.name + ".part")
        progress(f"{file.name}: downloading {file.size / 1_000_000:.0f} MB")
        with httpx.stream(
            "GET", KOKORO_RELEASE + file.name, follow_redirects=True, timeout=60
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
        if partial.stat().st_size != file.size or _sha256(partial) != file.sha256:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"{file.name} failed verification; nothing was installed")
        os.replace(partial, target)
        progress(f"{file.name}: verified")
