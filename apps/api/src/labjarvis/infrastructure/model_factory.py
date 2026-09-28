"""Select model adapters for each stage only at the composition boundary.

This is the single place that knows which provider runs each stage. Speech,
text and vision are composed independently, so any supported combination
works by configuration alone - for example local Whisper and EasyOCR beside a
hosted OpenAI-compatible text model. `LabJarvisService`, the API routes, the
domain, and the frontend stay untouched.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import SecretStr

from labjarvis.infrastructure.cascade import CascadeTextUnderstanding
from labjarvis.infrastructure.document_ocr import (
    RenderedDocumentOcr,
    UnavailableDocumentOcr,
)
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAICompatibleLabelRecognition,
    OpenAICompatibleTextUnderstanding,
    OpenAILabelRecognition,
    OpenAISpeechToText,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.real_models import (
    EasyOcrLabelRecognition,
    FasterWhisperSpeechToText,
    OllamaTextUnderstanding,
)
from labjarvis.infrastructure.realtime import (
    FakeRealtimeSessionFactory,
    OpenAIRealtimeSessionFactory,
)
from labjarvis.infrastructure.rule_grammar import RuleGrammarTextUnderstanding
from labjarvis.infrastructure.study_mode import (
    StudyInjector,
    StudyLabelRecognition,
    StudyTextUnderstanding,
    load_study_script,
)
from labjarvis.infrastructure.text_model_profiles import resolve_text_profile
from labjarvis.ports.models import (
    DocumentOcrPort,
    LabelRecognitionPort,
    SpeechToTextPort,
    TextUnderstandingPort,
)
from labjarvis.ports.realtime import RealtimeSessionPort
from labjarvis.settings import (
    HostedProvider,
    ModelMode,
    ReasoningEffort,
    Settings,
    Stage,
    StageProvider,
    StructuredOutputMode,
    TextInterpreter,
)


@runtime_checkable
class SupportsClose(Protocol):
    """An adapter that owns a resource needing deterministic release."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ModelAdapters:
    speech: SpeechToTextPort
    text: TextUnderstandingPort
    labels: LabelRecognitionPort
    documents: DocumentOcrPort
    #: A second, higher-accuracy transcription path used only for utterances
    #: whose exact wording could change the experimental record. ``None`` when
    #: cross-checking is switched off, which is the default.
    verification_speech: SpeechToTextPort | None = None

    def close(self) -> None:
        """Close every owned adapter exactly once.

        Adapters are de-duplicated by identity, and adapters that share one
        client (as the hosted profile does) rely on that client's own idempotent
        close, so repeated calls are safe.
        """

        seen: set[int] = set()
        for adapter in (
            self.speech,
            self.text,
            self.labels,
            self.documents,
            self.verification_speech,
        ):
            if adapter is None or not isinstance(adapter, SupportsClose):
                continue
            if id(adapter) in seen:
                continue
            seen.add(id(adapter))
            adapter.close()


