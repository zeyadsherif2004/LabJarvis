export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  correlation_id: string;
  /** Every reason a proposed configuration was refused (CONFIGURATION_INVALID). */
  problems?: string[];
}

/**
 * Staged readiness for one model adapter.
 *
 * A single boolean could not distinguish "the package is installed" from "the
 * model can actually run", which is exactly the difference an operator needs to
 * know before relying on speech at a bench. Stages that do not apply to a
 * profile are null rather than false.
 */
export interface ReadinessStage {
  status: "ready" | "blocked" | "not_applicable";
  detail: string;
}

export interface AdapterReadiness {
  ready: boolean;
  adapter?: string;
  model_name?: string;
  detail?: string;
  configured?: ReadinessStage;
  installed?: ReadinessStage;
  cached?: ReadinessStage;
  loaded?: ReadinessStage;
  reachable?: ReadinessStage;
  inference_verified?: ReadinessStage;
  /** The first blocked stage, or null when no prerequisite blocks use. */
  first_blocking_stage?: string | null;
}

export type ModelMode = "fake" | "local" | "hosted";

export interface StageProvenance {
  provider: string;
  provider_label: string;
  locality: "fake" | "local" | "hosted";
  model: string;
  detail: string;
  warmup: string;
}

export interface ReadinessResponse {
  /** Who speaks responses; Kokoro not ready means the browser voice is used. */
  speech_output?: {
    engine: "kokoro" | "browser";
    voice: string;
    ready: boolean;
    loaded?: boolean;
    detail: string;
  };
  /** fake, local, hosted, or hybrid across speech/text/vision. */
  profile?: string;
  stages?: Record<string, StageProvenance>;
  /** Present when the deployment runs a consented seeded-error study session. */
  study?: {
    enabled: boolean;
    study_id?: string;
    participant?: string;
    condition?: "tap" | "readback";
    block?: string;
  };
  status: "ready" | "not_ready";
  mode: ModelMode;
  /** Present only in hosted mode. Never carries credentials. */
  hosted_provider?: string;
  tool_contract_version: string;
  interaction: {
    wake_phrase: string;
    realtime_enabled: boolean;
    realtime_transport: "webrtc" | "websocket";
    realtime_voice: string;
    /** The exact configured model id, or "" when realtime is disabled. */
    realtime_model: string;
    /** How long a spoken confirmation may be recorded for. */
    realtime_confirmation_seconds: number;
    /** The deployment offers the optional in-browser VAD + wake-phrase listener. */
    hands_free_listening?: boolean;
    /** The wake-phrase listener may answer proposals with value-bearing replies. */
    voice_only_confirmation?: boolean;
    /** Live "scan until stable" label checking (local OCR only). */
    live_label_scan?: boolean;
    live_scan_required_consecutive?: number;
    live_scan_interval_ms?: number;
    live_scan_timeout_ms?: number;
    /** The one-switch hands-free profile is on (ADR 0018). */
    hands_free_profile?: boolean;
    /** Start listening when a session screen opens, after one tap. */
    hands_free_autostart?: boolean;
    /** Speak and sound elapsed-timer alarms until acknowledged. */
    timer_alarms?: boolean;
    timer_alarm_repeat_seconds?: number;
    /** Comma-separated seconds-remaining warnings, e.g. "60,10". */
    timer_warning_seconds?: string;
    /** "Jarvis, confirm observation" is accepted (opt-in, weaker). */
    voice_observations?: boolean;
    spoken_detail?: "brief" | "full";
  };
  /** One "Hands-free: ready" line, or exactly what is missing. */
  hands_free?: {
    profile?: boolean;
    ready?: boolean;
    summary?: string;
    missing?: string[];
  };
  privacy: {
    raw_media_retention: boolean;
    transcript_retention: boolean;
    /** True when model inference leaves this machine for a hosted provider. */
    external_model_processing: boolean;
  };
  adapters: {
    speech: AdapterReadiness;
    text: AdapterReadiness;
    vision: AdapterReadiness;
    document_ocr?: AdapterReadiness;
  };
  database: { ready: boolean };
  latency_window?: {
    max_samples: number;
    sample_count: number;
    modalities: Record<
      string,
      {
        sample_count: number;
        failed_count: number;
        average_ms: number;
        p50_ms: number;
        p95_ms: number;
      }
    >;
  };
}

export interface SourceReference {
  page: number;
  section: string | null;
  excerpt: string;
  ocr_derived?: boolean;
}

export interface ProtocolRequirement {
  /** Stable address for this reagent within its step. */
  requirement_id: string;
  name: string;
  quantity: string | null;
  unit: string | null;
  aliases: string[];
}

/**
 * Something import could not settle from the source text - a range, an unclear
 * time, a restarted numbering. The server will not approve a version while any
 * flag is unacknowledged.
 */
