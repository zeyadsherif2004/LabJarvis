# Adding or comparing a model adapter

## Contract first

Choose the modality port in `apps/api/src/labjarvis/ports/models.py`:

- `SpeechToTextPort.transcribe(bytes, *, content_type, vocabulary=())` → transcript, confidence, adapter/model, metadata (`vocabulary` is optional protocol-derived context for conditioning);
- `TextUnderstandingPort.interpret(transcript, CommandContext)` → strict `ParsedCommand` in a `TextResult`;
- `LabelRecognitionPort.recognise(bytes, *, content_type)` → visible text, confidence, adapter/model, metadata;
- `DocumentOcrPort` for scanned-protocol OCR (EasyOCR-backed when vision is local).

Spoken responses are not a model port: the Kokoro voice
(`infrastructure/speech_output.py`, `SpeechSynthesisPort`) is presentation
only, never part of the record, and is served by `api/routes/speech_output.py`
([ADR 0020](../architecture/decisions/0020-local-neural-voice-output.md)).

Every adapter also exposes non-downloading readiness; local adapters may add
`warm_up()` (used by `LABJARVIS_WARM_MODELS_ON_STARTUP`). Application use cases
must not contain vendor-specific branches.

## Implementation checklist

1. Add centralized validated settings for model/revision/device/quantization/runtime parameters.
2. Implement the adapter under infrastructure and translate vendor failures into stable `ModelInputError`, `ModelOutputError`, or `ModelUnavailableError`.
3. Return no raw input in normal metadata. Include useful non-sensitive runtime/prompt/revision information.
4. Ensure normal construction/readiness does not download weights unless the explicit download setting is enabled.
5. Wire the adapter in `model_factory.py` (per-stage provider selection lives there); update its close/lifecycle behavior.
6. Extend the setup/download script and exact dependency lock. Never commit weights.
7. Add dependency-injected unit tests without a real network/model, a default-skip smoke test, readiness/failure tests, and unchanged principal-flow tests.
8. Record source, licence, approximate size, immutable revision/checksum/digest, date, hardware expectation, and known limitations in `docs/models/README.md`.
9. Add a candidate to `evaluation/configs/local-candidates.yaml`; do not replace the held-out dataset or thresholds after seeing results without disclosure.
10. Run software gates, real smoke, and evaluation on the target hardware. Keep the adapter provisional until the model-selection protocol supports acceptance.

## Text adapter safety

Use a closed JSON schema and deterministic decoding where supported. Delimit protocol context and transcript as untrusted data. The model may select only the documented intent allowlist; domain validation canonicalizes values and rejects missing/extra/unsafe slots. Do not give the model repository, filesystem, network-tool, or equipment capabilities.

The intent-discriminated schema (`command-contract-v3`) and the `ParsedCommand`
conversion live in `infrastructure/command_contract.py`; the task
specification, development-split few-shot examples and message envelope
(`interpreter-prompt-v3`, hashed) live in `infrastructure/interpreter_spec.py`;
per-model decoding options (thinking, timeouts, output limits) live in
`infrastructure/text_model_profiles.py`. All text adapters share them. Do not
copy them into a new adapter: independent copies of the intent allowlist drift.
A new text adapter supplies transport only; it is normally wrapped by
`CascadeTextUnderstanding` (`infrastructure/cascade.py`), which runs the rule
grammar first and applies entity grounding, agreement and the optional verifier
([ADR 0015](../architecture/decisions/0015-deterministic-first-cascade.md)).

## Result envelopes

All three ports return frozen result types carrying provenance:

| Port | Returns |
|---|---|
| `SpeechToTextPort.transcribe` | `SpeechResult(transcript, confidence, adapter, model_name, metadata)` |
| `TextUnderstandingPort.interpret` | `TextResult(command, adapter, model_name, metadata)` |
| `LabelRecognitionPort.recognise` | `LabelResult(visible_text, confidence, adapter, model_name, metadata)` |

`metadata` is wrapped in a `MappingProxyType` and must carry only safe values:
provider, provider request ID, exact configured model identifier, API operation,
token or duration usage the provider actually supplied, retry count, and
finish/status reason. Never place raw authorization headers, keys, whole provider
responses, hidden reasoning, request bodies, images, audio, protocol content, or
transcripts in it.

`confidence` must be `None` unless the adapter has a genuine calibrated
probability. Deriving one from token logprobs or an unrelated score invents a
number the confidence threshold and reagent comparison will then treat as
meaningful. The hosted adapters return `None` for exactly this reason.

## Hosted adapters

`infrastructure/hosted_models.py` holds the hosted adapters: OpenAI (Responses
API for text and vision, `/audio/transcriptions` for speech) and
OpenAI-compatible (`/chat/completions` for text and vision, transcription when
declared). `infrastructure/model_factory.py` chooses a provider per stage from
settings, and `Settings.connection_for` resolves each provider's own endpoint
and key (OpenAI and DeepSeek never share one), so DeepSeek, GLM, a gateway or a
local vLLM/LM Studio server is configuration, not code ([ADR 0016](../architecture/decisions/0016-per-stage-provider-neutral-hosting.md)).
A provider with a different wire protocol would need a sibling adapter plus a
branch in `model_factory.py` - no route, domain type, schema, or frontend
change.

Requirements specific to hosted adapters:

- share one injected client so connections pool and closure is deterministic;
- accept an `httpx.BaseTransport` so tests use `httpx.MockTransport` and make no
  paid call;
- read responses under a byte cap before JSON decoding;
- retry only 429 and 5xx and connection errors, never 4xx or schema failures,
  and never beyond the configured ceiling;
- sanitise every exception message — no provider body, request body, or
  credential;
- validate the content of every response part, failing closed on refusals and
  unexpected shapes.

See [ADR 0016](../architecture/decisions/0016-per-stage-provider-neutral-hosting.md)
for provider selection and locality.

## Comparison discipline

Compare candidates on identical, consented, versioned inputs and report:

- exact model/runtime/configuration and warm/cold condition;
- per-sample failures and error examples, not only means;
- speech WER/CER/real-time factor;
- text intent, slot precision/recall/F1, schema, unsupported/unsafe/ambiguity behavior;
- vision text metrics, verification accuracy, `MATCH` precision/recall/F1, false accepts/uncertainty;
- latency and process RSS on the named hardware.

Prefer the smallest model that meets predeclared context/safety requirements. Reject a candidate honestly if it does not.

