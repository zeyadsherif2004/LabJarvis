# Test strategy

## Separation of concerns

Software tests answer whether LabJarvis implements its contracts and invariants. Model evaluation answers how well a candidate performs in the intended context. Perfect fake-adapter tests must never be reported as model accuracy.

## Test layers

| Layer | Location | Purpose |
|---|---|---|
| Domain unit | `apps/api/tests/unit/domain` | state transitions, provenance, command/unit canonicalization, entity grounding, confirmation and voice-only rules, frame consensus, live-scan rule, reagent boundaries |
| Application/evaluation unit | `apps/api/tests/unit/application`, `unit/evaluation` | tool registry, session orchestration, exports, metrics, safety gate, spend ledger |
| Infrastructure unit | `apps/api/tests/unit/infrastructure` | document abuse limits, fakes, bounded inference, command contract, rule grammar/cascade, local and hosted/OpenAI-compatible adapters, reasoning effort and locality, factory selection, study mode, warm-up, restart protocol (API and launcher sides), Kokoro voice output and its verified download |
| Configuration unit | `apps/api/tests/unit/test_settings.py`, `unit/test_saved_configuration.py` | settings validation, presets, Settings-page validation, source precedence, startup loading, OpenAI/DeepSeek connection separation |
| Stateful property | `apps/api/tests/property` | Hypothesis state machine driving the real service (1,000 sequences) and direct transition properties |
| API/DB integration | `apps/api/tests/integration` | principal transaction, failures, idempotency, rollback, retention, exports, repositories, clean migrations, evaluation runner, Settings API and supervised restart |
| Contract | `apps/api/tests/contract` | OpenAPI routes, schemas, declared errors/security headers |
| Model smoke | `apps/api/tests/model_smoke` | opt-in real WAV/text/image adapter sanity; skipped by default |
| Frontend component/workflow | `apps/web/src/**/*.test.ts(x)` | semantic UI behavior, confirmations, permission fallback, protocol editing, push-to-talk, hands-free listening and echo guard, voice phrases, live scan (shared vectors), Settings page, Kokoro playback and browser-voice fallback |
| Browser E2E | `apps/web/e2e` | principal journey, failure paths, resume/timers, route identity, hands-free (pedal) journey, **zero-touch journey** (`zero-touch.spec.ts`, second pass with the hands-free profile: one tap, then voice only; plus the kiosk launch with zero touches), accessibility (axe, 44 px targets, 320 px reflow, focus, reduced motion), screenshots |
| Research tooling | `research/post_review`, `research/analysis`, `research/experiments`, `research/study` | report-evidence scripts; run by `tasks.py test` and CI |
| Model evaluation | `evaluation/` | candidate metrics and context evidence; not a release unit test |
| Safety properties | `apps/api/src/labjarvis/evaluation/safety.py` | conflict handling, grounded abstention, and confirmation policy, measured against synthetic fixtures and gated on exit status |

### Suites worth knowing about by name

| Suite | What it protects |
|---|---|
| `integration/test_gateway_equivalence.py` | that a typed command, a push-to-talk recording, a realtime function call, and a button are held to the same legality, schema, and confirmation policy. The "one deterministic gateway" claim is checkable rather than aspirational |
| `integration/test_spoken_confirmation.py` | that a proposal can be answered out loud and only by a person: a bare "yes" cannot record a measurement, a wrong readback is refused, and the recording that produced the proposal cannot approve it |
| `integration/test_origin_guard.py` | that a hostile page cannot change state, asserted against the database rather than the status code |
| `integration/test_atomic_approval.py` | that review, approval, and session creation commit together, and that a retry after a lost response does not approve twice |
| `integration/test_repositories.py`, `test_resume_and_gateway.py` | one resolvable proposal per session, stale rejection/expiry recovery, and database-level race containment |
| `integration/test_voice_only_confirmation.py` | voice-only replies: value-bearing only, never a bare "yes", wrong/second value, unaddressed speech, echo, stale proposal, or a mismatching label |
| `integration/test_live_label_scan.py` | probes record nothing, stability authorises nothing, live scan refused with hosted OCR |
| `integration/test_reagent_integrity.py` | OCR outside the transaction followed by authoritative version/step revalidation, plus retry-safe upload and decision records |
| `integration/test_data_management.py`, `test_backup_restore.py` | aggregate deletion boundaries, content-free receipts, consistent backup manifests, and staged restore validation |
| `integration/test_settings_api.py` | Settings save/validation, no secret in any response, loopback-only changes, restart requests that cannot carry commands, active-session consent, records intact across restart, a key edited in `.env` used after restart, precedence warnings |
| `src/App.routes.test.tsx`, `e2e/route-identity.spec.ts` | that a route naming a missing resource can never leave the previous one on screen with live controls |
| `src/features/session/useRealtimeVoice.test.tsx` | reconnection, barge-in, current-version tool calls, and the confirmation cycle, with the provider replaced by a controllable double |

## Required local gates

```powershell
python scripts/tasks.py ci          # lint, typecheck, test, build, E2E, secret scan
python scripts/tasks.py readiness   # the same gates on a fresh copy of the working tree
python scripts/tasks.py evaluate
python scripts/tasks.py evaluate-safety
python scripts/tasks.py security    # pip-audit and pnpm audit need network access
```