export interface ExtractionFlag {
  code: string;
  message: string;
  excerpt: string | null;
  acknowledged: boolean;
}

/** Source text that import did not place in any step, kept for review. */
export interface UnassignedSourceText {
  page: number;
  section: string | null;
  text: string;
  reason: string;
}

export interface ProtocolStep {
  id: string;
  number: number;
  title: string;
  instruction: string;
  requirements: ProtocolRequirement[];
  duration_seconds: number | null;
  safety_notes: string[];
  source: SourceReference;
  parallel_allowed: boolean;
  flags?: ExtractionFlag[];
}

export interface ProtocolVersion {
  id: string;
  protocol_id: string;
  version_number: number;
  revision: number;
  status: "DRAFT" | "APPROVED" | "RETIRED";
  steps: ProtocolStep[];
  source_sha256: string;
  created_at: string;
  approved_at: string | null;
}

/** The complete extracted text a protocol version was structured from. */
export interface ProtocolSource {
  version_id: string;
  source_sha256: string;
  text: string;
  character_count: number;
  unassigned_text?: UnassignedSourceText[];
}

export interface ProtocolImportResponse {
  protocol_id: string;
  title: string;
  version: ProtocolVersion;
}

export interface ProtocolRecord {
  id: string;
  title: string;
  created_at: string;
  archived_at: string | null;
}

export type SessionState = "READY" | "ACTIVE" | "PAUSED" | "COMPLETED" | "ABORTED";

export interface SessionStep {
  step_id: string;
  state: string;
  started_at: string | null;
  completed_at: string | null;
  skipped_reason: string | null;
}

export interface SessionResponse {
  id: string;
  protocol_version_id: string;
  state: SessionState;
  steps: SessionStep[];
  active_step_id: string | null;
  version: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  label: string | null;
}

export interface PendingAction {
  id: string;
  session_id: string;
  intent: string;
  payload: Record<string, string>;
  source: string;
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "EXPIRED";
  expected_session_version: number;
  created_at: string;
  correlation_id: string;
}

export interface CommandResponse {
  transcript: string;
  intent: string;
  confidence: number;
  ambiguities: string[];
  pending_action: PendingAction | null;
  response_text: string;
  replayed: boolean;
  /** Resolved references, e.g. which reagent requirement "check the ethanol" chose. */
  parameters?: Record<string, string>;
}

export interface NotebookEntry {
  id: string;
  session_id: string;
  entry_type: string;
  source: string;
  payload: Record<string, string>;
  created_at: string;
  correlation_id: string;
  supersedes_entry_id: string | null;
  model_inference_id: string | null;
}

export interface ConfirmationResponse {
  session: SessionResponse;
  pending_action: PendingAction;
  entry: NotebookEntry | null;
}

/**
 * The outcome of answering a proposal out loud.
 *
 * `committed` is authoritative and `response_text` is the exact wording to
 * speak. `decision` is "ambiguous" whenever the reply was not clearly a yes or
 * a no, in which case the proposal is still open and nothing was recorded.
 */
/** One live-scan reading from the local OCR. Never persisted. */
export interface LabelProbe {
  outcome: "MATCH" | "MISMATCH" | "UNCERTAIN" | "UNREADABLE";
  observed_normalised: string;
  visible_text: string;
  score: number;
  requirement_id: string;
  expected_label: string;
}

/** How a reading's frames were chosen; recorded as provenance only. */
export interface CaptureMetadata {
  mode: "single" | "countdown" | "live_scan";
  scanRule?: string;
  framesExamined?: number;
  elapsedMs?: number;
}

export interface SpokenReagentDecision {
  decision: "confirm" | "reject" | "ambiguous" | "not_addressed" | "not_supported" | "control";
  committed: boolean;
  verification: ReagentVerification;
  response_text: string;
  reasons: string[];
  /** Set when the reply was an interface control such as STOP_SPEAKING. */
  control?: string;
}

export interface SpokenConfirmation {
  decision: "confirm" | "reject" | "ambiguous" | "not_addressed" | "control";
  /** Set when the reply was an interface control; the proposal still waits. */
  control?: string;
  committed: boolean;
  session: SessionResponse;
  pending_action: PendingAction;
  entry: NotebookEntry | null;
  response_text: string;
  utterance_id: string;
  heard: string;
  reasons: string[];
  replayed: boolean;
}

export type VerificationOutcome = "MATCH" | "MISMATCH" | "UNCERTAIN";

export interface ReagentVerification {
  id: string;
  session_id: string;
  step_id: string;
  requirement_id: string;
  expected_label: string;
  observed_label: string;
  outcome: VerificationOutcome;
  score: number;
  expected_normalised: string;
  observed_normalised: string;
  match_threshold: number;
  uncertain_threshold: number;
  model_confidence: number | null;
  decision: "PENDING" | "CONFIRMED" | "REJECTED" | "CORRECTED" | "STALE";
  created_at: string;
  resolved_at: string | null;
}

