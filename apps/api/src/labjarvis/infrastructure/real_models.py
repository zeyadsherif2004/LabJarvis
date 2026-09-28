"""Lazy local adapters for the three pre-trained model domains."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar, Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from labjarvis.domain.speech_vocabulary import vocabulary_sha256
from labjarvis.infrastructure.command_contract import (
    CONTRACT_VERSION,
    grammar_command_json_schema,
    parse_model_command,
    schema_sha256,
    to_parsed_command,
)
from labjarvis.infrastructure.interpreter_spec import (
    PROMPT_SHA256,
    PROMPT_VERSION,
    interpreter_messages,
)
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelInputError,
    ModelOutputError,
    ModelUnavailableError,
)
from labjarvis.infrastructure.speech_conditioning import protocol_hotwords, protocol_prompt
from labjarvis.infrastructure.text_model_profiles import TextModelProfile, resolve_text_profile
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    LabelResult,
    SpeechResult,
    TextResult,
)


class _WhisperSegment(Protocol):
    text: str
    avg_logprob: float


class _WhisperInfo(Protocol):
    language_probability: float


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: str,
        *,
        language: str,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
        hotwords: str | None = None,
        initial_prompt: str | None = None,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]: ...


class _WhisperFactory(Protocol):
    def __call__(self, **kwargs: object) -> _WhisperModel: ...


class _EasyReader(Protocol):
    def readtext(
        self,
        image: bytes,
        *,
        detail: int,
        paragraph: bool,
    ) -> list[tuple[object, str, float]]: ...


class _EasyReaderFactory(Protocol):
    def __call__(self, **kwargs: object) -> _EasyReader: ...


def _cache_has_content(cache_dir: Path) -> bool:
    """Whether a model cache directory holds any downloaded weight file.

    Used to distinguish "the dependency is installed" from "the weights are
    present", which are very different answers for an operator standing at a
    bench waiting for the first utterance to work.
    """

    try:
        return any(entry.is_file() and entry.stat().st_size > 0 for entry in cache_dir.rglob("*"))
    except OSError:  # pragma: no cover - unreadable cache is reported as absent
        return False


def register_cuda_libraries() -> list[str]:
    """Make pip-installed NVIDIA runtime DLLs visible to CTranslate2 on Windows.

    CTranslate2 (faster-whisper) links against CUDA 12 cuBLAS and cuDNN 9. On
    Windows these come from the ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``
    wheels, whose ``bin`` folders are not on the DLL search path by default.
    Returns the directories registered (empty elsewhere or when absent).
    """

    import os
    import sys

    if sys.platform != "win32":
        return []
    # When the CUDA build of PyTorch is installed (EasyOCR on GPU), load it
    # first: its cuDNN 9 is then shared by CTranslate2. Loading the separate
    # CUDA-12 cuDNN wheel first instead made EasyOCR fail in the same process
    # with CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH (measured 2026-09-27).
    torch_loaded = False
    if find_spec("torch") is not None:
        import_module("torch")
        torch_loaded = True
    registered: list[str] = []
    spec = find_spec("nvidia")
    roots = list(spec.submodule_search_locations or []) if spec is not None else []
    for root in roots:
        for binary in sorted(Path(root).glob("*/bin")):
            if torch_loaded and "cudnn" in binary.parent.name:
                continue
            os.add_dll_directory(str(binary))
            os.environ["PATH"] = f"{binary}{os.pathsep}{os.environ.get('PATH', '')}"
            registered.append(str(binary))
    return registered


def _default_whisper_factory(**kwargs: object) -> _WhisperModel:
    if str(kwargs.get("device", "cpu")).startswith("cuda"):
        register_cuda_libraries()
    try:
        module = import_module("faster_whisper")
        factory = cast(_WhisperFactory, module.WhisperModel)
        return factory(**kwargs)
    except (ImportError, OSError) as exc:
        raise ModelUnavailableError(
            "faster-whisper is unavailable; install the 'models' dependency group"
        ) from exc


def _default_easyocr_factory(**kwargs: object) -> _EasyReader:
    try:
        module = import_module("easyocr")
        factory = cast(_EasyReaderFactory, module.Reader)
        return factory(**kwargs)
    except (ImportError, OSError) as exc:
        raise ModelUnavailableError(
            "EasyOCR is unavailable; install the 'models' dependency group"
        ) from exc


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    content: str


class _OllamaResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    message: _OllamaMessage


class FasterWhisperSpeechToText:
    """Transcribe bounded local audio with a lazily loaded faster-whisper model."""

    adapter_name = "faster-whisper"
    _suffixes: ClassVar[dict[str, str]] = {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
    }

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        cache_dir: Path,
        language: str = "en",
        beam_size: int = 5,
        allow_download: bool = False,
        conditioning: str = "off",
        model_factory: _WhisperFactory = _default_whisper_factory,
    ) -> None:
        if conditioning not in {"off", "prompt", "hotwords"}:
            raise ValueError(f"unknown speech conditioning: {conditioning}")
        self.model_name = model_name
        self._conditioning = conditioning
        self._device = device
        self._compute_type = compute_type
        self._cache_dir = cache_dir
        self._language = language
        self._beam_size = beam_size
        self._allow_download = allow_download
        self._model_factory = model_factory
        self._model: _WhisperModel | None = None

    def _load(self) -> _WhisperModel:
        if self._model is None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._model = self._model_factory(
                    model_size_or_path=self.model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=str(self._cache_dir),
                    local_files_only=not self._allow_download,
                )
            except ModelUnavailableError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise ModelUnavailableError(
                    "the configured faster-whisper model could not be loaded locally"
                ) from exc
        return self._model

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        vocabulary: tuple[str, ...] = (),
    ) -> SpeechResult:
        media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
        suffix = self._suffixes.get(media_type)
        if suffix is None:
            raise ModelInputError("unsupported audio type")
        if not audio:
            raise ModelInputError("audio cannot be empty")

        conditioning: dict[str, str] = {}
        if vocabulary and self._conditioning == "hotwords":
            conditioning["hotwords"] = protocol_hotwords(vocabulary)
        elif vocabulary and self._conditioning == "prompt":
            conditioning["initial_prompt"] = protocol_prompt(vocabulary)
        with TemporaryDirectory(prefix="labjarvis-audio-") as temporary:
            audio_path = Path(temporary) / f"input{suffix}"
            audio_path.write_bytes(audio)
            try:
                segments, info = self._load().transcribe(
                    str(audio_path),
                    language=self._language,
                    beam_size=self._beam_size,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    **conditioning,
                )
                materialised = list(segments)
            except ModelUnavailableError:
                raise
            except RuntimeError as exc:
                if any(marker in str(exc) for marker in (".dll", "CUDA", "cuDNN", "cublas")):
                    raise ModelUnavailableError(
                        "the GPU runtime libraries for the speech model are missing; "
                        "install them or use LABJARVIS_SPEECH_DEVICE=cpu"
                    ) from exc
                raise ModelInputError("audio could not be decoded or transcribed") from exc
            except (OSError, ValueError) as exc:
                raise ModelInputError("audio could not be decoded or transcribed") from exc

        transcript = " ".join(segment.text.strip() for segment in materialised).strip()
        if not transcript:
            raise ModelOutputError("speech model returned no transcript")
        log_probabilities = [segment.avg_logprob for segment in materialised]
        confidence = None
        if log_probabilities:
            mean_log_probability = sum(log_probabilities) / len(log_probabilities)
            confidence = max(0.0, min(1.0, math.exp(mean_log_probability)))
        elif math.isfinite(info.language_probability):
            confidence = max(0.0, min(1.0, info.language_probability))
        return SpeechResult(
            transcript=transcript,
            confidence=confidence,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={
                "provider": "faster-whisper",
                "locality": "local",
                "device": self._device,
                "compute_type": self._compute_type,
                "language": self._language,
                "beam_size": str(self._beam_size),
                "conditioning": self._conditioning if vocabulary else "off",
                "vocabulary_sha256": vocabulary_sha256(vocabulary) if vocabulary else "",
            },
        )

    def warm_up(self) -> None:
        """Load the model so the first utterance does not pay the load time."""

        self._load()

    def readiness(self) -> AdapterReadiness:
        """Distinguish "the package exists" from "this model can transcribe".

        Whisper weights are downloaded into the configured cache. Reporting the
        cache stage separately is what stops the UI announcing speech is ready
        when the first utterance would in fact fail on a missing download.
        """

        dependency = self._model is not None or find_spec("faster_whisper") is not None
        loaded = self._model is not None
        cached = loaded or _cache_has_content(self._cache_dir)
        if not dependency:
            detail = "install the optional model dependencies: apps/api[models]"
        elif loaded:
            detail = "loaded and ready"
        elif cached:
            detail = "weights are cached; the model loads on first use"
        elif self._allow_download:
            detail = "weights are not cached; the first use will download them"
        else:
            detail = "weights are not cached and downloads are disabled"
        return AdapterReadiness(
            ready=dependency and (cached or self._allow_download),
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=detail,
            dependency_installed=dependency,
            model_cached=cached,
            loaded=loaded,
            inference_tested=loaded,
        )


class OllamaTextUnderstanding:
    """Extract one allowlisted command through Ollama grammar-constrained output.

    The request carries the interpreter specification, the development-only
    few-shot examples, the discriminated ``oneOf`` schema as Ollama's ``format``
    (compiled into a decoding grammar), and the model's explicit profile -
    including ``think: false`` for thinking-capable models.
    """

    adapter_name = "ollama-json-schema"

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 45,
        context_tokens: int = 4096,
        wake_phrase: str = "Jarvis",
        profile: TextModelProfile | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model_name = model_name
        self._profile = profile or resolve_text_profile(
            model_name,
            timeout_seconds=timeout_seconds,
            context_tokens=context_tokens,
        )
        self._health_timeout = min(self._profile.timeout_seconds, 2.0)
        self._wake_phrase = wake_phrase
        self._client = client or httpx.Client(
            base_url=base_url, timeout=self._profile.timeout_seconds
        )
        self._warm = False

    @property
    def profile(self) -> TextModelProfile:
        return self._profile

    def request_payload(self, transcript: str, context: CommandContext) -> dict[str, object]:
        """The exact request body, exposed so tests and provenance can inspect it."""

        payload: dict[str, object] = {
            "model": self.model_name,
            "messages": interpreter_messages(
                transcript,
                context,
                wake_phrase=self._wake_phrase,
                few_shot=self._profile.few_shot,
            ),
            "stream": False,
            "options": {
                "temperature": self._profile.temperature,
                "num_ctx": self._profile.context_tokens,
                "num_predict": self._profile.max_output_tokens,
            },
            "keep_alive": "30m",
        }
        if self._profile.structured_output == "json_schema":
            payload["format"] = grammar_command_json_schema()
        elif self._profile.structured_output == "json_object":
            payload["format"] = "json"
        if self._profile.think is not None:
            payload["think"] = self._profile.think
        return payload

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        payload = self.request_payload(transcript, context)
        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
            envelope = _OllamaResponse.model_validate(body)
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(
                "the local text model did not answer within its configured timeout"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ModelUnavailableError("the local Ollama model service is unavailable") from exc
        except ValidationError as exc:
            raise ModelOutputError("the local Ollama response had an unexpected shape") from exc
        try:
            command = parse_model_command(envelope.message.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ModelOutputError("text model output failed the command schema") from exc
        parsed, basis = to_parsed_command(command, transcript)
        self._warm = True
        metadata = {
            "provider": "ollama",
            "locality": "local",
            "api_operation": "chat",
            "interpreter": "llm",
            "contract_version": CONTRACT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": PROMPT_SHA256,
            "schema_sha256": schema_sha256(),
            "confidence_basis": basis,
            **self._profile.provenance(),
        }
        if isinstance(body, dict):
            for key in ("eval_count", "prompt_eval_count", "total_duration", "load_duration"):
                value = body.get(key)
                if isinstance(value, int):
                    metadata[f"ollama_{key}"] = str(value)
        return TextResult(
            command=parsed,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata=metadata,
        )

    def warm_up(self) -> None:
        """Load the model into memory without interpreting anything."""

        payload: dict[str, object] = {
            "model": self.model_name,
            "messages": [],
            "keep_alive": "30m",
            "stream": False,
        }
        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelUnavailableError("the local Ollama model could not be loaded") from exc
        self._warm = True

    def readiness(self) -> AdapterReadiness:
        """Ask the local service whether this exact model is installed."""

        try:
            response = self._client.get("/api/tags", timeout=self._health_timeout)
            response.raise_for_status()
            payload = response.json()
            names = {
                item.get("name")
                for item in payload.get("models", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            reachable = True
            installed = self.model_name in names
            detail = (
                "model installed and the service is reachable"
                if installed
                else f"Ollama is running but {self.model_name} is not pulled"
            )
        except (httpx.HTTPError, ValueError, AttributeError):
            reachable = False
            installed = False
            detail = "Ollama is not reachable on the configured loopback URL"
        return AdapterReadiness(
            ready=installed,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=detail,
            dependency_installed=True,
            model_cached=installed,
            loaded=installed and self._warm,
            provider_reachable=reachable,
            inference_tested=self._warm if installed else False,
        )

    def close(self) -> None:
        self._client.close()


class EasyOcrLabelRecognition:
    """Read visible reagent-label text with a lazily loaded EasyOCR model."""

    adapter_name = "easyocr"
    _supported_types: ClassVar[set[str]] = {"image/jpeg", "image/png", "image/webp"}

    def __init__(
        self,
        *,
        languages: tuple[str, ...],
        gpu: bool,
        cache_dir: Path,
        model_name: str = "english_g2",
        allow_download: bool = False,
        reader_factory: _EasyReaderFactory = _default_easyocr_factory,
    ) -> None:
        self.model_name = model_name
        self._languages = languages
        self._gpu = gpu
        self._cache_dir = cache_dir
        self._allow_download = allow_download
        self._reader_factory = reader_factory
        self._reader: _EasyReader | None = None

    def _load(self) -> _EasyReader:
        if self._reader is None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._reader = self._reader_factory(
                    lang_list=list(self._languages),
                    gpu=self._gpu,
                    model_storage_directory=str(self._cache_dir),
                    download_enabled=self._allow_download,
                    recog_network=self.model_name,
                    verbose=False,
                )
            except ModelUnavailableError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                raise ModelUnavailableError(
                    "the configured EasyOCR model could not be loaded locally"
                ) from exc
        return self._reader

    def warm_up(self) -> None:
        """Load the OCR networks so the first label check does not pay the load time."""

        self._load()

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
        if media_type not in self._supported_types:
            raise ModelInputError("unsupported image type")
        if not image:
            raise ModelInputError("image cannot be empty")
        try:
            detections = self._load().readtext(image, detail=1, paragraph=False)
        except ModelUnavailableError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ModelInputError("image could not be decoded or processed") from exc
        visible = " ".join(text.strip() for _, text, _ in detections if text.strip())
        confidence_values = [confidence for _, text, confidence in detections if text.strip()]
        confidence = None
        if confidence_values:
            confidence = max(0.0, min(1.0, sum(confidence_values) / len(confidence_values)))
        return LabelResult(
            visible_text=visible,
            confidence=confidence,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={
                "provider": "easyocr",
                "locality": "local",
                "device": "gpu" if self._gpu else "cpu",
                "languages": ",".join(self._languages),
                "recognition_network": self.model_name,
            },
        )

    def readiness(self) -> AdapterReadiness:
        """Report the OCR cache separately from the installed package."""

        dependency = self._reader is not None or find_spec("easyocr") is not None
        loaded = self._reader is not None
        cached = loaded or _cache_has_content(self._cache_dir)
        if not dependency:
            detail = "install the optional model dependencies: apps/api[models]"
        elif loaded:
            detail = "loaded and ready"
        elif cached:
            detail = "detector weights are cached; the model loads on first use"
        elif self._allow_download:
            detail = "detector weights are not cached; the first use will download them"
        else:
            detail = "detector weights are not cached and downloads are disabled"
        return AdapterReadiness(
            ready=dependency and (cached or self._allow_download),
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=detail,
            dependency_installed=dependency,
            model_cached=cached,
            loaded=loaded,
            inference_tested=loaded,
        )