`tasks.py test` runs backend tests, research-tooling tests and frontend tests;
`ci` adds lint, typecheck, build, the E2E journeys and the secret scan.
`readiness` copies exactly the files a commit would contain into a temporary
directory, runs every gate there with no `LABJARVIS_*` variables, and writes
`output/readiness/readiness.{md,json}` locally. Tests are
hermetic: autouse fixtures in `apps/api/tests/conftest.py` and
`research/conftest.py` remove ambient `LABJARVIS_*` variables, ignore `.env`
and ignore any configuration saved from the Settings page.

`evaluate-safety` is a **gate, not a report**: it exits nonzero when any
transcript-conflict, grounding, or tool-policy case fails, and the JSON it
writes carries `passed` and `failure_count` so a CI job and a person reading the
file cannot reach different conclusions. It previously printed its failures and
exited zero, which produced a record that looked like evidence of passing.

Backend coverage is gated at 85% in CI. Coverage is a diagnostic, not a substitute for boundary/failure assertions. CI also performs upgrade/downgrade/upgrade on a clean SQLite database, validates both Compose profiles, checks secrets, and audits dependency graphs.

Older test counts describe the commits on which they were measured. Run
`python scripts/readiness.py` to check a fresh copy of the current source.
It writes gate results, skips, and environment details to the ignored local
`output/readiness/` directory.

## Writing tests

- Assert externally meaningful state, records, and stable error codes; avoid implementation-call choreography unless testing an adapter boundary.
- Use the same public application/API contract in fake and real modes.
- Include negative and recovery behavior: no mutation after ambiguity, low confidence, timeout, invalid output, stale version, duplicate decision, or transaction failure.
- Use generated IDs/temp directories and isolate databases. Never use a real lab or participant file in the repository.
- Give synthetic media an explicit `FAKE:` marker and label results `synthetic_development_only`.
- For browser tests, start servers with `scripts/run_e2e.py`; it allocates isolated ports/database and reports useful process tails only on failure. It runs two passes: the default configuration, then `LABJARVIS_HANDS_FREE_PROFILE=true` for `zero-touch.spec.ts` (`--profile default|hands-free` runs one).
- The stateful property suite takes `LABJARVIS_PROPERTY_EXAMPLES=1000` for the evidence run (`research/final_report_evidence/verification/property-1000.json`).
- Automated accessibility scanning is a floor. Complete the [manual checklist](../accessibility-checklist.md) before a study/release.

## Opt-in real model smoke test

```powershell
$env:LABJARVIS_RUN_MODEL_TESTS="1"
$env:LABJARVIS_SMOKE_AUDIO="C:\private-fixtures\command.wav"
$env:LABJARVIS_SMOKE_IMAGE="C:\private-fixtures\label.jpg"
$env:LABJARVIS_SMOKE_SCANNED_PDF="C:\private-fixtures\scanned-protocol.pdf"
python scripts/tasks.py test-models
```

The text test also requires local Ollama with the configured model. Without the
opt-in and all private fixture paths, the four speech/text/label-OCR/document-OCR
tests are skipped rather than fabricated. A smoke pass proves wiring, not model
quality. Record model digests/hardware and use the evaluation harness for
performance claims.

## Hosted adapter tests

Hosted adapters are tested entirely through `httpx.MockTransport`. The default
suite makes no external request and consumes no paid provider usage. Cover the
endpoint, authentication header, request schema, configured model, success paths,
every mapped failure (401/403, 404, 429, 5xx, connection failure, timeout,
oversized response, malformed JSON, schema-invalid output), retry behaviour and
its ceiling, and client closure — plus an assertion that no credential or request
body appears in any error.

Relevant files: `tests/unit/infrastructure/test_hosted_client.py`,
`test_hosted_speech.py`, `test_hosted_text.py`, `test_hosted_vision.py`,
`test_openai_compatible.py`, `test_reasoning_locality_verifier.py`,
`test_command_contract.py`, and `tests/integration/test_hosted_workflow.py`.

`python scripts/tasks.py smoke-hosted` (with `LABJARVIS_RUN_HOSTED_SMOKE=1`) is
the provider-neutral smoke runner (`scripts/hosted_smoke.py` +
`scripts/hosted_smoke.yaml`); every call is authorised against the global
cumulative spend ledger in
`research/final_report_evidence/live/hosted-spend-ledger.json` ($2.00 cap).

## Opt-in live hosted smoke test

```powershell
$env:LABJARVIS_MODEL_MODE="hosted"
$env:LABJARVIS_ALLOW_HOSTED_MODELS="true"
$env:LABJARVIS_HOSTED_API_KEY="<from your secret store>"
$env:LABJARVIS_HOSTED_SPEECH_MODEL_NAME="gpt-4o-mini-transcribe"
$env:LABJARVIS_HOSTED_TEXT_MODEL_NAME="gpt-5.4-nano"
$env:LABJARVIS_HOSTED_VISION_MODEL_NAME="gpt-5.4-nano"
$env:LABJARVIS_RUN_HOSTED_SMOKE="1"
python scripts/tasks.py test-hosted-smoke
```

**This makes real, billable requests.** It is guarded by the `hosted_smoke`
marker, excluded from the default suite via `addopts`, and never runs in CI or
pre-commit. Both `LABJARVIS_RUN_HOSTED_SMOKE=1` and a configured key are
required, so a key present for other reasons cannot trigger paid calls.

Fixtures are synthetic. The test prints no key, response body, audio, image,
protocol content, or transcript.

A hosted smoke pass proves that the credential, base URL, and model identifiers
reach a live provider and return a parseable response. It proves nothing about
model quality, accuracy, or latency under load. Do not cite it as performance
evidence — use the evaluation harness with a hosted candidate for that.