export interface NotebookResponse {
  protocol_version_id: string;
  entries: NotebookEntry[];
  inferences: ModelInferenceRecord[];
  verifications: ReagentVerification[];
  audit_events: AuditEventRecord[];
  utterances: UtteranceRecord[];
}

/**
 * One spoken input and every reading taken of it.
 *
 * `agreement` is how the two independent readings compared. `canonical_transcript`
 * is what the system acted on, and is empty when they conflicted - in that case
 * nothing was acted on at all.
 */
export interface UtteranceRecord {
  id: string;
  realtime_transcript: string;
  verification_transcript: string;
  canonical_transcript: string;
  agreement: string;
  reasons: string[];
  primary_model: string;
  verification_model: string;
  primary_inference_id: string;
  verification_inference_id: string;
  confirmed_transcript: string;
  created_at: string;
  correlation_id: string;
}

export interface ModelInferenceRecord {
  id: string;
  modality: string;
  adapter: string;
  model_name: string;
  status: string;
  confidence: number | null;
  latency_ms: number;
  input_sha256: string;
  output: Record<string, string>;
  created_at: string;
}

export interface AuditEventRecord {
  id: string;
  event_type: string;
  actor: string;
  correlation_id: string;
  details: Record<string, string>;
  created_at: string;
}

// ---------------------------------------------------------------- discovery

export interface ProtocolSummary {
  protocol_id: string;
  title: string;
  created_at: string;
  latest_version_id: string;
  latest_version_status: "DRAFT" | "APPROVED" | "RETIRED";
  latest_revision: number;
  step_count: number;
  session_count: number;
  archived_at: string | null;
}

export interface SessionSummary {
  session_id: string;
  protocol_id: string;
  protocol_title: string;
  protocol_version_id: string;
  state: SessionState;
  version: number;
  step_count: number;
  completed_step_count: number;
  active_step_number: number | null;
  resumable: boolean;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  label: string | null;
}

// ---------------------------------------------------------- data management

export interface DeletionReceipt {
  deletion_id: string;
  deleted_at: string;
  object_type: string;
  /** One-way identifier retained for audit without retaining the deleted ID. */
  object_id_hash: string;
  cleanup_state: string;
}

export interface BackupFile {
  path: string;
  size: number;
  sha256: string;
}

export interface BackupManifest {
  format_version: string;
  created_at: string;
  schema_revision: string;
  application_commit: string | null;
  profile: string;
  files: BackupFile[];
}

export interface BackupPackage {
  backup_name: string;
  manifest: BackupManifest;
}

export interface RestoreValidation {
  backup_name: string;
  staging_name: string;
  manifest: BackupManifest;
  activation_required: boolean;
}

// ------------------------------------------------------------------ timers

export type TimerState = "RUNNING" | "PAUSED" | "ELAPSED" | "CANCELLED";

/**
 * A durable timer. `remaining_seconds` is the server's authoritative value at
 * the moment of the response; the client may tick it down locally for smooth
 * display, but must re-read rather than trust a long-running local counter.
 */
export interface SessionTimer {
  id: string;
  session_id: string;
  step_id: string | null;
  label: string;
  duration_seconds: number;
  state: TimerState;
  remaining_seconds: number;
  /** Elapsed and not yet acknowledged; derived by the server on every read. */
  alarm_active?: boolean;
  started_at: string;
  deadline_at: string | null;
  paused_at: string | null;
  resolved_at: string | null;
}

// ------------------------------------------------------------ tool gateway

export type ToolKind = "read_only" | "mutating";
export type ConfirmationPolicy = "none" | "always" | "model_proposals_only";

export interface ToolDescriptor {
  name: string;
  version: string;
  kind: ToolKind;
  confirmation: ConfirmationPolicy;
  summary: string;
  legal_session_states: string[];
  required_parameters: string[];
  realtime_exposed: boolean;
}

export interface ToolRegistry {
  contract_version: string;
  tools: ToolDescriptor[];
}

/**
 * The outcome of one gateway invocation. `committed` and `pending_action` are
 * mutually exclusive, so the UI can never present a proposal as a done deal.
 */
export interface ToolInvocation {
  tool: string;
  committed: boolean;
  replayed: boolean;
  message: string;
  session: SessionResponse;
  pending_action: PendingAction | null;
  timers: SessionTimer[];
  entry: NotebookEntry | null;
  data: Record<string, string>;
}

export interface GroundedAnswer {
  text: string;
  abstained: boolean;
  citation: string | null;
  page: number | null;
  stepId: string | null;
  relevance: number | null;
}

// --------------------------------------------------------------- snapshots

