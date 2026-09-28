# API guide

The FastAPI application (`apps/api/src/labjarvis/api/app.py`) exposes OpenAPI at
`/openapi.json`, Swagger UI at `/docs` and ReDoc at `/redoc` while running.
Product routes live under `/api/v1` (data management under `/api/v1/data`);
health routes are unversioned. The Vite dev server and the nginx container both
proxy `/api/` and `/health/` to the API. `tests/contract/test_openapi.py` keeps
this list and the schemas honest.

## Headers

- `If-Match: <numeric revision>` - required where the table says **If-Match**
  (protocol-version edits/approval, session start, commands, tool invocations,
  reagent uploads and decisions). Quoted/weak numeric ETags are accepted; the
  current revision is returned in the JSON body, not as an `ETag` header.
- `Idempotency-Key: <unique value>` (max 120 characters) - required where the
  table says **Idem**. Replaying the same key with the same request returns the
  stored result; reusing it for different input returns `409 IDEMPOTENCY_CONFLICT`.
- `X-Correlation-ID` - optional; echoed, or generated when absent.
- State-changing requests whose `Origin` is not an allowed CORS origin are
  refused before routing (see [SECURITY.md](../../SECURITY.md)).

## Endpoints

### Health

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live` | process liveness |
| GET | `/health/ready` | per-adapter readiness, per-stage provider/locality/model (`stages`), execution `profile`, warm-up state, privacy flags, `interaction` settings (wake phrase, hands-free listening, voice-only confirmation, live-scan parameters, realtime), `speech_output` (Kokoro or browser voice, and whether Kokoro is ready), study-mode banner data. `503` means a model is not usable yet, not that the API is down |

### Protocols

| Method | Path | Headers | Notes |
|---|---|---|---|
| GET | `/api/v1/protocols` | | discovery; query `limit` (1-100), `q`, `archived`, `from`, `to` |
| POST | `/api/v1/protocols/upload` | | multipart `file` (PDF/DOCX, bounded); `?ocr=true` for an image-only PDF; creates a draft (`201`) |
| GET | `/api/v1/protocols/{protocol_id}` | | protocol identity and versions |
| POST | `/api/v1/protocols/{protocol_id}/archive` | Idem | hide from normal discovery |
| POST | `/api/v1/protocols/{protocol_id}/restore` | Idem | undo archive |
| GET | `/api/v1/protocol-versions/{version_id}` | | reviewed steps, revision, status, source hash |
| PUT | `/api/v1/protocol-versions/{version_id}` | If-Match | full replacement of `steps` while draft |
| GET | `/api/v1/protocol-versions/{version_id}/source` | | preserved source text/excerpts |
| POST | `/api/v1/protocol-versions/{version_id}/approve` | If-Match | freeze the reviewed version |
| POST | `/api/v1/protocol-versions/{version_id}/approve-and-start` | If-Match | approve and create a session in one transaction (`201`) |
| POST | `/api/v1/protocol-versions/{version_id}/review-approve-and-start` | If-Match, Idem | save reviewed `steps`, approve and create a session atomically (`201`) |
| POST | `/api/v1/protocol-versions/{version_id}/copy` | Idem | copy an approved version into a new draft (`201`) |

### Sessions, commands and tools

| Method | Path | Headers | Notes |
|---|---|---|---|
| GET | `/api/v1/tools` | | the tool registry (names, parameters, confirmation policy, legal states) the UI and realtime layer read |
| GET | `/api/v1/sessions` | | discovery; query `limit`, `resumable_only`, `q`, `protocol_version_id`, `label`, `state`, `from`, `to`, `protocol_archived` |
| POST | `/api/v1/sessions` | | `{ "protocol_version_id": "...", "label": optional }` (`201`) |
| GET | `/api/v1/sessions/{session_id}` | | state, steps, active step, version |
| GET | `/api/v1/sessions/{session_id}/snapshot` | | everything needed to rebuild the session view: session, version, timers, pending action, pending reading, requirement progress, entries |
| GET | `/api/v1/sessions/{session_id}/timers` | | durable timers with server-derived remaining time |
| POST | `/api/v1/sessions/{session_id}/start` | If-Match | approved protocol required |
| POST | `/api/v1/sessions/{session_id}/commands/text` | If-Match, Idem | `{ "transcript": "..." }` (`202`) |
| POST | `/api/v1/sessions/{session_id}/commands/audio` | If-Match, Idem | multipart `file`; `?require_wake_phrase=true` (hands-free listener) discards speech not starting with the wake phrase and answers intent `WAKE_PHRASE_ABSENT` without retaining anything (`202`). `?listen_mode=standby` accepts only "start listening"; `?listen_mode=barge_in` accepts only "stop"/"quiet", "mute" and "stop the alarm"; anything else in those modes returns `LISTEN_MODE_IGNORED` untraced. Interface controls return their intent (`STOP_LISTENING`, `START_LISTENING`, `MUTE_SPEECH`, `UNMUTE_SPEECH`, `REPEAT_LAST_SPOKEN`, `STOP_SPEAKING`, `ACKNOWLEDGE_ALARM`) and never change the record. `parameters` carries a resolved `requirement_id` for "check the ethanol" (ADR 0018) |
| POST | `/api/v1/sessions/{session_id}/tools/{tool_name}` | If-Match, Idem | `{ "parameters": {...} }`; direct UI controls and realtime function calls go through the same gateway |

### Proposals and confirmations

| Method | Path | Headers | Notes |
|---|---|---|---|
| POST | `/api/v1/pending-actions/{action_id}/confirm` | | `{ "accepted": true/false }`; rechecks the expected session version |
| POST | `/api/v1/pending-actions/{action_id}/spoken-confirmation` | Idem | multipart `file` (a fresh recording); optional `?expected_version=`; `?via_wake_phrase=true` for voice-only mode (the reply must start with the wake phrase and restate the value; unaddressed speech returns decision `not_addressed` and writes nothing). Needs the high-integrity transcriber for read-back. A reply that is an interface control returns decision `control` with `control` set; the proposal keeps waiting |

### Reagent labels

| Method | Path | Headers | Notes |
|---|---|---|---|
| POST | `/api/v1/sessions/{session_id}/reagent-verifications` | If-Match, Idem | one image `file`; optional `requirement_id`; returns a **pending** reading |
| POST | `/api/v1/sessions/{session_id}/reagent-verifications/frames` | If-Match, Idem | 1-5 image `frames`, unanimous consensus; optional `requirement_id` and provenance-only `capture_mode` (`single`/`countdown`/`live_scan`), `scan_rule`, `scan_frames_examined`, `scan_elapsed_ms` |
| POST | `/api/v1/sessions/{session_id}/reagent-scans/probe` | | live-scan probe: one frame read by the local OCR and compared; **nothing is recorded**. `503 LIVE_LABEL_SCAN_UNAVAILABLE` unless enabled |
| POST | `/api/v1/reagent-verifications/{verification_id}/confirm` | If-Match, Idem | `{ "accepted": bool, "corrected_observed_label": optional }` |
| POST | `/api/v1/reagent-verifications/{verification_id}/spoken-decision` | If-Match, Idem | voice-only mode: "Jarvis, confirm <reagent>" confirms a MATCH; "Jarvis, reject" rejects any reading; a MISMATCH/UNCERTAIN reading is never confirmed by voice |

### Notebook

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/sessions/{session_id}/notebook` | entries, inferences, verifications, audit events, protocol version |
| POST | `/api/v1/notebook/{entry_id}/corrections` | replacement payload; appends a correction that supersedes the entry (`201`) |
| GET | `/api/v1/sessions/{session_id}/notebook/export?format=json\|csv` | JSON: full `labjarvis.session` 2.0 aggregate; CSV: flattened notebook subset |

