# Architecture overview

## Architectural intent

LabJarvis uses AI only at unstructured perception/interpretation boundaries. The application service orchestrates use cases; framework-free domain code owns legal transitions, canonical command values, label comparison, and append-only provenance. Model proposals cannot call repositories. Consequential actions and OCR readings remain pending until an operator resolves them.

The pragmatic dependency rule is:

```text
React / FastAPI → application use cases → domain + ports
SQLAlchemy / model vendors / file storage → port implementations
```

The API is process-local and synchronous at the use-case boundary. Potentially blocking extraction and inference are dispatched from async routes to a thread pool, then passed through a bounded inference runner with a timeout and semaphore.

## C4 level 1: system context

```mermaid
flowchart TB
    Operator["Person: lab operator\nReviews protocol and confirms every consequential decision"]
    Supervisor["Person: supervisor / assessor\nDefines laboratory procedure and reviews evidence"]
    LabJarvis["Software system: LabJarvis\nLocal protocol guidance, capture, verification, and audit"]
    Browser["System: browser media APIs\nMicrophone, camera, speech synthesis, in-browser VAD"]
    Models["Systems: local model runtimes\nfaster-whisper, Ollama, EasyOCR, Kokoro voice"]
    Hosted["Systems: optional hosted providers\nOpenAI, DeepSeek, OpenAI-compatible, per stage"]
    Files["External input/output\nPDF/DOCX/TXT/Markdown protocols and JSON/CSV exports"]

    Operator -->|"reviewed commands and decisions"| LabJarvis
    Supervisor -->|"approved procedures / reviews records"| LabJarvis
    LabJarvis --> Browser
    LabJarvis --> Models
    LabJarvis -. "explicit opt-in only" .-> Hosted
    Files <--> LabJarvis
```

No third-party cloud service is part of the default context. Each model stage
can be switched to a hosted provider independently
([ADR 0016](decisions/0016-per-stage-provider-neutral-hosting.md)).
Spoken output is the same grounded visible message, voiced by the local Kokoro
model on the server or, as a fallback, the browser's own speech synthesis
(ADR 0020).

## C4 level 2: containers

```mermaid
flowchart LR
    subgraph Device["Operator workstation"]
        UI["Web UI\nReact 19, TypeScript, Vite\nsetup, review, sterile mode, notebook"]
        API["API\nFastAPI, Pydantic\nHTTP contracts, limits, Problem Details"]
        Core["Application + domain\nuse cases, state machine, deterministic validation"]
        DB[("SQLite by default\nPostgreSQL profile")]
        Raw[("Content-addressed raw media\nexplicit opt-in only")]
        FW["faster-whisper\nCTranslate2 speech (+ optional second transcriber)"]
        OL["Rule grammar, then Ollama + Qwen2.5\nintent-discriminated command proposal"]
        EO["EasyOCR\nlabel text proposal"]
        TTS["Kokoro (ONNX Runtime)\nspoken replies, not recorded"]
        Cfg[("settings.json + .env\nconfiguration, read at startup")]
        Sup["tasks.py dev supervisor\nrestarts the API on request"]
    end
    HP["Optional hosted providers\n(any stage, per-stage opt-in)"]

    User["Operator"] --> UI
    UI -->|"versioned JSON/multipart HTTP"| API
    API --> Core
    Core --> DB
    Core -. "if retention enabled" .-> Raw
    Core --> FW
    Core --> OL
    Core --> EO
    Core -. "hosted stages only" .-> HP
    API --> TTS
    Cfg -. "startup" .-> API
    Sup -. "starts / restarts" .-> API
```

In fake mode, deterministic in-process adapters replace all three model
runtimes. `infrastructure/model_factory.py` is the only place that knows which
provider runs each stage; the web interface and application use cases do not
branch on vendor. A loopback OpenAI-compatible server counts as local.

## Configuration and restart

Model and execution choices are made on the Settings page, validated by the
same `Settings` rules as any other source, saved to `<data dir>/settings.json`
and applied only at startup; nothing is hot-swapped in a running process
([ADR 0019](decisions/0019-in-app-configuration-applied-at-startup.md)).
Precedence is per variable: process environment, saved choice, `.env`,
default. Keys and endpoint URLs come only from `.env`/the environment, and
`Settings.connection_for` keeps each named provider on its own endpoint and key.
**Restart LabJarvis** is one narrow action: the loopback-only endpoint writes a
token-bearing marker for the `tasks.py dev` supervisor and stops the API along
the graceful Ctrl+C path; the supervisor restarts only its fixed command.

