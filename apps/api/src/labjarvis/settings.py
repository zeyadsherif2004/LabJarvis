"""Validated application configuration with conservative local defaults."""

import json
from collections.abc import Mapping
from contextvars import ContextVar
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ModelMode(StrEnum):
    """Model execution profile."""

    FAKE = "fake"
    LOCAL = "local"
    HOSTED = "hosted"


class HostedProvider(StrEnum):
    """Hosted model provider selected at the composition boundary."""

    OPENAI = "openai"
    #: Any service exposing the OpenAI-compatible HTTP surface (DeepSeek, GLM /
    #: Zhipu, self-hosted gateways, ...). Base URL, model and capabilities are
    #: configuration; nothing about a specific vendor is compiled in.
    OPENAI_COMPATIBLE = "openai_compatible"


class StageProvider(StrEnum):
    """Who runs one model stage (speech, text, or vision)."""

    FAKE = "fake"
    LOCAL = "local"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    #: DeepSeek as its own named provider, with its own key, endpoint and
    #: request options, so it can be configured beside OpenAI and chosen per
    #: start without sharing anything with it. Text stage only.
    DEEPSEEK = "deepseek"


HOSTED_STAGE_PROVIDERS = frozenset(
    {StageProvider.OPENAI, StageProvider.OPENAI_COMPATIBLE, StageProvider.DEEPSEEK}
)
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
Stage = Literal["speech", "text", "vision"]
STAGES: tuple[Stage, ...] = ("speech", "text", "vision")


class TextInterpreter(StrEnum):
    """How an utterance becomes a command proposal."""

    #: The deterministic grammar only.
    RULES = "rules"
    #: The configured language model only (grounded, schema-validated).
    LLM = "llm"
    #: Grammar first; the model only for phrasings the grammar does not know.
    CASCADE = "cascade"
    #: As ``cascade``, and the model must also agree with every grammar match.
    AGREEMENT = "agreement"


class StructuredOutputMode(StrEnum):
    """The strongest structured-output facility a text provider offers."""

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_ONLY = "prompt_only"


class ReasoningEffort(StrEnum):
    """Requested reasoning effort for a hosted reasoning model.

    Sent only when configured, and only values the provider documents for the
    model should be used: a model that does not accept the value rejects the
    request, which fails closed (no proposal) with a configuration message.
    """

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class RealtimeTransport(StrEnum):
    """How the browser reaches the realtime provider."""

    #: Preferred in a browser: the provider handles jitter, echo, and playback.
    WEBRTC = "webrtc"
    #: Fallback for environments where WebRTC is blocked.
    WEBSOCKET = "websocket"


#: Values from the in-app saved configuration, supplied only while
#: :func:`settings_with_saved_values` builds a ``Settings``. Every other
#: construction (tests, evaluation scripts) sees none, so research runs stay
#: reproducible from their explicit arguments and environment alone.
_SAVED_VALUES: ContextVar[Mapping[str, object] | None] = ContextVar(
    "labjarvis_saved_values", default=None
)