### Realtime voice (optional)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/realtime/sessions` | mints a short-lived, tool-scoped grant; refused unless realtime is enabled with an OpenAI key |

### Settings and restart

Model/execution choices are saved here and applied at the next start
([ADR 0019](../architecture/decisions/0019-in-app-configuration-applied-at-startup.md)).
`PUT` and `/restart` are accepted only from the loopback address. No response
contains a key, endpoint URL or provider body.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/settings` | `active` and `saved` configuration, `next_start`, `restart_required`, `restart.available`/`reason`, `overrides` (saved choices an environment variable overrides), `credentials` (configured yes/no and source `.env`/`environment`, never the value), `warnings`, provider options, presets, Kokoro status and voices, `active_sessions`, `instance_id` |
| PUT | `/api/v1/settings` | the full choice (`profile`, `speech`/`text`/`vision` `{provider, model, device, reasoning_effort}`, `allow_hosted_processing`, `hands_free`, `high_integrity_transcription`, `high_integrity_model`, `voice_output`, `voice`); unknown fields rejected; invalid choices return `422 CONFIGURATION_INVALID` with every reason in `problems`, and nothing is saved |
| POST | `/api/v1/settings/restart` | body `{ "acknowledge_active_session": bool }` only (`202`). `409 RESTART_UNAVAILABLE` without the `tasks.py dev` supervisor, `409 SAVED_CONFIGURATION_INVALID` if the saved choice would not start, `409 RESTART_CONFIRMATION_REQUIRED` during an active session without consent |

### Voice output

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/speech-output/synthesize` | `{ "text": 1-1000 chars, "voice": optional (Settings preview) }` -> `audio/wav` spoken by the local Kokoro voice; nothing is stored. `409 SPEECH_OUTPUT_BROWSER` when the browser voice is configured, `503 SPEECH_OUTPUT_UNAVAILABLE` when Kokoro is not installed; the browser then speaks with its own voice ([ADR 0020](../architecture/decisions/0020-local-neural-voice-output.md)) |