## C4 level 3: API components

```mermaid
flowchart TB
    Routes["FastAPI routes + schemas"]
    Middleware["Correlation, security headers, CORS, trusted hosts, error mapping"]
    Service["LabJarvisService\ncompatibility facade"]
    Workflows["Focused application workflows\nsession orchestration · confirmation\nreagent verification · protocol lifecycle\nnotebook/export · model provenance"]
    Domain["Domain\nstate machine, command canonicaliser, entity grounding,\nreagent classifier + frame consensus, confirmation and\nvoice-confirmation rules, wake phrase, live-scan rule, grounding"]
    Interpreter["Text interpreter\nrule grammar · cascade/agreement/verifier · command contract"]
    ModelPorts["Model ports\nSpeechToText, TextUnderstanding, LabelRecognition, InferenceRunner"]
    RepoPorts["Repository/UoW ports\nprotocol, session, notebook, inference, verification, audit, idempotency"]
    MediaPort["RawMediaStorePort"]
    Real["Local and hosted model adapters"]
    Fake["Deterministic fake adapters"]
    SQL["SQLAlchemy unit of work"]
    Store["Discarding / content-addressed media stores"]

    Middleware --> Routes
    Routes --> Service
    Service --> Workflows
    Workflows --> Domain
    Workflows --> ModelPorts
    Workflows --> RepoPorts
    Workflows --> MediaPort
    Real --> ModelPorts
    Fake --> ModelPorts
    Interpreter --> ModelPorts
    SQL --> RepoPorts
    Store --> MediaPort
```

`LabJarvisService` remains the stable API-facing facade while focused application
services own session discovery/restoration, confirmation resolution, reagent
verification, protocol lifecycle, notebook/export, and provenance workflows.
Slow OCR runs outside a database transaction; the workflow then reopens a unit
of work and revalidates the active session, step, requirement, version, and
idempotency record before committing. Domain types import no web, database, or
model libraries. Infrastructure may depend on domain/ports, never the reverse.

## Protocol import sequence

```mermaid
sequenceDiagram
    actor U as Operator
    participant UI as React UI
    participant API as FastAPI route
    participant P as Bounded parser
    participant S as Application service
    participant DB as Unit of work

    U->>UI: Choose PDF/DOCX/TXT/Markdown; explicitly opt into OCR for a scan
    UI->>API: Multipart upload
    API->>API: Stream to byte limit; verify media type
    API->>P: Extract in worker thread
    P->>P: PDF page/time/OCR limit or DOCX archive/path/size checks
    P-->>S: Source text, SHA-256, step proposals
    S->>DB: Store protocol + editable draft version
    DB-->>UI: Draft with source references
    U->>UI: Edit/reorder/add/remove steps and requirements
    UI->>S: Update with expected revision
    S->>DB: Optimistic revision update
    U->>S: Approve reviewed revision
    S->>DB: Mark immutable approved version
```

Extraction is deterministic in the current scope. Protocol text is data, never executable instructions to a model or the application.

## Voice command sequence

```mermaid
sequenceDiagram
    actor U as Operator
    participant UI as Sterile-mode UI
    participant API as Session route
    participant STT as SpeechToTextPort
    participant NLU as TextUnderstandingPort
    participant D as Domain validation
    participant DB as Unit of work

    U->>UI: Push-to-talk (button, key or pedal) or wake-phrase segment
    UI->>API: Audio + session version + idempotency key
    API->>STT: Bounded inference
    STT-->>API: Transcript, confidence, runtime metadata
    API->>NLU: Transcript + active reviewed instruction
    NLU-->>API: Grammar match or grounded model proposal (or abstention)
    API->>D: Canonicalize and validate intent/slots/state
    alt Read-only grounded request
        D-->>UI: Exact reviewed current/next instruction
    else Consequential command
        API->>DB: Persist inference + pending action
        API-->>UI: Confirmation proposal
        U->>UI: Confirm or reject
        UI->>API: Resolve pending action
        API->>D: Recheck expected session version
        API->>DB: Append notebook + audit; update state atomically
        API-->>UI: Visible and spoken grounded result
    else Low confidence, ambiguity, invalid output, or timeout
        API->>DB: Append safe inference/audit failure metadata
        API-->>UI: Non-mutating recovery guidance
    end
```