export type RequirementReviewStatus =
  | "unverified"
  | "pending_review"
  | "confirmed"
  | "not_matching"
  | "rejected"
  | "stale";

export interface RequirementProgress {
  requirement_id: string;
  name: string;
  status: RequirementReviewStatus;
  verification_id: string | null;
}

/** Everything needed to restore a session view without any client memory. */
export interface SessionSnapshot {
  session: SessionResponse;
  protocol_id: string;
  protocol_title: string;
  version: ProtocolVersion;
  timers: SessionTimer[];
  pending_action: PendingAction | null;
  pending_verification: ReagentVerification | null;
  requirement_progress: RequirementProgress[];
  entries: NotebookEntry[];
}

export interface ApproveAndStartResponse {
  version: ProtocolVersion;
  session: SessionResponse;
}

// ---------------------------------------------------------------- realtime

/**
 * A short-lived realtime grant. `client_secret` expires within minutes and is
 * already scoped to one model, one voice, and one tool allowlist; the account
 * key never reaches the browser.
 */
export interface RealtimeGrant {
  client_secret: string;
  expires_at: string;
  model_name: string;
  voice: string;
  transport: "webrtc" | "websocket";
  provider: string;
  provider_session_id: string;
  tool_names: string[];
  max_utterance_seconds: number;
  idle_timeout_seconds: number;
  snapshot: SessionSnapshot;
}

export type RealtimeStatus =
  | "idle"
  | "requesting_permission"
  | "connecting"
  | "listening"
  | "processing"
  | "speaking"
  | "awaiting_confirmation"
  | "capturing_confirmation"
  | "reconnecting"
  | "muted"
  | "stopped"
  | "error";

// ------------------------------------------------------------- configuration

export type StageName = "speech" | "text" | "vision";
export type StageProviderChoice = "local" | "openai" | "deepseek" | "openai_compatible";
export type ConfigurationProfile = "local" | "hosted" | "hybrid" | "custom";

export interface StageChoice {
  provider: StageProviderChoice;
  model: string;
  device: "cpu" | "gpu";
  reasoning_effort: string | null;
}

/** What the Settings page saves. Never a key, URL or path. */
export interface ModelConfiguration {
  profile: ConfigurationProfile;
  speech: StageChoice;
  text: StageChoice;
  vision: StageChoice;
  allow_hosted_processing: boolean;
  hands_free: boolean;
  high_integrity_transcription: boolean;
  high_integrity_model: string;
  /** Kokoro (local neural voice, recommended) or the browser voice. */
  voice_output: "kokoro" | "browser";
  voice: string;
}

/** A configuration as it runs (or will run); a stage may be the dev fixture. */
export interface ConfigurationProjection {
  speech: Omit<StageChoice, "provider"> & { provider: StageProviderChoice | "fake" };
  text: Omit<StageChoice, "provider"> & { provider: StageProviderChoice | "fake" };
  vision: Omit<StageChoice, "provider"> & { provider: StageProviderChoice | "fake" };
  allow_hosted_processing: boolean;
  hands_free: boolean;
  high_integrity_transcription: boolean;
  high_integrity_model: string;
  voice_output: "kokoro" | "browser";
  voice: string;
}

export interface ConfigurationOverride {
  field: string;
  saved: unknown;
  effective: unknown;
  variables: string[];
}

export interface ProviderOption {
  value: StageProviderChoice;
  label: string;
  locality: "local" | "hosted";
  /** Whether this provider's API key is configured on the server (never the key). */
  configured: boolean;
}

export interface ConfigurationPreset {
  label: string;
  description: string;
  speech?: StageChoice;
  text?: StageChoice;
  vision?: StageChoice;
}

export interface ConfigurationState {
  instance_id: string;
  active: ConfigurationProjection & { profile: ConfigurationProfile | "development" };
  saved: ModelConfiguration | null;
  saved_ignored: boolean;
  next_start: ConfigurationProjection | null;
  next_start_problem: string | null;
  restart_required: boolean;
  overrides: ConfigurationOverride[];
  restart: { available: boolean; reason: string | null };
  active_sessions: number;
  credentials: {
    id: string;
    label: string;
    configured: boolean;
    /** Where the key in use comes from; never the key itself. */
    source: ".env" | "environment" | null;
    variable: string | null;
  }[];
  /** Anything that would make configuration behave differently than it looks. */
  warnings: string[];
  options: {
    providers: Record<StageName, ProviderOption[]>;
    model_suggestions: Record<StageName, Record<string, string[]>>;
    reasoning_efforts: string[];
  };
  presets: Record<ConfigurationProfile, ConfigurationPreset>;
  settings_file: string;
  voice_output: { kokoro_installed: boolean; kokoro_detail: string; voices: string[] };
}

export interface RestartAccepted {
  restarting: boolean;
  instance_id: string;
}