### Data management

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/data/backups` | consistent SQLite backup package (`201`); refused for PostgreSQL |
| POST | `/api/v1/data/backups/{backup_name}/validate` | re-hash, schema and integrity check, copy into `restore-staging`; never activates |
| DELETE | `/api/v1/data/sessions/{session_id}` | body `{ "confirmation": "DELETE <session-id>" }`; content-free receipt |
| DELETE | `/api/v1/data/protocols/{protocol_id}` | same typed confirmation; refused while a session references it |

## Command contract

Commands are interpreted by the configured text interpreter (by default a
deterministic grammar first, then the language model; see
[ADR 0015](../architecture/decisions/0015-deterministic-first-cascade.md)). The
model may only return one of the intent-discriminated branches in
`infrastructure/command_contract.py`: `RECORD_MEASUREMENT`, `RECORD_OBSERVATION`,
`SET_TIMER`, `PAUSE_TIMER`, `RESUME_TIMER`, `CANCEL_TIMER`, `START_SESSION`,
`COMPLETE_STEP`, `SKIP_STEP_WITH_REASON`, `PAUSE_SESSION`, `RESUME_SESSION`,
`END_SESSION`, `GET_CURRENT_STEP`, `GET_NEXT_STEP`, `REPEAT_INSTRUCTION`,
`GET_PROTOCOL_STATE`, `GET_EXPECTED_REAGENTS`, `GET_ACTIVE_TIMERS`,
`READ_LAST_ENTRY`, `ANSWER_PROTOCOL_QUESTION`, `VERIFY_REAGENT`,
`CORRECT_PREVIOUS_ENTRY`, `HELP` or `UNSUPPORTED` (`command-contract-v3`). Models report no confidence;
the value returned as `confidence` is derived deterministically from how the
command was obtained (grammar match, grounded model proposal, agreement) and
whether its values appear in the utterance.

The response carries transcript, intent, parameters, confidence, ambiguities,
the visible grounded response, an optional pending action and replay status.
Read-only guidance is returned immediately; consequential proposals must be
resolved through a confirmation endpoint.

## Errors

Failures use `application/problem+json`:

```json
{
  "type": "https://labjarvis.local/problems/if-match-required",
  "title": "If Match Required",
  "status": 428,
  "detail": "If-Match version header is required",
  "code": "IF_MATCH_REQUIRED",
  "correlation_id": "..."
}
```

Status families (from `api/errors.py`): `400` invalid headers, `404` codes ending
`_NOT_FOUND`, `409` conflicts (`*CONFLICT*`, `PENDING_ACTION_EXISTS`,
`PENDING_ACTION_RESOLVED`, `REAGENT_VERIFICATION_PENDING/RESOLVED/STALE`,
`CONFIRMATION_AUDIO_REPLAYED`, `VOICE_ONLY_CONFIRMATION_DISABLED`, ...), `413`
size limits, `415` unsupported media, `422` other application/validation errors,
`428` missing preconditions, `502` invalid model output, `503` model unavailable,
timeout, or a control the deployment has not configured (codes ending
`_UNAVAILABLE`). Unexpected errors omit internal details.

## Example: typed command

```powershell
$headers = @{
  "If-Match" = "1"
  "Idempotency-Key" = [guid]::NewGuid().ToString()
  "Content-Type" = "application/json"
}
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/sessions/<session-id>/commands/text" `
  -Headers $headers `
  -Body '{"transcript":"Record the temperature as 37 degrees Celsius"}'
```

Use generated development data only in examples.