Push-to-talk works from the on-screen button or a configurable key anywhere on
the page (a foot pedal can emulate it); capture is bounded to 30 seconds. With
hands-free listening enabled, an in-browser VAD sends each speech segment with
`require_wake_phrase`, and the server discards any segment not starting with the
wake phrase before interpretation or retention. A proposal is answered through
`/pending-actions/{id}/confirm` (screen) or `/spoken-confirmation` (a fresh
recording, read by the high-integrity transcriber; measurements need the value
read back). In voice-only mode a wake-phrase reply must restate the value being
confirmed ([ADR 0017](decisions/0017-hands-free-listening-and-voice-only-confirmation.md)).

## Reagent verification sequence

```mermaid
sequenceDiagram
    actor U as Operator
    participant UI as Camera UI
    participant API as Session service
    participant OCR as LabelRecognitionPort
    participant C as Deterministic classifier
    participant DB as Unit of work

    U->>UI: Show/capture label (single frame, 3-frame countdown, or live scan)
    UI->>API: Bounded image(s) + expected session version
    API->>OCR: Recognize visible text (each frame)
    OCR-->>API: Proposed text, confidence, model metadata
    API->>C: Compare normalized text/aliases to reviewed requirement
    C-->>API: MATCH / MISMATCH / UNCERTAIN + score + thresholds
    API->>DB: Persist inference + PENDING verification
    API-->>UI: Show expected, observed, score, outcome, limitations
    U->>UI: Confirm, reject, or correct visible text
    UI->>API: Explicit decision
    API->>C: Recompute if corrected
    API->>DB: Append verification decision + notebook + audit
```

With several frames the outcome is the unanimous consensus (`unanimous-v1`):
any disagreement or unreadable frame becomes `UNCERTAIN`. Live scanning probes
frames through `/reagent-scans/probe` without recording anything until the
stability rule holds, then submits the stable frames as above. A `MATCH` means
only that visible OCR text met the configured comparison threshold. It does not
establish what is inside the bottle; a confirmed non-matching reading is
recorded as `not_matching`, never as a completed review.

## Logical data model

Steps and protocol requirements are immutable value objects serialized in reviewed version/session snapshots. Other evidence has its own append-oriented table.

```mermaid
erDiagram
    PROTOCOLS ||--o{ PROTOCOL_VERSIONS : versions
    PROTOCOL_VERSIONS ||--o{ SESSIONS : instantiates
    SESSIONS ||--o{ PENDING_ACTIONS : proposes
    SESSIONS ||--o{ NOTEBOOK_ENTRIES : records
    SESSIONS ||--o{ MODEL_INFERENCES : observes
    SESSIONS ||--o{ REAGENT_VERIFICATIONS : checks
    SESSIONS ||--o{ AUDIT_EVENTS : audits
    NOTEBOOK_ENTRIES o|--o{ NOTEBOOK_ENTRIES : supersedes
    MODEL_INFERENCES o|--o{ NOTEBOOK_ENTRIES : supports
    MODEL_INFERENCES o|--o{ REAGENT_VERIFICATIONS : supports
    SESSIONS ||--o{ SESSION_TIMERS : times
    SESSIONS ||--o{ UTTERANCES : hears
    PENDING_ACTIONS o|--o{ UTTERANCES : answered_by

    PROTOCOLS {
      string id PK
      string title
      datetime created_at
    }
    PROTOCOL_VERSIONS {
      string id PK
      string protocol_id FK
      int version_number
      int revision
      string status
      text steps_json
      string source_sha256
      text source_text
    }
    SESSIONS {
      string id PK
      string protocol_version_id FK
      string state
      text steps_json
      string active_step_id
      int version
      string label
    }
    PENDING_ACTIONS {
      string id PK
      string session_id FK
      string intent
      text payload_json
      string status
      int expected_session_version
    }
    NOTEBOOK_ENTRIES {
      string id PK
      string session_id FK
      string entry_type
      string source
      text payload_json
      string supersedes_entry_id
      string model_inference_id
    }
    MODEL_INFERENCES {
      string id PK
      string session_id FK
      string modality
      string adapter
      string model_name
      string status
      float confidence
      int latency_ms
      string input_sha256
    }
    REAGENT_VERIFICATIONS {
      string id PK
      string session_id FK
      string expected_label
      string observed_label
      string outcome
      float score
      string decision
    }
    AUDIT_EVENTS {
      string id PK
      string session_id FK
      string event_type
      string actor
      string correlation_id
    }
    SESSION_TIMERS {
      string id PK
      string session_id FK
      string state
      datetime deadline_at
    }
    UTTERANCES {
      string id PK
      string session_id FK
      string purpose
      string agreement
      string decision
      string audio_sha256
    }
```