class _HostedClients:
    """One pooled client per distinct (provider, endpoint, key) combination."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[tuple[str, str, str, str], OpenAIClient] = {}

    def for_stage(self, stage: Stage) -> OpenAIClient:
        settings = self._settings
        key = settings.hosted_key_for(stage)
        if key is None:  # pragma: no cover - settings validation guarantees this
            raise ValueError(f"hosted {stage} requires an API key")
        return self.for_endpoint(
            provider=settings.stage_provider(stage).value,
            base_url=settings.hosted_base_url_for(stage),
            key=key,
            label=settings.hosted_label_for(stage),
        )

    def for_verifier(self) -> OpenAIClient:
        settings = self._settings
        key = settings.verifier_api_key()
        provider = settings.text_verifier_provider
        if key is None or provider is None:  # pragma: no cover - validated in settings
            raise ValueError("a hosted text verifier requires a provider and an API key")
        return self.for_endpoint(
            provider=provider.value,
            base_url=settings.verifier_base_url(),
            key=key,
            label=settings.verifier_label(),
        )

    def for_endpoint(
        self, *, provider: str, base_url: str, key: SecretStr, label: str
    ) -> OpenAIClient:
        settings = self._settings
        identity = (provider, base_url, key.get_secret_value(), label)
        client = self._clients.get(identity)
        if client is None:
            client = OpenAIClient(
                HostedClientConfig(
                    api_key=key,
                    base_url=base_url,
                    timeout_seconds=settings.inference_timeout_seconds,
                    max_response_bytes=settings.hosted_max_response_bytes,
                    max_retries=settings.hosted_max_retries,
                    provider=provider,
                    provider_label=label,
                )
            )
            self._clients[identity] = client
        return client


def _extra_body(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    decoded = json.loads(raw)
    return dict(decoded) if isinstance(decoded, dict) else {}


def _effort(value: ReasoningEffort | None) -> str | None:
    return None if value is None else value.value


def _build_speech(
    settings: Settings,
    clients: _HostedClients,
    *,
    model_name: str | None = None,
    verification: bool = False,
) -> SpeechToTextPort:
    provider = settings.stage_provider("speech")
    if provider is StageProvider.FAKE:
        return FakeSpeechToText(verification=verification)
    if provider is StageProvider.LOCAL:
        return FasterWhisperSpeechToText(
            model_name=model_name or settings.speech_model_name,
            device=settings.speech_device,
            compute_type=settings.speech_compute_type,
            cache_dir=settings.model_cache_dir / "faster-whisper",
            language=settings.speech_language,
            beam_size=settings.speech_beam_size,
            allow_download=settings.allow_model_download,
            conditioning=settings.speech_conditioning,
        )
    return OpenAISpeechToText(
        clients.for_stage("speech"),
        model_name=model_name or settings.hosted_model_for("speech"),
        language=settings.speech_language,
    )


def _build_language_model(
    settings: Settings,
    clients: _HostedClients,
) -> TextUnderstandingPort | None:
    provider = settings.stage_provider("text")
    if provider is StageProvider.FAKE:
        return None
    if provider is StageProvider.LOCAL:
        profile = resolve_text_profile(
            settings.text_model_name,
            think=settings.text_think,
            timeout_seconds=settings.text_timeout_seconds or 20.0,
            temperature=settings.text_temperature,
            context_tokens=settings.text_context_tokens,
            max_output_tokens=settings.text_max_output_tokens,
            few_shot=settings.text_few_shot,
        )
        return OllamaTextUnderstanding(
            model_name=settings.text_model_name,
            base_url=settings.ollama_base_url,
            wake_phrase=settings.wake_phrase,
            profile=profile,
        )
    deepseek = provider is StageProvider.DEEPSEEK
    return _hosted_text_model(
        settings,
        clients.for_stage("text"),
        provider=provider,
        model_name=settings.hosted_model_for("text"),
        structured_output=settings.effective_text_structured_output(),
        # DeepSeek's options are its own; OpenAI's reasoning setting and the
        # generic endpoint's extra fields are never sent to it.
        reasoning_effort=None if deepseek else _effort(settings.hosted_text_reasoning_effort),
        extra_body=_extra_body(
            settings.deepseek_text_extra_body if deepseek else settings.hosted_text_extra_body
        ),
    )


def _hosted_text_model(
    settings: Settings,
    client: OpenAIClient,
    *,
    provider: StageProvider,
    model_name: str,
    structured_output: StructuredOutputMode,
    reasoning_effort: str | None,
    extra_body: dict[str, Any],
) -> TextUnderstandingPort:
    if provider is StageProvider.OPENAI:
        return OpenAITextUnderstanding(
            client,
            model_name=model_name,
            wake_phrase=settings.wake_phrase,
            few_shot=settings.text_few_shot,
            max_output_tokens=settings.hosted_text_max_output_tokens,
            reasoning_effort=reasoning_effort,
        )
    return OpenAICompatibleTextUnderstanding(
        client,
        model_name=model_name,
        wake_phrase=settings.wake_phrase,
        structured_output=structured_output.value,
        temperature=settings.text_temperature,
        max_output_tokens=settings.hosted_text_max_output_tokens,
        token_limit_field=settings.hosted_text_token_limit_field,
        extra_body=extra_body,
        few_shot=settings.text_few_shot,
        reasoning_effort=reasoning_effort,
    )


def build_verifier(
    settings: Settings, clients: "_HostedClients | None" = None
) -> TextUnderstandingPort | None:
    """The optional second model for the cascade, on its own provider."""

    name = settings.text_verifier_model_name.strip()
    if not name:
        return None
    provider = settings.text_verifier_provider
    if provider is None or provider is StageProvider.LOCAL:
        return OllamaTextUnderstanding(
            model_name=name,
            base_url=settings.ollama_base_url,
            wake_phrase=settings.wake_phrase,
            profile=resolve_text_profile(
                name,
                think=settings.text_think,
                timeout_seconds=settings.text_timeout_seconds or 20.0,
                temperature=settings.text_temperature,
                context_tokens=settings.text_context_tokens,
                max_output_tokens=settings.text_max_output_tokens,
                few_shot=settings.text_few_shot,
            ),
        )
    hosted_clients = clients if clients is not None else _HostedClients(settings)
    structured = settings.text_verifier_structured_output or (
        StructuredOutputMode.JSON_SCHEMA
        if provider is StageProvider.OPENAI
        else settings.deepseek_text_structured_output
        if provider is StageProvider.DEEPSEEK
        else StructuredOutputMode.JSON_OBJECT
    )
    return _hosted_text_model(
        settings,
        hosted_clients.for_verifier(),
        provider=provider,
        model_name=name,
        structured_output=structured,
        reasoning_effort=_effort(settings.text_verifier_reasoning_effort),
        extra_body=_extra_body(settings.text_verifier_extra_body),
    )


def build_text_interpreter(
    settings: Settings,
    language_model: TextUnderstandingPort | None,
    *,
    clients: "_HostedClients | None" = None,
) -> TextUnderstandingPort:
    """Wrap the configured language model in the configured interpreter."""

    interpreter = settings.effective_text_interpreter()
    verifier = build_verifier(settings, clients) if language_model is not None else None
    if language_model is None:
        # The fake profile keeps its historical rule-grammar identity.
        return FakeTextUnderstanding(wake_phrase=settings.wake_phrase)
    if interpreter is TextInterpreter.RULES:
        return RuleGrammarTextUnderstanding(wake_phrase=settings.wake_phrase)
    if interpreter is TextInterpreter.LLM:
        return language_model
    return CascadeTextUnderstanding(
        model=language_model,
        mode="agreement" if interpreter is TextInterpreter.AGREEMENT else "rules_first",
        wake_phrase=settings.wake_phrase,
        verifier=verifier,
    )


def _build_labels(settings: Settings, clients: _HostedClients) -> LabelRecognitionPort:
    provider = settings.stage_provider("vision")
    if provider is StageProvider.FAKE:
        return FakeLabelRecognition()
    if provider is StageProvider.LOCAL:
        return EasyOcrLabelRecognition(
            languages=settings.vision_languages,
            gpu=settings.vision_gpu,
            cache_dir=settings.model_cache_dir / "easyocr",
            model_name=settings.vision_model_name,
            allow_download=settings.allow_model_download,
        )
    client = clients.for_stage("vision")
    if provider is StageProvider.OPENAI:
        return OpenAILabelRecognition(client, model_name=settings.hosted_model_for("vision"))
    return OpenAICompatibleLabelRecognition(
        client,
        model_name=settings.hosted_model_for("vision"),
        token_limit_field=settings.hosted_text_token_limit_field,
    )


def build_realtime_sessions(settings: Settings) -> RealtimeSessionPort | None:
    """Build the realtime session minter, or ``None`` when the feature is off.

    Fake and local profiles get the deterministic factory so the whole realtime
    UI - consent, connection states, reconnect, barge-in - can be exercised and
    tested without a paid provider or a network. Only the OpenAI provider offers
    a realtime protocol; OpenAI-compatible endpoints are never assumed to.
    """

    if not settings.realtime_enabled:
        return None
    if settings.model_mode is ModelMode.HOSTED:
        realtime_key = settings.openai_key()
        if realtime_key is None:  # pragma: no cover - settings guarantee this
            raise ValueError("realtime voice requires a hosted API key")
        if settings.hosted_provider is not HostedProvider.OPENAI:
            raise ValueError("realtime voice is only available with the OpenAI provider")
        client = OpenAIClient(
            HostedClientConfig(
                api_key=realtime_key,
                base_url=settings.connection_for("text", StageProvider.OPENAI)[0],
                timeout_seconds=settings.inference_timeout_seconds,
                max_response_bytes=settings.hosted_max_response_bytes,
                max_retries=settings.hosted_max_retries,
            )
        )
        return OpenAIRealtimeSessionFactory(
            client,
            model_name=settings.realtime_model_name,
            voice=settings.realtime_voice,
            transport=settings.realtime_transport.value,
            ttl_seconds=settings.realtime_session_ttl_seconds,
        )
    return FakeRealtimeSessionFactory(
        model_name=settings.realtime_model_name,
        voice=settings.realtime_voice,
        transport=settings.realtime_transport.value,
        ttl_seconds=settings.realtime_session_ttl_seconds,
    )


def build_model_adapters(settings: Settings) -> ModelAdapters:
    clients = _HostedClients(settings)
    speech = _build_speech(settings, clients)
    verification_speech = (
        _build_speech(
            settings,
            clients,
            model_name=settings.high_integrity_transcription_model_name,
            verification=True,
        )
        if settings.high_integrity_transcription_enabled
        else None
    )
    text = build_text_interpreter(
        settings, _build_language_model(settings, clients), clients=clients
    )
    labels = _build_labels(settings, clients)
    ocr_labels = labels
    if settings.study_mode_enabled and settings.study_mode_script is not None:
        injector = StudyInjector(load_study_script(settings.study_mode_script))
        text = StudyTextUnderstanding(text, injector)
        labels = StudyLabelRecognition(labels, injector)
    documents: DocumentOcrPort = (
        RenderedDocumentOcr(
            recognizer=ocr_labels,
            dpi=settings.document_ocr_dpi,
            max_page_pixels=settings.document_ocr_max_page_pixels,
            max_total_pixels=settings.document_ocr_max_total_pixels,
        )
        if isinstance(ocr_labels, EasyOcrLabelRecognition)
        else UnavailableDocumentOcr()
    )
    return ModelAdapters(
        speech=speech,
        verification_speech=verification_speech,
        text=text,
        labels=labels,
        documents=documents,
    )
