"""Hosted adapters behind the existing model ports: OpenAI and OpenAI-compatible.

This module is the only place in the application that knows hosted providers
exist. It implements the same three ports as the fake and local adapters and
returns the same application-facing result types, so `LabJarvisService`, the API
routes, the domain, and the frontend are unchanged by hosted mode.

Two families share one bounded HTTP client:

* **OpenAI** (first-class): Responses API with strict JSON-schema output for
  text and vision, the transcription endpoint for speech, and the realtime
  shell elsewhere.
* **OpenAI-compatible** (DeepSeek, GLM/Zhipu, self-hosted gateways, ...):
  ``/chat/completions`` for text and, when the endpoint declares image input,
  vision; ``/audio/transcriptions`` for speech when it declares transcription.
  Base URL, model identifier, structured-output capability and any
  provider-specific request fields are configuration. No vendor URL or model
  name is compiled in.

Whatever the provider can or cannot enforce, every text answer is parsed
against the same discriminated command union and grounded deterministically;
nothing is relaxed to accommodate a weaker provider.

Safety properties enforced here:

* the API key is read from `SecretStr` at request time and never logged,
  echoed into an exception, or included in any result metadata;
* responses are read under a byte cap before JSON decoding;
* only transient failures are retried, and never more than the configured
  ceiling, so no background work outlives a reported failure;
* exception messages are sanitised — no provider body, request body, or user
  content ever reaches them.

Models propose transcripts, commands, and visible label text. They never decide
reagent safety, never mutate state, and are never given tools.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import sleep
from types import TracebackType
from typing import Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from labjarvis.domain.models import ParsedCommand
from labjarvis.infrastructure.command_contract import (
    CONTRACT_VERSION,
    parse_model_command,
    schema_sha256,
    strict_command_json_schema,
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
from labjarvis.infrastructure.speech_conditioning import protocol_prompt
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    LabelResult,
    SpeechResult,
    TextResult,
)

PROVIDER_NAME = "openai"

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_REQUEST_ID_HEADERS = ("x-request-id", "request-id", "openai-request-id")
_RETRY_BACKOFF_SECONDS = 0.25
_MAX_VISIBLE_LABEL_CHARACTERS = 250
_MAX_COMMAND_OUTPUT_TOKENS = 512
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_LABEL_OUTPUT_TOKENS = 256


@dataclass(frozen=True, slots=True)
class HostedClientConfig:
    """Validated transport settings for one hosted provider account.

    ``provider`` is the adapter family (``openai`` or ``openai_compatible``);
    ``provider_label`` is the human-readable name shown in the UI and recorded
    in provenance, so a DeepSeek or GLM call is never displayed as OpenAI or
    as local.
    """

    api_key: SecretStr
    base_url: str
    timeout_seconds: float
    max_response_bytes: int
    max_retries: int
    provider: str = PROVIDER_NAME
    provider_label: str = "OpenAI"
    extra_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HostedResponse:
    """A bounded, decoded provider response plus its safe request metadata."""

    data: Mapping[str, Any]
    request_id: str
    retry_count: int
    status_code: int

    def usage_metadata(self) -> dict[str, str]:
        """Return only the usage counters the provider actually supplied."""

        usage = self.data.get("usage")
        if not isinstance(usage, dict):
            return {}
        safe: dict[str, str] = {}
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, int | float):
                safe[key] = str(value)
        # Cached prompt tokens are billed at a discount and reasoning tokens are
        # billed as output; both are recorded so spend is costed from what the
        # provider actually reported.
        for details_key, field_name, safe_key in (
            ("input_tokens_details", "cached_tokens", "cached_input_tokens"),
            ("prompt_tokens_details", "cached_tokens", "cached_input_tokens"),
            ("output_tokens_details", "reasoning_tokens", "reasoning_tokens"),
            ("completion_tokens_details", "reasoning_tokens", "reasoning_tokens"),
        ):
            details = usage.get(details_key)
            if isinstance(details, dict):
                detail_value = details.get(field_name)
                if isinstance(detail_value, int | float):
                    safe[safe_key] = str(detail_value)
        seconds = usage.get("seconds")
        if isinstance(seconds, int | float):
            safe["usage_seconds"] = str(seconds)
        return safe


class OpenAIClient:
    """A reused, bounded HTTP client for one hosted provider account.

    Constructed once at the composition boundary and shared by all three hosted
    adapters, so connections are pooled and closure is deterministic.
    """

    def __init__(
        self,
        config: HostedClientConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._closed = False
        timeout = httpx.Timeout(
            connect=min(config.timeout_seconds, 10.0),
            read=config.timeout_seconds,
            write=config.timeout_seconds,
            pool=min(config.timeout_seconds, 10.0),
        )
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def provider(self) -> str:
        return self._config.provider

    @property
    def provider_label(self) -> str:
        return self._config.provider_label

    @property
    def is_loopback(self) -> bool:
        """True for an OpenAI-compatible server on this machine (e.g. vLLM, LM Studio)."""

        return urlsplit(self._config.base_url).hostname in _LOOPBACK_HOSTS

    @property
    def base_domain(self) -> str:
        """Host name only: recorded in provenance, never the path or a credential."""

        return urlsplit(self._config.base_url).hostname or ""

    def _headers(self) -> dict[str, str]:
        # Built per request so the secret is never held on a logged attribute.
        headers = dict(self._config.extra_headers)
        headers["Authorization"] = f"Bearer {self._config.api_key.get_secret_value()}"
        return headers

    def get_json(self, path: str) -> HostedResponse:
        return self._send("GET", path)

    def post_json(self, path: str, payload: Mapping[str, Any]) -> HostedResponse:
        return self._send("POST", path, json_body=payload)

    def post_multipart(
        self,
        path: str,
        *,
        files: Mapping[str, tuple[str, bytes, str]],
        data: Mapping[str, str],
    ) -> HostedResponse:
        return self._send("POST", path, files=files, data=data)

    def get_model(self, model_name: str) -> HostedResponse:
        return self._send("GET", f"/models/{model_name}")

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        data: Mapping[str, str] | None = None,
    ) -> HostedResponse:
        attempts = self._config.max_retries + 1
        last_status: int | None = None
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    path,
                    headers=self._headers(),
                    json=dict(json_body) if json_body is not None else None,
                    files=dict(files) if files is not None else None,
                    data=dict(data) if data is not None else None,
                )
            except httpx.TimeoutException as exc:
                raise InferenceTimeoutError(
                    "the hosted provider request exceeded its deadline"
                ) from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ModelUnavailableError(
                    "the hosted provider is not reachable from this machine"
                ) from exc

            try:
                if response.status_code < 400:
                    return HostedResponse(
                        data=self._decode(response),
                        request_id=_request_id(response),
                        retry_count=attempt,
                        status_code=response.status_code,
                    )
                last_status = response.status_code
                if response.status_code not in _RETRYABLE_STATUSES:
                    raise _status_error(response.status_code)
            finally:
                response.close()

            if attempt + 1 < attempts:
                sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

        raise _status_error(last_status if last_status is not None else 503)

    def _decode(self, response: httpx.Response) -> Mapping[str, Any]:
        """Read the body under a hard byte cap, then decode it as a JSON object."""

        limit = self._config.max_response_bytes
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ModelOutputError("the hosted provider response was too large to process")
        try:
            decoded = json.loads(bytes(chunks).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelOutputError("the hosted provider returned a malformed response") from exc
        if not isinstance(decoded, dict):
            raise ModelOutputError("the hosted provider returned an unexpected response shape")
        return decoded

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _request_id(response: httpx.Response) -> str:
    for header in _REQUEST_ID_HEADERS:
        value = response.headers.get(header)
        if value:
            return str(value)
    return ""


def _status_error(status_code: int) -> ModelUnavailableError:
    """Map a provider status to a sanitised, actionable message.

    The provider's own error body is deliberately discarded: it can echo request
    content and, on authentication failures, fragments of the submitted key.
    """

    if status_code in {401, 403}:
        return ModelUnavailableError("the hosted provider rejected the configured credential")
    if status_code == 404:
        return ModelUnavailableError(
            "the configured hosted model is not available to this account"
        )
    if status_code == 429:
        return ModelUnavailableError("the hosted provider rate limit or quota was exhausted")
    if status_code == 400:
        return ModelUnavailableError(
            "the hosted provider rejected the request parameters; check model-specific "
            "options such as reasoning effort, structured output or the token-limit field"
        )
    if status_code in {413, 415, 422}:
        return ModelUnavailableError("the hosted provider rejected the request as unsupported")
    return ModelUnavailableError("the hosted provider is currently unavailable")


def _provider_metadata(
    response: HostedResponse,
    *,
    operation: str,
    model: str,
    client: OpenAIClient | None = None,
) -> dict[str, str]:
    """Assemble the allowlisted metadata safe to persist for one request."""

    metadata = {
        "provider": client.provider if client is not None else PROVIDER_NAME,
        "provider_label": client.provider_label if client is not None else "OpenAI",
        "locality": "local" if client is not None and client.is_loopback else "hosted",
        "api_operation": operation,
        "model_identifier": model,
        "retry_count": str(response.retry_count),
    }
    if client is not None and client.base_domain:
        metadata["base_domain"] = client.base_domain
    if response.request_id:
        metadata["request_id"] = response.request_id
    metadata.update(response.usage_metadata())
    return metadata


class _TranscriptionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str


class _VisibleLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    visible_text: str


def _output_text(response: HostedResponse) -> str:
    """Extract assistant text from a Responses API payload, rejecting refusals.

    The `output` array is walked directly rather than trusting the convenience
    `output_text` field, so a refusal or any unexpected content part fails closed
    instead of silently producing an empty command.
    """

    status = response.data.get("status")
    if status != "completed":
        raise ModelOutputError("the hosted model did not return a completed response")
    output = response.data.get("output")
    if not isinstance(output, list):
        raise ModelOutputError("the hosted model returned an unexpected response shape")
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list) or not content:
            raise ModelOutputError("the hosted model returned an empty message")
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise ModelOutputError("the hosted model declined or returned unsupported content")
            text = part.get("text")
            if not isinstance(text, str):
                raise ModelOutputError("the hosted model returned a malformed content part")
            parts.append(text)
        return "".join(parts)
    raise ModelOutputError("the hosted model returned no assistant message")


def _readiness_for(
    client: OpenAIClient,
    *,
    adapter: str,
    model_name: str,
) -> AdapterReadiness:
    """Probe model availability with an unpaid metadata request.

    Sends no protocol, audio, transcript, or image data, and reports only whether
    the configured model is usable — never the account's model inventory.
    """

    try:
        if client.provider == PROVIDER_NAME:
            client.get_model(model_name)
        else:
            listing = client.get_json("/models")
            listed = listing.data.get("data")
            if isinstance(listed, list):
                identifiers = {
                    item.get("id") for item in listed if isinstance(item, dict)
                }
                if model_name not in identifiers:
                    return AdapterReadiness(
                        ready=False,
                        adapter=adapter,
                        model_name=model_name,
                        detail="the provider is reachable but does not list this model",
                        dependency_installed=True,
                        provider_reachable=True,
                    )
    except ModelUnavailableError as exc:
        return AdapterReadiness(
            ready=False,
            adapter=adapter,
            model_name=model_name,
            detail=str(exc),
            dependency_installed=True,
            provider_reachable=False,
        )
    except (InferenceTimeoutError, ModelOutputError):
        return AdapterReadiness(
            ready=False,
            adapter=adapter,
            model_name=model_name,
            detail="the hosted provider did not respond in time",
            dependency_installed=True,
            provider_reachable=False,
        )
    # The probe confirms the provider answered for this exact model. It is
    # deliberately not an inference: that would cost money on a health check.
    return AdapterReadiness(
        ready=True,
        adapter=adapter,
        model_name=model_name,
        detail="hosted model reachable; no inference has been billed to verify output",
        dependency_installed=True,
        model_cached=True,
        loaded=True,
        provider_reachable=True,
        inference_tested=False,
    )


class OpenAISpeechToText:
    """Transcribe bounded audio through the hosted transcription API."""

    adapter_name = "openai-transcriptions"
    _suffixes: ClassVar[dict[str, str]] = {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
    }

    def __init__(self, client: OpenAIClient, *, model_name: str, language: str = "en") -> None:
        self._client = client
        self.model_name = model_name
        self._language = language

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

        data = {
            "model": self.model_name,
            "language": self._language,
            "response_format": "json",
        }
        if vocabulary:
            # The transcription API's documented ``prompt`` biases spelling
            # toward the reviewed protocol's reagent names and units.
            data["prompt"] = protocol_prompt(vocabulary)
        response = self._client.post_multipart(
            "/audio/transcriptions",
            files={"file": (f"input{suffix}", audio, media_type)},
            data=data,
        )
        try:
            envelope = _TranscriptionEnvelope.model_validate(dict(response.data))
        except ValidationError as exc:
            raise ModelOutputError("the hosted speech response failed its contract") from exc
        transcript = envelope.text.strip()
        if not transcript:
            raise ModelOutputError("speech model returned no transcript")
        return SpeechResult(
            transcript=transcript,
            # The provider exposes token logprobs, not a calibrated transcript
            # probability. Deriving a confidence from them would invent a number
            # the downstream threshold logic would treat as meaningful.
            confidence=None,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata=_provider_metadata(
                response,
                operation="audio.transcriptions",
                model=self.model_name,
                client=self._client,
            ),
        )

    def readiness(self) -> AdapterReadiness:
        return _readiness_for(
            self._client,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def close(self) -> None:
        self._client.close()


def _command_metadata(basis: str) -> dict[str, str]:
    return {
        "interpreter": "llm",
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": PROMPT_SHA256,
        "schema_sha256": schema_sha256(),
        "confidence_basis": basis,
    }


def _parse_command_text(text: str, transcript: str) -> tuple[ParsedCommand, str]:
    try:
        command = parse_model_command(text)
    except (ValueError, UnicodeDecodeError) as exc:
        # ``ValidationError`` and ``json.JSONDecodeError`` are both ValueErrors.
        raise ModelOutputError("text model output failed the command schema") from exc
    return to_parsed_command(command, transcript)


class OpenAITextUnderstanding:
    """Extract one allowlisted command through OpenAI strict structured output."""

    adapter_name = "openai-responses-json-schema"

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model_name: str,
        wake_phrase: str = "Jarvis",
        few_shot: bool = True,
        max_output_tokens: int = _MAX_COMMAND_OUTPUT_TOKENS,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._wake_phrase = wake_phrase
        self._few_shot = few_shot
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort

    def request_payload(self, transcript: str, context: CommandContext) -> dict[str, Any]:
        messages = interpreter_messages(
            transcript,
            context,
            wake_phrase=self._wake_phrase,
            few_shot=self._few_shot,
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "instructions": messages[0]["content"],
            "input": messages[1:],
            # Reasoning tokens count against this limit, so an over-long
            # reasoning pass ends "incomplete" and fails closed.
            "max_output_tokens": self._max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "labjarvis_command",
                    "schema": strict_command_json_schema(),
                    "strict": True,
                }
            },
        }
        if self._reasoning_effort is not None:
            payload["reasoning"] = {"effort": self._reasoning_effort}
        return payload

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        # No `tools` key is ever sent: the model proposes a command and cannot
        # invoke anything.
        response = self._client.post_json("/responses", self.request_payload(transcript, context))
        parsed, basis = _parse_command_text(_output_text(response), transcript)
        return TextResult(
            command=parsed,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={
                **_provider_metadata(
                    response,
                    operation="responses",
                    model=self.model_name,
                    client=self._client,
                ),
                **_command_metadata(basis),
                "structured_output": "json_schema",
                "reasoning_effort": self._reasoning_effort or "provider-default",
                "max_output_tokens": str(self._max_output_tokens),
            },
        )

    def readiness(self) -> AdapterReadiness:
        return _readiness_for(
            self._client,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def close(self) -> None:
        self._client.close()


def _chat_message_text(response: HostedResponse) -> str:
    """Extract the assistant text from a chat-completions payload, failing closed."""

    choices = response.data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelOutputError("the hosted model returned an unexpected response shape")
    choice = choices[0]
    if choice.get("finish_reason") not in (None, "stop"):
        raise ModelOutputError("the hosted model did not finish its answer")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelOutputError("the hosted model returned no assistant message")
    if message.get("refusal"):
        raise ModelOutputError("the hosted model declined the request")
    if message.get("tool_calls"):
        raise ModelOutputError("the hosted model returned unsupported content")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelOutputError("the hosted model returned an empty message")
    return content


class OpenAICompatibleTextUnderstanding:
    """Extract one command through any OpenAI-compatible ``/chat/completions`` API.

    ``structured_output`` states what the endpoint can enforce:

    * ``json_schema`` - strict JSON schema (the same union as the OpenAI path);
    * ``json_object`` - JSON mode only; the task contract is in the prompt;
    * ``prompt_only`` - no response-format support at all.

    In every case the answer is parsed against the same discriminated union and
    grounded; a provider that cannot enforce the schema simply fails closed more
    often.
    """

    adapter_name = "openai-compatible-chat"

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model_name: str,
        wake_phrase: str = "Jarvis",
        structured_output: Literal["json_schema", "json_object", "prompt_only"] = "json_object",
        temperature: float = 0.0,
        max_output_tokens: int = _MAX_COMMAND_OUTPUT_TOKENS,
        token_limit_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
        extra_body: Mapping[str, Any] | None = None,
        few_shot: bool = True,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._wake_phrase = wake_phrase
        self._reasoning_effort = reasoning_effort
        self._structured_output = structured_output
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._token_limit_field = token_limit_field
        self._extra_body = dict(extra_body or {})
        self._few_shot = few_shot

    def request_payload(self, transcript: str, context: CommandContext) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self._extra_body,
            "model": self.model_name,
            "messages": interpreter_messages(
                transcript,
                context,
                wake_phrase=self._wake_phrase,
                few_shot=self._few_shot,
            ),
            "temperature": self._temperature,
            self._token_limit_field: self._max_output_tokens,
            "stream": False,
        }
        if self._reasoning_effort is not None:
            payload["reasoning_effort"] = self._reasoning_effort
        if self._structured_output == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "labjarvis_command",
                    "schema": strict_command_json_schema(),
                    "strict": True,
                },
            }
        elif self._structured_output == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        response = self._client.post_json(
            "/chat/completions", self.request_payload(transcript, context)
        )
        parsed, basis = _parse_command_text(_chat_message_text(response), transcript)
        return TextResult(
            command=parsed,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata={
                **_provider_metadata(
                    response,
                    operation="chat.completions",
                    model=self.model_name,
                    client=self._client,
                ),
                **_command_metadata(basis),
                "structured_output": self._structured_output,
                "reasoning_effort": self._reasoning_effort or "provider-default",
                "max_output_tokens": str(self._max_output_tokens),
            },
        )

    def readiness(self) -> AdapterReadiness:
        return _readiness_for(
            self._client,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def close(self) -> None:
        self._client.close()


class OpenAILabelRecognition:
    """Transcribe visible reagent-label text through hosted image input."""

    adapter_name = "openai-responses-vision"
    _supported_types: ClassVar[set[str]] = {"image/jpeg", "image/png", "image/webp"}
    instruction: ClassVar[str] = (
        "Transcribe only the visible printed text on the label in this image. "
        "Return the text exactly as it appears. Do not identify the substance, "
        "judge whether it is correct or suitable, infer container contents, or "
        "give any safety advice. If no text is legible, return an empty string."
    )

    def __init__(self, client: OpenAIClient, *, model_name: str) -> None:
        self._client = client
        self.model_name = model_name

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
        if media_type not in self._supported_types:
            raise ModelInputError("unsupported image type")
        if not image:
            raise ModelInputError("image cannot be empty")

        encoded = base64.b64encode(image).decode("ascii")
        payload = {
            "model": self.model_name,
            "instructions": self.instruction,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Transcribe the visible label text."},
                        {
                            "type": "input_image",
                            "image_url": f"data:{media_type};base64,{encoded}",
                        },
                    ],
                }
            ],
            "max_output_tokens": _MAX_LABEL_OUTPUT_TOKENS,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "labjarvis_visible_label",
                    # Only visible text. The model is structurally unable to
                    # return a verification outcome; MATCH/MISMATCH/UNCERTAIN
                    # stay deterministic domain decisions.
                    "schema": {
                        "type": "object",
                        "properties": {
                            "visible_text": {
                                "type": "string",
                                "maxLength": _MAX_VISIBLE_LABEL_CHARACTERS,
                            }
                        },
                        "required": ["visible_text"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        }
        response = self._client.post_json("/responses", payload)
        try:
            label = _VisibleLabel.model_validate_json(_output_text(response))
        except ValidationError as exc:
            raise ModelOutputError("the hosted label response failed its contract") from exc
        visible = label.visible_text.strip()
        if not visible:
            raise ModelOutputError("the hosted label model returned no visible text")
        if len(visible) > _MAX_VISIBLE_LABEL_CHARACTERS:
            raise ModelOutputError("the hosted label model returned oversized text")
        return LabelResult(
            visible_text=visible,
            # No calibrated OCR confidence is exposed. Inventing one would
            # corrupt the deterministic reagent threshold comparison.
            confidence=None,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata=_provider_metadata(
                response,
                operation="responses",
                model=self.model_name,
                client=self._client,
            ),
        )

    def readiness(self) -> AdapterReadiness:
        return _readiness_for(
            self._client,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def close(self) -> None:
        self._client.close()


class OpenAICompatibleLabelRecognition:
    """Transcribe visible label text through an OpenAI-compatible vision chat model.

    Built only when the endpoint declares image input. The model is asked for
    the visible text as JSON and nothing else; MATCH/MISMATCH/UNCERTAIN stays a
    deterministic comparison in the domain.
    """

    adapter_name = "openai-compatible-vision"
    _supported_types: ClassVar[set[str]] = {"image/jpeg", "image/png", "image/webp"}

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model_name: str,
        json_mode: bool = True,
        token_limit_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._json_mode = json_mode
        self._token_limit_field = token_limit_field

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        media_type = content_type.split(";", maxsplit=1)[0].strip().casefold()
        if media_type not in self._supported_types:
            raise ModelInputError("unsupported image type")
        if not image:
            raise ModelInputError("image cannot be empty")
        encoded = base64.b64encode(image).decode("ascii")
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": OpenAILabelRecognition.instruction
                    + ' Reply only with JSON of the form {"visible_text": "..."}.',
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Transcribe the visible label text."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
            "temperature": 0,
            self._token_limit_field: _MAX_LABEL_OUTPUT_TOKENS,
            "stream": False,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self._client.post_json("/chat/completions", payload)
        try:
            label = _VisibleLabel.model_validate_json(_chat_message_text(response))
        except ValidationError as exc:
            raise ModelOutputError("the hosted label response failed its contract") from exc
        visible = label.visible_text.strip()
        if not visible:
            raise ModelOutputError("the hosted label model returned no visible text")
        if len(visible) > _MAX_VISIBLE_LABEL_CHARACTERS:
            raise ModelOutputError("the hosted label model returned oversized text")
        return LabelResult(
            visible_text=visible,
            confidence=None,
            adapter=self.adapter_name,
            model_name=self.model_name,
            metadata=_provider_metadata(
                response,
                operation="chat.completions",
                model=self.model_name,
                client=self._client,
            ),
        )

    def readiness(self) -> AdapterReadiness:
        return _readiness_for(
            self._client,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def close(self) -> None:
        self._client.close()