class _SavedConfigurationSource(PydanticBaseSettingsSource):
    """The in-app saved configuration as one layer of the settings sources."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # pragma: no cover - __call__ is overridden

    def __call__(self) -> dict[str, Any]:
        return dict(_SAVED_VALUES.get() or {})


class Settings(BaseSettings):
    """Central configuration.

    Precedence, highest first: explicit constructor arguments, process
    environment variables (``LABJARVIS_*``), the in-app saved configuration
    (only on the application startup path), the ``.env`` file, then defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="LABJARVIS_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _SavedConfigurationSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    app_env: Literal["development", "test", "production"] = "development"
    model_mode: ModelMode = ModelMode.FAKE
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path(".labjarvis")
    backup_dir: Path | None = None
    model_cache_dir: Path = Path(".labjarvis/models")
    database_url: str = "sqlite+pysqlite:///.labjarvis/labjarvis.db"
    retain_raw_media: bool = False
    retain_transcripts: bool = True
    wake_phrase: str = Field(default="Jarvis", min_length=1, max_length=40)
    allow_model_download: bool = False
    allow_remote_model_service: bool = False
    speech_model_name: str = Field(default="small.en", min_length=1, max_length=200)
    speech_device: str = Field(default="cpu", min_length=1, max_length=40)
    speech_compute_type: str = Field(default="int8", min_length=1, max_length=40)
    speech_language: str = Field(default="en", min_length=2, max_length=12)
    speech_beam_size: int = Field(default=5, ge=1, le=20)
    #: Protocol-conditioned recognition: bias the speech model toward the
    #: approved version's reagent names, aliases and units at inference time.
    speech_conditioning: Literal["off", "prompt", "hotwords"] = "off"
    # Provisional default chosen from the 2026-09-26 sweep (highest held-out
    # accuracy, p95 0.3 s); no configuration met every predeclared criterion.
    text_model_name: str = Field(default="qwen2.5:3b", min_length=1, max_length=200)
    ollama_base_url: str = "http://127.0.0.1:11434"
    text_context_tokens: int = Field(default=4096, ge=1024, le=131072)
    vision_model_name: str = Field(default="english_g2", min_length=1, max_length=200)
    vision_languages: tuple[str, ...] = ("en",)
    vision_gpu: bool = False
    document_ocr_dpi: int = Field(default=150, ge=72, le=300)
    document_ocr_max_page_pixels: int = Field(default=8_000_000, ge=10_000)
    document_ocr_max_total_pixels: int = Field(default=80_000_000, ge=10_000)
    document_min_text_characters_per_page: int = Field(default=8, ge=1, le=1000)
    # --- High-integrity transcription -------------------------------------
    # A second, more accurate reading is taken only for utterances whose
    # exact wording could change the experimental record. Running two
    # transcription systems on every harmless question would double the cost
    # and latency of the interaction for no safety benefit.
    high_integrity_transcription_enabled: bool = False
    high_integrity_transcription_model_name: str = Field(default="", max_length=200)

    # --- Realtime conversational shell -------------------------------------
    # Off by default. The realtime layer is an interface, never a source of
    # truth, so the product must remain fully usable with it disabled.
    realtime_enabled: bool = False
    # No default model identifier is supplied. Provider aliases move over time,
    # and an evaluation result is only reproducible if the exact identifier that
    # produced it was recorded rather than inherited from a drifting alias.
    realtime_model_name: str = Field(default="", max_length=200)
    realtime_transport: RealtimeTransport = RealtimeTransport.WEBRTC
    realtime_voice: str = Field(default="alloy", min_length=1, max_length=60)
    realtime_session_ttl_seconds: int = Field(default=60, ge=10, le=600)
    realtime_max_utterance_seconds: int = Field(default=30, ge=5, le=120)
    # How long the browser records the operator answering a proposal. Short on
    # purpose: a confirmation is a few words, and an open-ended window would
    # keep the microphone recording after the operator has finished speaking.
    realtime_confirmation_seconds: int = Field(default=5, ge=2, le=20)
    realtime_idle_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    allow_hosted_models: bool = False
    hosted_provider: HostedProvider = HostedProvider.OPENAI
    hosted_api_key: SecretStr | None = None
    hosted_base_url: str = "https://api.openai.com/v1"
    hosted_speech_model_name: str = Field(default="", max_length=200)
    hosted_text_model_name: str = Field(default="", max_length=200)
    hosted_vision_model_name: str = Field(default="", max_length=200)
    hosted_max_response_bytes: int = Field(default=1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    hosted_max_retries: int = Field(default=2, ge=0, le=3)

    # --- Named hosted providers ----------------------------------------------
    # OpenAI and DeepSeek each have their own connection, so both can be
    # configured at once and choosing one never sends a request (or a key) to
    # the other. The OpenAI key falls back to LABJARVIS_HOSTED_API_KEY and the
    # OpenAI endpoint to LABJARVIS_HOSTED_BASE_URL when the hosted provider is
    # OpenAI, so existing configurations keep working unchanged.
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = DEEPSEEK_DEFAULT_BASE_URL
    deepseek_text_structured_output: StructuredOutputMode = StructuredOutputMode.JSON_OBJECT
    #: Disables DeepSeek's thinking mode, as in every recorded DeepSeek run.
    deepseek_text_extra_body: str = Field(
        default='{"thinking": {"type": "disabled"}}', max_length=2000
    )

    # --- Per-stage providers (hybrid profiles) ------------------------------
    # ``None`` means "follow model_mode". Setting one stage explicitly lets, for
    # example, local Whisper and local EasyOCR run beside a hosted
    # OpenAI-compatible text model. Every hosted stage still requires
    # LABJARVIS_ALLOW_HOSTED_MODELS, a key and an explicit model identifier.
    speech_provider: StageProvider | None = None
    text_provider: StageProvider | None = None
    vision_provider: StageProvider | None = None
    #: Per-stage endpoint and key overrides; unset stages use hosted_base_url
    #: and hosted_api_key. Keys are secrets and never leave the backend.
    hosted_speech_base_url: str | None = None
    hosted_text_base_url: str | None = None
    hosted_vision_base_url: str | None = None
    hosted_speech_api_key: SecretStr | None = None
    hosted_text_api_key: SecretStr | None = None
    hosted_vision_api_key: SecretStr | None = None
    #: Display names shown truthfully in the UI and provenance (e.g. "DeepSeek").
    hosted_speech_provider_label: str = Field(default="", max_length=60)
    hosted_text_provider_label: str = Field(default="", max_length=60)
    hosted_vision_provider_label: str = Field(default="", max_length=60)
    #: Capability declarations for OpenAI-compatible endpoints. Compatibility of
    #: the chat surface says nothing about audio or images, so both default off.
    hosted_text_structured_output: StructuredOutputMode | None = None
    hosted_vision_image_input: bool = False
    hosted_speech_transcription: bool = False
    #: Provider-specific request fields, as a JSON object merged into each text
    #: request (e.g. a switch that disables a provider's reasoning mode).
    hosted_text_extra_body: str = Field(default="", max_length=2000)
    hosted_text_token_limit_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    hosted_text_max_output_tokens: int = Field(default=256, ge=16, le=4096)
    #: Reasoning effort for a hosted reasoning model (e.g. ``none`` for
    #: gpt-5.4-nano / gpt-5.6-luna). Unset sends nothing (provider default).
    hosted_text_reasoning_effort: ReasoningEffort | None = None

    # --- Text interpretation -------------------------------------------------
    #: ``None`` selects rules in the fake profile and the cascade otherwise.
    text_interpreter: TextInterpreter | None = None
    text_think: Literal["auto", "on", "off"] = "auto"
    #: Per-request timeout for the text model; ``None`` uses the model profile.
    text_timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    text_temperature: float = Field(default=0.0, ge=0, le=2)
    text_max_output_tokens: int = Field(default=128, ge=16, le=4096)
    text_few_shot: bool = True
    #: Optional second model that must agree with every LLM-routed
    #: state-changing proposal in the cascade; empty disables it. It runs on
    #: its own provider: local Ollama (default), OpenAI, or OpenAI-compatible.
    text_verifier_model_name: str = Field(default="", max_length=200)
    text_verifier_provider: StageProvider | None = None
    text_verifier_base_url: str | None = None
    text_verifier_api_key: SecretStr | None = None
    text_verifier_provider_label: str = Field(default="", max_length=60)
    text_verifier_structured_output: StructuredOutputMode | None = None
    text_verifier_reasoning_effort: ReasoningEffort | None = None
    text_verifier_extra_body: str = Field(default="", max_length=2000)

    # --- Study mode (seeded-error confirmation trials) --------------------------
    #: A study script of pre-declared seeded errors. Study mode needs BOTH this
    #: path and the exact acknowledgement phrase, and is refused in production,
    #: so it cannot be switched on by accident.
    study_mode_script: Path | None = None
    study_mode_acknowledgement: str = Field(default="", max_length=80)

    #: Offer the optional in-browser voice-activity listener (wake phrase
    #: required). Off by default; push-to-talk always remains available.
    hands_free_listening_enabled: bool = False
    #: Let the wake-phrase listener also answer a waiting proposal - only with a
    #: value-bearing reply ("Jarvis, confirm 10 millilitres"); a bare "yes"
    #: never commits. Off by default; needs hands-free listening and the
    #: high-integrity transcription cross-check.
    voice_only_confirmation_enabled: bool = False
    #: Optional live "scan until stable" label checking (local OCR only). The
    #: camera is probed every interval until N readable probes among the last
    #: N+2 agree on MATCH, or on both outcome and text otherwise
    #: (stable-asymmetric-v3). Only those agreeing frames form a pending reading
    #: that still needs confirmation. Refused at startup with remote vision.
    live_label_scan_enabled: bool = False
    live_scan_required_consecutive: int = Field(default=3, ge=2, le=10)
    live_scan_interval_ms: int = Field(default=400, ge=100, le=5000)
    live_scan_timeout_seconds: int = Field(default=12, ge=1, le=120)

    # --- Hands-free bench workflow (ADR 0018) --------------------------------
    #: One switch for the whole hands-free profile: listening, autostart,
    #: voice-only confirmation, the high-integrity transcriber (default local
    #: model ``base.en`` only here), warm-up and timer alarms. Local Vision gets
    #: live scanning; hosted Vision gets one voice-triggered timed capture.
    #: Startup is refused when a required local model is missing.
    hands_free_profile: bool = False
    #: Start listening as soon as a session screen opens, after the single
    #: gesture browsers require (none in the documented kiosk launch mode).
    hands_free_autostart: bool = False
    #: Speak and sound an alarm when a timer elapses, repeated until acknowledged.
    timer_alarms_enabled: bool = False
    timer_alarm_repeat_seconds: int = Field(default=20, ge=5, le=300)
    #: Spoken warnings before a timer elapses (seconds remaining), unless muted.
    timer_warning_seconds: tuple[int, ...] = (60, 10)
    #: Opt-in, weaker: "Jarvis, confirm observation" after LabJarvis reads the
    #: dictated text back, accepted only when both transcriptions agreed.
    voice_observations_enabled: bool = False
    #: How much LabJarvis says: ``brief`` (the outcome and the phrase to say) or
    #: ``full`` (everything shown on screen).
    spoken_detail: Literal["brief", "full"] = "full"
    #: Who speaks responses: ``kokoro`` (local neural voice, recommended; see
    #: ADR 0020) or ``browser`` (the browser's own synthesiser, which is also
    #: the automatic fallback whenever Kokoro is unavailable).
    speech_output: Literal["browser", "kokoro"] = "browser"
    kokoro_voice: str = Field(default="af_heart", pattern=r"^[a-z]{2}_[a-z]+$")
    kokoro_speed: float = Field(default=1.0, ge=0.5, le=2.0)

    # --- Warm-up -------------------------------------------------------------
    #: Load expensive local models in the background at startup so the first
    #: utterance does not pay the load time. Never blocks startup.
    warm_models_on_startup: bool = False
    max_document_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_document_pages: int = Field(default=100, ge=1, le=1000)
    max_docx_uncompressed_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_docx_entries: int = Field(default=2000, ge=2, le=20_000)
    max_extracted_characters: int = Field(default=1_000_000, ge=1000)
    max_audio_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    inference_timeout_seconds: float = Field(default=45.0, gt=0, le=600)
    inference_concurrency: int = Field(default=1, ge=1, le=8)
    command_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    reagent_match_threshold: float = Field(default=92.0, ge=0, le=100)
    reagent_uncertain_threshold: float = Field(default=72.0, ge=0, le=100)
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    application_commit: str | None = Field(default=None, max_length=100)

    @field_validator(
        "speech_provider",
        "text_provider",
        "vision_provider",
        "text_interpreter",
        "hosted_text_structured_output",
        "hosted_speech_base_url",
        "hosted_text_base_url",
        "hosted_vision_base_url",
        "hosted_speech_api_key",
        "hosted_text_api_key",
        "hosted_vision_api_key",
        "text_timeout_seconds",
        "study_mode_script",
        "hosted_text_reasoning_effort",
        "openai_api_key",
        "openai_base_url",
        "deepseek_api_key",
        "text_verifier_provider",
        "text_verifier_base_url",
        "text_verifier_api_key",
        "text_verifier_structured_output",
        "text_verifier_reasoning_effort",
        mode="before",
    )
    @classmethod
    def blank_means_unset(cls, value: object) -> object:
        """An empty ``.env`` entry (``LABJARVIS_TEXT_PROVIDER=``) means "not set"."""

        return None if isinstance(value, str) and not value.strip() else value

    #: Everything the hands-free profile switches on.
    HANDS_FREE_PROFILE_FLAGS: ClassVar[tuple[str, ...]] = (
        "hands_free_listening_enabled",
        "hands_free_autostart",
        "voice_only_confirmation_enabled",
        "high_integrity_transcription_enabled",
        "live_label_scan_enabled",
        "warm_models_on_startup",
        "timer_alarms_enabled",
    )
    #: The one place a default verifier model name is allowed (see ADR 0018).
    HANDS_FREE_DEFAULT_VERIFIER: ClassVar[str] = "base.en"

    def _apply_hands_free_profile(self) -> None:
        if not self.hands_free_profile:
            return
        for name in self.HANDS_FREE_PROFILE_FLAGS:
            # Hosted Vision uses one deliberate voice-triggered capture. Never
            # enable the repeated live probes that would send many images away.
            if name == "live_label_scan_enabled" and self.stage_is_remote("vision"):
                continue
            if name in self.model_fields_set and getattr(self, name) is False:
                raise ValueError(
                    f"the hands-free profile needs LABJARVIS_{name.upper()}=true; remove "
                    "that setting or switch the profile off"
                )
            object.__setattr__(self, name, True)
        if not self.high_integrity_transcription_model_name.strip():
            object.__setattr__(
                self, "high_integrity_transcription_model_name", self.HANDS_FREE_DEFAULT_VERIFIER
            )

    @model_validator(mode="after")
    def validate_threshold_order(self) -> Self:
        self._apply_hands_free_profile()
        if self.reagent_match_threshold <= self.reagent_uncertain_threshold:
            raise ValueError("match threshold must exceed uncertain threshold")
        parsed_ollama = urlsplit(self.ollama_base_url)
        if parsed_ollama.scheme not in {"http", "https"} or parsed_ollama.hostname is None:
            raise ValueError("Ollama base URL must be an absolute HTTP(S) URL")
        if parsed_ollama.username is not None or parsed_ollama.password is not None:
            raise ValueError("Ollama base URL must not contain credentials")
        if not self.allow_remote_model_service and parsed_ollama.hostname not in LOOPBACK_HOSTS:
            raise ValueError("remote model services require explicit opt-in")

        for url in (
            self.hosted_base_url,
            self.hosted_speech_base_url,
            self.hosted_text_base_url,
            self.hosted_vision_base_url,
            self.text_verifier_base_url,
            self.openai_base_url,
            self.deepseek_base_url,
        ):
            if url is not None:
                _validate_hosted_url(url)
        _validate_extra_body(self.hosted_text_extra_body, "hosted text extra body")
        _validate_extra_body(self.text_verifier_extra_body, "text verifier extra body")
        _validate_extra_body(self.deepseek_text_extra_body, "DeepSeek extra body")
        self._validate_verifier()
        if self.model_mode is ModelMode.HOSTED:
            self._validate_hosted_requirements()
        self._validate_stage_providers()
        self._validate_study_mode()
        if self.live_label_scan_enabled and self.stage_is_remote("vision"):
            raise ValueError(
                "live label scanning sends a frame every few hundred milliseconds, so it "
                "is only available with a local OCR stage (the vision stage is hosted)"
            )
        if self.voice_only_confirmation_enabled:
            if not self.hands_free_listening_enabled:
                raise ValueError(
                    "voice-only confirmation needs LABJARVIS_HANDS_FREE_LISTENING_ENABLED=true"
                )
            if not self.high_integrity_transcription_enabled:
                raise ValueError(
                    "voice-only confirmation needs the high-integrity transcription "
                    "cross-check (LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_ENABLED=true)"
                )
        if self.voice_observations_enabled and not self.voice_only_confirmation_enabled:
            raise ValueError(
                "voice-confirmed observations need voice-only confirmation "
                "(LABJARVIS_VOICE_ONLY_CONFIRMATION_ENABLED=true or the hands-free profile)"
            )
        if self.hands_free_autostart and not self.hands_free_listening_enabled:
            raise ValueError(
                "hands-free autostart needs LABJARVIS_HANDS_FREE_LISTENING_ENABLED=true"
            )
        if any(value <= 0 or value > 3600 for value in self.timer_warning_seconds):
            raise ValueError("timer warnings must be between 1 and 3600 seconds")
        if self.realtime_enabled:
            self._validate_realtime_requirements()
        if (
            self.high_integrity_transcription_enabled
            and not self.high_integrity_transcription_model_name.strip()
        ):
            raise ValueError(
                "high-integrity transcription requires an explicit "
                "LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_MODEL_NAME"
            )

        blank_vision_language = any(not language.strip() for language in self.vision_languages)
        if not self.vision_languages or blank_vision_language:
            raise ValueError("at least one non-blank OCR language is required")
        if not self.allowed_hosts or any(
            not host.strip() or host == "*" for host in self.allowed_hosts
        ):
            raise ValueError("allowed hosts must be explicit non-blank hostnames")
        return self

    def _validate_realtime_requirements(self) -> None:
        """Fail closed unless realtime voice is fully and explicitly configured.

        Realtime streams microphone audio to a hosted provider, so it inherits
        the same explicit opt-in as any other hosted processing. Messages name
        only the setting at fault; the credential is never interpolated.
        """

        if not self.allow_hosted_models:
            raise ValueError(
                "realtime voice sends audio to a hosted provider and requires "
                "LABJARVIS_ALLOW_HOSTED_MODELS"
            )
        if self.hosted_provider is not HostedProvider.OPENAI:
            # Chat-completions compatibility does not imply a realtime protocol.
            raise ValueError(
                "realtime voice requires a provider with a realtime capability; "
                "only the OpenAI provider declares one"
            )
        key = self.openai_key()
        if key is None or not key.get_secret_value().strip():
            raise ValueError("realtime voice requires a non-blank hosted API key")
        if not self.realtime_model_name.strip():
            raise ValueError(
                "realtime voice requires an explicit LABJARVIS_REALTIME_MODEL_NAME; "
                "no default is assumed because provider aliases change"
            )

    def _validate_hosted_requirements(self) -> None:
        """Fail closed unless hosted processing is opted into and fully configured.

        Messages deliberately name only the setting at fault. The secret itself is
        never interpolated, so a validation error is safe to log or surface.
        """

        if not self.allow_hosted_models:
            raise ValueError(
                "hosted mode requires explicit hosted processing via "
                "LABJARVIS_ALLOW_HOSTED_MODELS"
            )
        hosted = [stage for stage in STAGES if self.stage_is_hosted(stage)]
        for stage in hosted:
            key = self.hosted_key_for(stage)
            if key is None or not key.get_secret_value().strip():
                raise ValueError("hosted mode requires a non-blank hosted API key")
        missing = [stage for stage in hosted if not self.hosted_model_for(stage).strip()]
        if missing:
            raise ValueError(f"hosted mode requires a model identifier for: {', '.join(missing)}")

    def _validate_stage_providers(self) -> None:
        """Validate hybrid per-stage providers and declared capabilities.

        A provider being "OpenAI-compatible" says nothing about which modalities
        it serves, so an OpenAI-compatible speech or vision stage must declare
        the capability explicitly; otherwise configuration fails closed here
        rather than at the first live request.
        """

        explicit_hosted = [
            stage
            for stage in STAGES
            if getattr(self, f"{stage}_provider") in HOSTED_STAGE_PROVIDERS
        ]
        if explicit_hosted and self.model_mode is not ModelMode.HOSTED:
            self._validate_hosted_requirements()
        for other in ("speech", "vision"):
            if getattr(self, f"{other}_provider") is StageProvider.DEEPSEEK:
                raise ValueError(f"DeepSeek is configured for the text stage only, not {other}")
        if (
            self.stage_provider("speech") is StageProvider.OPENAI_COMPATIBLE
            and not self.hosted_speech_transcription
        ):
            raise ValueError(
                "incompatible modality: the OpenAI-compatible speech provider must declare "
                "LABJARVIS_HOSTED_SPEECH_TRANSCRIPTION=true"
            )
        if (
            self.stage_provider("vision") is StageProvider.OPENAI_COMPATIBLE
            and not self.hosted_vision_image_input
        ):
            raise ValueError(
                "incompatible modality: the OpenAI-compatible vision provider must declare "
                "LABJARVIS_HOSTED_VISION_IMAGE_INPUT=true"
            )
        if (
            self.text_interpreter not in (None, TextInterpreter.RULES)
            and self.stage_provider("text") is StageProvider.FAKE
        ):
            raise ValueError("the fake text stage has no language model; use rules")

    def _validate_verifier(self) -> None:
        provider = self.text_verifier_provider
        if provider is None or provider is StageProvider.LOCAL:
            return
        if not self.text_verifier_model_name.strip():
            raise ValueError("a text verifier provider needs LABJARVIS_TEXT_VERIFIER_MODEL_NAME")
        if provider is StageProvider.FAKE:
            raise ValueError("the text verifier cannot use the fake provider")
        if not self.allow_hosted_models:
            raise ValueError(
                "a hosted text verifier requires LABJARVIS_ALLOW_HOSTED_MODELS=true"
            )
        key = self.verifier_api_key()
        if key is None or not key.get_secret_value().strip():
            raise ValueError("a hosted text verifier requires an API key")
        if provider is StageProvider.OPENAI_COMPATIBLE and self.text_verifier_base_url is None:
            raise ValueError(
                "an OpenAI-compatible text verifier requires LABJARVIS_TEXT_VERIFIER_BASE_URL"
            )

    def verifier_api_key(self) -> SecretStr | None:
        if self.text_verifier_api_key is not None:
            return self.text_verifier_api_key
        provider = self.text_verifier_provider
        if provider in (StageProvider.OPENAI, StageProvider.DEEPSEEK):
            return self.connection_for("text", provider)[1]
        return self.hosted_key_for("text")

    def verifier_base_url(self) -> str:
        if self.text_verifier_base_url is not None:
            return self.text_verifier_base_url
        provider = self.text_verifier_provider
        if provider in (StageProvider.OPENAI, StageProvider.DEEPSEEK):
            return self.connection_for("text", provider)[0]
        return self.hosted_base_url

    def verifier_label(self) -> str:
        label = self.text_verifier_provider_label.strip()
        if label:
            return label
        if self.text_verifier_provider is StageProvider.OPENAI:
            return "OpenAI"
        if self.text_verifier_provider is StageProvider.DEEPSEEK:
            return "DeepSeek"
        host = urlsplit(self.verifier_base_url()).hostname or "unknown"
        return f"OpenAI-compatible ({host})"

    def verifier_locality(self) -> Literal["local", "hosted"]:
        provider = self.text_verifier_provider
        if provider is None or provider is StageProvider.LOCAL:
            return "local"
        host = urlsplit(self.verifier_base_url()).hostname
        return "local" if host in LOOPBACK_HOSTS else "hosted"

    def _validate_study_mode(self) -> None:
        acknowledged = bool(self.study_mode_acknowledgement.strip())
        if self.study_mode_script is None and not acknowledged:
            return
        from labjarvis.infrastructure.study_mode import STUDY_ACKNOWLEDGEMENT

        if self.study_mode_script is None or not acknowledged:
            raise ValueError(
                "study mode needs both LABJARVIS_STUDY_MODE_SCRIPT and "
                "LABJARVIS_STUDY_MODE_ACKNOWLEDGEMENT"
            )
        if self.study_mode_acknowledgement != STUDY_ACKNOWLEDGEMENT:
            raise ValueError("study mode acknowledgement phrase does not match")
        if self.app_env == "production":
            raise ValueError("study mode is refused in production")
        if not self.study_mode_script.is_file():
            raise ValueError("the study script does not exist")

    @property
    def study_mode_enabled(self) -> bool:
        return self.study_mode_script is not None and bool(self.study_mode_acknowledgement)

    # --- Resolved per-stage configuration ------------------------------------

    def stage_provider(self, stage: Stage) -> StageProvider:
        """The provider that runs ``stage``: explicit, or implied by model_mode."""

        explicit: StageProvider | None = getattr(self, f"{stage}_provider")
        if explicit is not None:
            return explicit
        if self.model_mode is ModelMode.FAKE:
            return StageProvider.FAKE
        if self.model_mode is ModelMode.LOCAL:
            return StageProvider.LOCAL
        return StageProvider(self.hosted_provider.value)

    def stage_is_hosted(self, stage: Stage) -> bool:
        """True when ``stage`` uses a hosted-API adapter (OpenAI or compatible).

        This says which adapter family runs, not where it runs: an
        OpenAI-compatible server on this machine (vLLM, llama.cpp, LM Studio)
        uses the hosted adapter but is local. Use :meth:`stage_locality`.
        """

        return self.stage_provider(stage) in HOSTED_STAGE_PROVIDERS

    def stage_locality(self, stage: Stage) -> Literal["fake", "local", "hosted"]:
        """Where ``stage`` actually runs, for provenance and display."""

        provider = self.stage_provider(stage)
        if provider is StageProvider.FAKE:
            return "fake"
        if provider is StageProvider.LOCAL:
            return "local"
        host = urlsplit(self.hosted_base_url_for(stage)).hostname
        return "local" if host in LOOPBACK_HOSTS else "hosted"

    def stage_is_remote(self, stage: Stage) -> bool:
        """True when ``stage`` sends data off this machine."""

        return self.stage_locality(stage) == "hosted"

    def openai_key(self) -> SecretStr | None:
        """OpenAI's own key: LABJARVIS_OPENAI_API_KEY, else the hosted key."""

        if self.openai_api_key is not None:
            return self.openai_api_key
        if self.hosted_provider is HostedProvider.OPENAI:
            return self.hosted_api_key
        return None

    def connection_for(
        self, stage: Stage, provider: StageProvider
    ) -> tuple[str, SecretStr | None]:
        """Endpoint and key ``provider`` would use for ``stage``.

        Named providers never borrow each other's settings: OpenAI always goes
        to OpenAI's endpoint with OpenAI's key, DeepSeek to DeepSeek's. The
        per-stage endpoint and key overrides belong to the generic
        OpenAI-compatible provider (a per-stage key serves OpenAI only when no
        per-stage endpoint says it is meant for another service).
        """

        stage_url: str | None = getattr(self, f"hosted_{stage}_base_url")
        stage_key: SecretStr | None = getattr(self, f"hosted_{stage}_api_key")
        if provider is StageProvider.DEEPSEEK:
            return self.deepseek_base_url, self.deepseek_api_key
        if provider is StageProvider.OPENAI:
            url = self.openai_base_url or (
                self.hosted_base_url
                if self.hosted_provider is HostedProvider.OPENAI
                else OPENAI_DEFAULT_BASE_URL
            )
            if self.openai_api_key is not None:
                return url, self.openai_api_key
            if stage_key is not None and stage_url is None:
                return url, stage_key
            return url, self.openai_key()
        return (
            stage_url if stage_url is not None else self.hosted_base_url,
            stage_key if stage_key is not None else self.hosted_api_key,
        )

    def hosted_key_for(self, stage: Stage) -> SecretStr | None:
        return self.connection_for(stage, self.stage_provider(stage))[1]

    def hosted_base_url_for(self, stage: Stage) -> str:
        return self.connection_for(stage, self.stage_provider(stage))[0]

    def hosted_model_for(self, stage: Stage) -> str:
        return str(getattr(self, f"hosted_{stage}_model_name"))

    def hosted_label_for(self, stage: Stage) -> str:
        label = str(getattr(self, f"hosted_{stage}_provider_label")).strip()
        if label:
            return label
        provider = self.stage_provider(stage)
        if provider is StageProvider.OPENAI:
            return "OpenAI"
        if provider is StageProvider.DEEPSEEK:
            return "DeepSeek"
        host = urlsplit(self.hosted_base_url_for(stage)).hostname or "unknown"
        return f"OpenAI-compatible ({host})"

    def effective_text_interpreter(self) -> TextInterpreter:
        if self.text_interpreter is not None:
            return self.text_interpreter
        if self.stage_provider("text") is StageProvider.FAKE:
            return TextInterpreter.RULES
        return TextInterpreter.CASCADE

    def effective_text_structured_output(self) -> StructuredOutputMode:
        if self.stage_provider("text") is StageProvider.DEEPSEEK:
            return self.deepseek_text_structured_output
        if self.hosted_text_structured_output is not None:
            return self.hosted_text_structured_output
        if self.stage_provider("text") is StageProvider.OPENAI:
            return StructuredOutputMode.JSON_SCHEMA
        return StructuredOutputMode.JSON_OBJECT

    def execution_profile(self) -> str:
        """``fake``, ``local``, ``hosted`` or ``hybrid``, for truthful display."""

        localities = {self.stage_locality(stage) for stage in STAGES}
        if len(localities) == 1:
            return next(iter(localities))
        return "hybrid"

    @property
    def any_stage_hosted(self) -> bool:
        return any(self.stage_is_hosted(stage) for stage in STAGES)

    @property
    def any_stage_remote(self) -> bool:
        """True when any inference (including a text verifier) leaves this machine."""

        return any(self.stage_is_remote(stage) for stage in STAGES) or (
            bool(self.text_verifier_model_name.strip()) and self.verifier_locality() == "hosted"
        )


_FORBIDDEN_EXTRA_BODY_KEYS = frozenset(
    {"model", "messages", "tools", "tool_choice", "functions", "stream"}
)


def _validate_extra_body(raw: str, name: str) -> None:
    if not raw.strip():
        return
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(extra, dict):
        raise ValueError(f"{name} must be a JSON object")
    if _FORBIDDEN_EXTRA_BODY_KEYS & set(extra):
        raise ValueError(f"{name} may not override model, messages, streaming or tools")


def _validate_hosted_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("hosted base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("hosted base URL must not contain credentials")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("hosted base URL must use HTTPS outside loopback")


def settings_with_saved_values(values: Mapping[str, object] | None) -> Settings:
    """Build settings with ``values`` as the saved-configuration layer.

    The same validators run as for any other source, so a saved configuration
    is accepted or refused by exactly the rules the application starts with.
    """

    token = _SAVED_VALUES.set(values)
    try:
        return Settings()
    finally:
        _SAVED_VALUES.reset(token)


@cache
def get_settings() -> Settings:
    """Return process-wide validated settings, including the saved configuration."""

    from labjarvis.saved_configuration import load_application_settings

    return load_application_settings()