The authoritative schema is `infrastructure/db/schema.py` plus the Alembic
revisions `0001`-`0009` in `apps/api/migrations/versions`; the diagram shows the
main fields only.

Protocol rows also carry an optional archive timestamp. Backup manifests and
content-free deletion receipts live outside session aggregates: a session can
be deleted without deleting the receipt that proves the operation occurred,
and deleting active data does not silently rewrite older backups.

Idempotency records use `(scope, key)` as a composite primary key and retain a request hash plus serialized response. Corrections append a new notebook row whose `supersedes_entry_id` points at the previous entry.

## Session state machine

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> READY: approved protocol selected
    READY --> ACTIVE: start
    READY --> ABORTED: abort
    ACTIVE --> PAUSED: pause
    PAUSED --> ACTIVE: resume
    ACTIVE --> COMPLETED: final step completed
    ACTIVE --> ABORTED: confirmed early end
    PAUSED --> ABORTED: confirmed early end
    COMPLETED --> [*]
    ABORTED --> [*]
```

## Step state machine

```mermaid
stateDiagram-v2
    [*] --> NOT_STARTED
    NOT_STARTED --> IN_PROGRESS: activate
    NOT_STARTED --> SKIPPED_WITH_REASON: skip with reason
    IN_PROGRESS --> COMPLETED
    IN_PROGRESS --> SKIPPED_WITH_REASON: skip with reason
    COMPLETED --> [*]
    SKIPPED_WITH_REASON --> [*]
```

Only one step may be active. Pending operator actions and reagent-label reviews are
orthogonal records rather than step states. Completing the active step activates the next
reviewed step, or completes the session. Optimistic versions prevent a stale confirmation
from applying after another state change. Historical `AWAITING_*` values are migrated and
read as `IN_PROGRESS` without changing their associated pending records.

## Local deployment

```mermaid
flowchart TB
    subgraph Host["Single local workstation"]
        Browser["Browser\n127.0.0.1:5173 dev / :8080 container"]
        Web["Vite dev server or nginx static container"]
        API["Uvicorn/FastAPI\n127.0.0.1:8000"]
        SQLite[("SQLite volume")]
        PostgreSQL[("Optional PostgreSQL container")]
        Ollama["Optional Ollama\n127.0.0.1:11434"]
        LocalLLM["Optional local OpenAI-compatible server\n(counts as local)"]
        PythonModels["Optional local Python model cache"]
        Media["Optional retained-media directory"]
    end

    Browser --> Web
    Web -->|"/api + /health proxy"| API
    API --> SQLite
    API -. "profile override" .-> PostgreSQL
    API -. "local mode" .-> Ollama
    API -. "local mode" .-> PythonModels
    API -. "retention opt-in" .-> Media
    API -. "compatible provider on loopback" .-> LocalLLM
    API -. "hosted stage opt-in" .-> Cloud["Hosted provider (off-host)"]
```

Compose publishes only to `127.0.0.1`. The system has no authentication because non-loopback/multi-user deployment is outside the implemented security boundary.

## Cross-cutting controls

- **Boundary validation:** strict Pydantic schemas, allowlisted intents, canonical Decimal/unit handling, content/type/page/archive/character limits.
- **Consistency:** SQLAlchemy unit of work, foreign keys, optimistic versions, idempotency keys, immutable approved protocol versions.
- **Model containment:** input hashes, strict structured output, timeout, concurrency bound, confidence/ambiguity gates, no direct persistence.
- **Privacy:** raw media discarded by default; transcript retention independent; normal logs carry no bodies; content-addressed opt-in store uses generated paths.
- **Traceability:** model, adapter, latency, confidence, timestamp, correlation ID, confirmation, verification decision, and correction lineage.
- **Recovery:** stable Problem Details codes, typed fallback when media permissions fail, non-mutating low-confidence/unsupported paths, readiness diagnostics.
- **Lifecycle/data integrity:** immutable approved versions, archive/copy semantics, typed aggregate deletion, consistent SQLite backup manifests, hash/schema/integrity validation, and offline-only restore activation.

## Decision records

The overview above describes the application split, ports, persistence, and retention. The [selected decisions](decisions/README.md) give more detail on the state machine, realtime boundary, transcription, grounding, confirmation, cascade, per-stage providers, hands-free workflow, settings, and voice output.
