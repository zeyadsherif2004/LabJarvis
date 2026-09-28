# Privacy and retention operations

## Defaults

| Data | Default | Persisted evidence |
|---|---|---|
| Uploaded protocol | source text + SHA-256 retained in approved version | steps, source references, source hash/text |
| Raw audio | discarded after inference | SHA-256, adapter/model, timing/confidence, retention flag |
| Transcript | retained by default; independently disable | transcript or its SHA-256/disabled marker plus structured result |
| Raw label image | discarded after inference | SHA-256, OCR/model metadata, verification proposal/decision |
| Live-scan probe frames | never stored or recorded (not even hashes) | only the frames of the final stable reading are processed as a label image |
| Hands-free listening audio | segments without the wake phrase are discarded before inference, retention or any record | as raw audio, for addressed segments |
| Spoken replies (Kokoro) | synthesised on this machine and never stored | none; the text is what the screen already shows |
| Settings choices | `<data dir>/settings.json` | providers, models, switches only; never a key, URL or path |
| Notebook/audit | retained in database | append-only entries, corrections, confirmations, correlations |
| Evaluation predictions | omitted in real config by default | hashes and metrics; optional protected results |

Raw retention requires `LABJARVIS_RETAIN_RAW_MEDIA=true`; retained blobs use content-derived generated paths under the configured data directory. The setting applies prospectively and does not delete old files. Transcript retention is `LABJARVIS_RETAIN_TRANSCRIPTS`.

## Hosted mode changes the trust boundary

The table above describes data **on this machine**. In fake and local mode that
is the whole picture: model inference never leaves the workstation.

Any hosted stage changes that: the Hosted or Hybrid profile or a hosted
stage in Custom (Settings), `LABJARVIS_MODEL_MODE=hosted`, a per-stage provider
(`LABJARVIS_SPEECH_PROVIDER` / `TEXT_PROVIDER` / `VISION_PROVIDER` set to
`openai`, `deepseek` or `openai_compatible` with a non-loopback URL), a hosted
text verifier, or realtime voice. Each needs **Allow hosted processing**
(`LABJARVIS_ALLOW_HOSTED_MODELS`). Spoken replies never leave the machine: the
Kokoro voice runs locally and the fallback is the browser's own voice. Readiness reports `external_model_processing: true`
and the header chips name the provider for each hosted stage. Per
operator-initiated action, only the hosted stage's input is transmitted:

| Hosted stage | Transmitted |
|---|---|
| Speech (incl. the high-integrity second reading) | the audio clip |
| Text (incl. a hosted verifier) | the transcript and the active step instruction, truncated, inside a delimited data envelope, plus fixed task instructions and development-split examples |
| Vision | the label image(s) |
| Realtime voice | a live audio stream while the realtime session is open |

An OpenAI-compatible server on `localhost`/`127.0.0.1` is local: nothing leaves
the machine. Live label scanning is refused when vision is hosted.

Nothing is transmitted before the operator starts an action. Protocol history,
notebook history, audit history, database exports, and unrelated session data
are never sent — only the minimum content for that one modality.

**The local retention settings above do not govern the provider.** Disabling
`LABJARVIS_RETAIN_RAW_MEDIA` stops LabJarvis writing a blob to this disk; it has
no effect on what the provider does with the copy it received.

Provider-side retention, training use, data residency, and deletion are governed
entirely by the terms of the account whose key is configured. LabJarvis cannot
observe or enforce them. This project makes **no claim** of end-to-end
encryption, zero retention, regulatory compliance, data residency, or training
opt-out for hosted mode. If any of those matter for your data, verify them
against your own provider account configuration and contract before enabling
hosted mode — do not infer them from this document.

Institutional approval may be required before sending laboratory or participant
data to a third-party provider, even where the same data may be processed
locally without additional approval. Treat enabling hosted mode as a change to
your approved data-handling plan, not a configuration detail.

Credentials are read from `.env` or the environment only. A key is never
accepted through the browser, stored in the database or `settings.json`,
written to logs, included in readiness or Settings responses (which report only
whether a key is configured and where from), exceptions, or exports, or
committed to Git.

## Before collecting real data

1. Determine the lawful/ethical basis and obtain university approval/consent where applicable.
2. Minimize content: use non-sensitive test protocols, short commands, and label-only images.
3. State what is collected, why, who can access it, location, encryption, duration, withdrawal/deletion method, and whether hashes/results remain after deletion.
4. Use a dedicated encrypted directory outside Git/cloud sync unless explicitly approved.
5. Test the selected settings/readiness display and deletion procedure with synthetic data.
6. Keep identity/contact/consent keys separate from pseudonymous media/results.

## Deletion and withdrawal

LabJarvis does not implement an automatic retention scheduler. Its local
**Data** screen can first export a session, create/validate a SQLite backup, and
delete one complete session aggregate after the
operator types `DELETE <session-id>` exactly. Database rows are removed in one
transaction; retained raw media is removed immediately afterward. The operation
writes a content-free receipt outside the aggregate containing a generated
deletion id, timestamp, object type, hash of the local id, and whether media
cleanup completed. If cleanup reports `media_cleanup_required`, finish and
document that cleanup under the approved plan.

Protocols referenced by any session are never deleted. An unused all-draft
protocol, or an archived protocol with no sessions, can be deleted with the same
typed confirmation. Archive anything that must remain available as historical
session provenance.

Local aggregate deletion does not remove exports, backups, screenshots, model
runtime traces, synchronized copies, or data already sent to a hosted provider.
Identify and handle those copies under the approved retention/withdrawal plan;
do not promise their deletion merely because the active LabJarvis aggregate is
gone. The receipt intentionally cannot identify a participant without the
separately held mapping.

A backup is an independent retained copy. Creating one before deletion is
useful for accidental-loss recovery but conflicts with a request to erase that
same data unless the approved plan also identifies and deletes or expires the
backup. Restore validation copies a verified package into staging and never
replaces the active database while the service is running.

For normal development cleanup, `python scripts/tasks.py clean` removes local app/output/generated evaluation data. It is deliberately destructive and is not a compliant participant deletion workflow on its own.

## Logs, traces, screenshots, and exports

Normal application logs omit content, but Playwright traces/screenshots, browser developer tools, model runtime logs, OS crash dumps, shell history, exports, and manual screenshots can still capture it. Use synthetic sessions for repository/demo screenshots. Review every artifact before publication.

JSON/CSV exports and database backups should inherit the highest sensitivity of their source session. Do not email them or commit them. Use access control/encryption and an approved transfer route.

## Incident response

If sensitive data is exposed: stop further synchronization/sharing, preserve minimal incident facts without copying content, notify the supervisor/university data or ethics contact under policy, rotate any affected secrets, determine copies/recipients, and follow required breach/participant notification. Do not conceal the incident or attempt an undocumented destructive cleanup.
