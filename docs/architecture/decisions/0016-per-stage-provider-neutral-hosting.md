# ADR 0016: Per-stage, provider-neutral model execution

- **Status:** Accepted (2026-09-26/27).
- **Code:** `settings.py` (`StageProvider`, per-stage settings, `stage_locality`), `infrastructure/model_factory.py`, `infrastructure/hosted_models.py`, `api/routes/health.py` (`stages`), `apps/web/src/components/ModelStages.tsx`, `evaluation/spend.py`.

## Context

The earlier hosted profile made execution all-local or all-hosted with OpenAI.
Evaluation showed
that the text stage benefits most from a stronger hosted model while speech and
label OCR are fast and adequate locally, that other providers (DeepSeek, GLM)
expose OpenAI-compatible APIs, and that some local model servers (vLLM,
llama.cpp, LM Studio) speak the same protocol on `localhost`.

## Decision

1. Each stage - speech, text, vision - has its own provider: `fake`, `local`,
   `openai` or `openai_compatible`, with its own base URL, key, model identifier
   and display label. `LABJARVIS_MODEL_MODE` only supplies the default.
2. OpenAI-compatible endpoints are used through `/chat/completions` with strict
   JSON schema, JSON mode or prompt-only output as declared; image input and
   transcription must be declared explicitly or startup fails. Provider-specific
   request fields are allowed only through a validated `extra_body` that cannot
   override the model, messages, tools or streaming.
3. Reasoning effort is a per-stage setting and is recorded with each call.
4. Locality is derived from the endpoint: a loopback base URL is **local**; any
   other hosted endpoint is **hosted**. Readiness, the header chips, Diagnostics
   and every inference row state the provider label and locality.
5. The cascade's verifier model may run on its own provider.
6. Evaluation tooling that makes paid calls must authorise every request
   against a global, cumulative spend ledger before sending it.

## Consequences

- Fully local, hybrid and fully hosted profiles are configuration only; all
  three completed the demo scenarios end to end (deviation D8b).
- Operators can see exactly which stage sends data where; the provider label is
  operator-supplied text and cannot be verified by LabJarvis.
- Live label scanning is refused when the vision stage is hosted, because it
  would send a frame every few hundred milliseconds.
- Realtime voice remains OpenAI-only; it depends on OpenAI's realtime protocol.
