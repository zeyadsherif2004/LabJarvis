import type {
  ApproveAndStartResponse,
  BackupPackage,
  CommandResponse,
  ConfirmationResponse,
  DeletionReceipt,
  NotebookEntry,
  NotebookResponse,
  ProblemDetail,
  ProtocolImportResponse,
  ProtocolRecord,
  ProtocolStep,
  ProtocolSource,
  ProtocolSummary,
  ProtocolVersion,
  ReadinessResponse,
  ReagentVerification,
  RealtimeGrant,
  RestoreValidation,
  SessionResponse,
  SessionSnapshot,
  SessionSummary,
  SessionTimer,
  SpokenConfirmation,
  SpokenReagentDecision,
  CaptureMetadata,
  LabelProbe,
  ToolInvocation,
  ToolRegistry,
  ConfigurationState,
  ModelConfiguration,
  RestartAccepted,
} from "../types";


export class ApiProblem extends Error {
  public constructor(public readonly problem: ProblemDetail) {
    super(problem.detail);
    this.name = "ApiProblem";
  }
}

/**
 * The request never reached the API at all - DNS, connection refused, CORS, or
 * the process not running.
 *
 * This is deliberately a different type from {@link ApiProblem}. A 503 from
 * `/health/ready` means the API answered and told us a dependency is not usable
 * yet, which the operator fixes by installing a model or starting Ollama.
 * Reporting that as "cannot reach the API" sent people to look at the wrong
 * thing entirely.
 */
export class ApiUnreachable extends Error {
  public constructor(cause: unknown) {
    super("The LabJarvis API did not respond.");
    this.name = "ApiUnreachable";
    this.cause = cause;
  }
}

/** Readiness plus whether the service considered itself ready (HTTP 200 vs 503). */
export interface ReadinessProbe {
  readiness: ReadinessResponse;
  degraded: boolean;
}

export interface ClientPort {
  getReadiness(): Promise<ReadinessProbe>;
  getTools(): Promise<ToolRegistry>;
  listProtocols(options?: {
    query?: string;
    archived?: boolean;
    createdFrom?: string;
    createdTo?: string;
  }, signal?: AbortSignal): Promise<ProtocolSummary[]>;
  listSessions(options?: {
    resumableOnly?: boolean;
    query?: string;
    protocolVersionId?: string;
    label?: string;
    state?: string;
    createdFrom?: string;
    createdTo?: string;
    protocolArchived?: boolean;
  }, signal?: AbortSignal): Promise<SessionSummary[]>;
  getSession(sessionId: string): Promise<SessionResponse>;
  getSessionSnapshot(sessionId: string): Promise<SessionSnapshot>;
  getTimers(sessionId: string): Promise<SessionTimer[]>;
  uploadProtocol(file: File, useOcr?: boolean): Promise<ProtocolImportResponse>;
  reviewProtocol(
    versionId: string,
    steps: ProtocolStep[],
    revision: number,
  ): Promise<ProtocolVersion>;
  approveProtocol(versionId: string, revision: number): Promise<ProtocolVersion>;
  approveAndStart(versionId: string, revision: number): Promise<ApproveAndStartResponse>;
  reviewApproveAndStart(
    versionId: string,
    steps: ProtocolStep[],
    revision: number,
    idempotencyKey: string,
    label?: string | null,
  ): Promise<ApproveAndStartResponse>;
  getProtocolVersion(versionId: string): Promise<ProtocolVersion>;
  getProtocol(protocolId: string): Promise<ProtocolRecord>;
  getProtocolSource(versionId: string): Promise<ProtocolSource>;
  copyProtocolVersion(versionId: string): Promise<ProtocolVersion>;
  archiveProtocol(protocolId: string): Promise<ProtocolRecord>;
  restoreProtocol(protocolId: string): Promise<ProtocolRecord>;
  createSession(protocolVersionId: string, label?: string | null): Promise<SessionResponse>;
  startSession(sessionId: string, version: number): Promise<SessionResponse>;
  invokeTool(
    sessionId: string,
    tool: string,
    version: number,
    parameters?: Record<string, string>,
    idempotencyKey?: string,
  ): Promise<ToolInvocation>;
  createRealtimeSession(sessionId: string): Promise<RealtimeGrant>;
  submitTextCommand(
    sessionId: string,
    transcript: string,
    version: number,
    idempotencyKey: string,
  ): Promise<CommandResponse>;
  submitAudioCommand(
    sessionId: string,
    audio: Blob,
    version: number,
    idempotencyKey: string,
    options?: { requireWakePhrase?: boolean; listenMode?: "command" | "standby" | "barge_in" },
  ): Promise<CommandResponse>;
  confirmAction(actionId: string, accepted: boolean): Promise<ConfirmationResponse>;
  confirmActionByVoice(
    actionId: string,
    audio: Blob,
    expectedVersion: number,
    idempotencyKey: string,
    options?: { viaWakePhrase?: boolean },
  ): Promise<SpokenConfirmation>;
  /** Voice-only mode: answer a pending label reading with a wake-phrase reply. */
  decideReagentVerificationByVoice(
    verificationId: string,
    audio: Blob,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<SpokenReagentDecision>;
  verifyReagent(
    sessionId: string,
    image: Blob,
    requirementId: string | null,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<ReagentVerification>;
  /** Several frames of one label, answered with their deterministic consensus. */
  verifyReagentFrames(
    sessionId: string,
    frames: Blob[],
    requirementId: string | null,
    expectedVersion: number,
    idempotencyKey: string,
    capture?: CaptureMetadata,
  ): Promise<ReagentVerification>;
  /** Live scan: read one frame with the local OCR; the server records nothing. */
  probeReagentLabel(
    sessionId: string,
    frame: Blob,
    requirementId: string | null,
  ): Promise<LabelProbe>;
  confirmReagentVerification(
    verificationId: string,
    accepted: boolean,
    correctedObservedLabel: string | null,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<ReagentVerification>;
  getNotebook(sessionId: string): Promise<NotebookResponse>;
  correctNotebookEntry(
    entryId: string,
    payload: Record<string, string>,
  ): Promise<NotebookEntry>;
  exportNotebook(sessionId: string, format: "json" | "csv"): string;
  deleteSession(sessionId: string, confirmation: string): Promise<DeletionReceipt>;
  deleteProtocol(protocolId: string, confirmation: string): Promise<DeletionReceipt>;
  createBackup(): Promise<BackupPackage>;
  validateBackup(backupName: string): Promise<RestoreValidation>;
  /** Active and saved configuration, and whether a restart is needed. */
  getConfiguration(): Promise<ConfigurationState>;
  /** Validate and save; applies on the next start. Rejects with every problem. */
  saveConfiguration(configuration: ModelConfiguration): Promise<ConfigurationState>;
  /** Ask the supervising launcher to restart LabJarvis. */
  restartLabJarvis(acknowledgeActiveSession: boolean): Promise<RestartAccepted>;
  /** A WAV of ``text`` in the local Kokoro voice (``voice`` only for previews). */
  synthesizeSpeech(
    text: string,
    options?: { voice?: string; signal?: AbortSignal },
  ): Promise<ArrayBuffer>;
}

function correlationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now().toString(36)}`;
}

export class LabJarvisClient implements ClientPort {
  public constructor(private readonly baseUrl = "") {}

  private async send(path: string, init: RequestInit = {}): Promise<Response> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Correlation-ID": correlationId(),
      ...(init.headers as Record<string, string> | undefined),
    };
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    try {
      return await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
      // fetch only rejects when the request never completed. Everything the
      // service actually answered - including 503 - arrives as a Response.
      throw new ApiUnreachable(cause);
    }
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.send(path, init);
    if (!response.ok) {
      const contentType = response.headers.get("Content-Type") ?? "";
      if (contentType.includes("application/problem+json")) {
        throw new ApiProblem((await response.json()) as ProblemDetail);
      }
      throw new ApiProblem({
        type: "about:blank",
        title: "Request failed",
        status: response.status,
        detail: "The LabJarvis service returned an unexpected response.",
        code: "HTTP_ERROR",
        correlation_id: response.headers.get("X-Correlation-ID") ?? "unavailable",
      });
    }
    return (await response.json()) as T;
  }

  /**
   * Read readiness, treating 503 as a *reported* degraded state rather than a
   * failure. The body of a 503 carries the per-adapter detail the operator
   * needs, so throwing it away and saying "unreachable" hid the diagnosis.
   */
  public async getReadiness(): Promise<ReadinessProbe> {
    const response = await this.send("/health/ready");
    if (response.status !== 200 && response.status !== 503) {
      throw new ApiProblem({
        type: "about:blank",
        title: "Readiness unavailable",
        status: response.status,
        detail: "The readiness probe returned an unexpected response.",
        code: "READINESS_UNAVAILABLE",
        correlation_id: response.headers.get("X-Correlation-ID") ?? "unavailable",
      });
    }
    return {
      readiness: (await response.json()) as ReadinessResponse,
      degraded: response.status === 503,
    };
  }

  public getTools(): Promise<ToolRegistry> {
    return this.request<ToolRegistry>("/api/v1/tools");
  }

  public async listProtocols(
    options: {
      query?: string;
      archived?: boolean;
      createdFrom?: string;
      createdTo?: string;
    } = {},
    signal?: AbortSignal,
  ): Promise<ProtocolSummary[]> {
    const parameters = new URLSearchParams();
    if (options.query) parameters.set("q", options.query);
    if (options.archived) parameters.set("archived", "true");
    if (options.createdFrom) parameters.set("from", options.createdFrom);
    if (options.createdTo) parameters.set("to", options.createdTo);
    const search = parameters.toString() ? `?${parameters.toString()}` : "";
    const body = await this.request<{ protocols: ProtocolSummary[] }>(
      `/api/v1/protocols${search}`,
      signal === undefined ? {} : { signal },
    );
    return body.protocols;
  }

  public async listSessions(
    options: {
      resumableOnly?: boolean;
      query?: string;
      protocolVersionId?: string;
      label?: string;
      state?: string;
      createdFrom?: string;
      createdTo?: string;
      protocolArchived?: boolean;
    } = {},
    signal?: AbortSignal,
  ): Promise<SessionSummary[]> {
    const search = new URLSearchParams();
    if (options.resumableOnly) search.set("resumable_only", "true");
    if (options.query) search.set("q", options.query);
    if (options.protocolVersionId) {
      search.set("protocol_version_id", options.protocolVersionId);
    }
    if (options.label) search.set("label", options.label);
    if (options.state) search.set("state", options.state);
    if (options.createdFrom) search.set("from", options.createdFrom);
    if (options.createdTo) search.set("to", options.createdTo);
    if (options.protocolArchived !== undefined) {
      search.set("protocol_archived", String(options.protocolArchived));
    }
    const suffix = search.toString() ? `?${search.toString()}` : "";
    const body = await this.request<{ sessions: SessionSummary[] }>(
      `/api/v1/sessions${suffix}`,
      signal === undefined ? {} : { signal },
    );
    return body.sessions;
  }

  public getSessionSnapshot(sessionId: string): Promise<SessionSnapshot> {
    return this.request<SessionSnapshot>(`/api/v1/sessions/${sessionId}/snapshot`);
  }

  public async getTimers(sessionId: string): Promise<SessionTimer[]> {
    const body = await this.request<{ timers: SessionTimer[] }>(
      `/api/v1/sessions/${sessionId}/timers`,
    );
    return body.timers;
  }

  public getProtocolVersion(versionId: string): Promise<ProtocolVersion> {
    return this.request<ProtocolVersion>(`/api/v1/protocol-versions/${versionId}`);
  }

  /**
   * The complete extracted source text, fetched separately from the version.
   *
   * Keeping it off the version response means listing protocols and restoring a
   * session do not each carry a whole document.
   */
  public getProtocolSource(versionId: string): Promise<ProtocolSource> {
    return this.request<ProtocolSource>(
      `/api/v1/protocol-versions/${versionId}/source`,
    );
  }

  public getProtocol(protocolId: string): Promise<ProtocolRecord> {
    return this.request<ProtocolRecord>(`/api/v1/protocols/${protocolId}`);
  }

  public copyProtocolVersion(versionId: string): Promise<ProtocolVersion> {
    return this.request<ProtocolVersion>(`/api/v1/protocol-versions/${versionId}/copy`, {
      method: "POST",
      headers: { "Idempotency-Key": correlationId() },
    });
  }

  public archiveProtocol(protocolId: string): Promise<ProtocolRecord> {
    return this.setProtocolArchived(protocolId, "archive");
  }

  public restoreProtocol(protocolId: string): Promise<ProtocolRecord> {
    return this.setProtocolArchived(protocolId, "restore");
  }

  private setProtocolArchived(
    protocolId: string,
    operation: "archive" | "restore",
  ): Promise<ProtocolRecord> {
    return this.request<ProtocolRecord>(`/api/v1/protocols/${protocolId}/${operation}`, {
      method: "POST",
      headers: { "Idempotency-Key": correlationId() },
    });
  }

  public approveAndStart(
    versionId: string,
    revision: number,
  ): Promise<ApproveAndStartResponse> {
    return this.request<ApproveAndStartResponse>(
      `/api/v1/protocol-versions/${versionId}/approve-and-start`,
      { method: "POST", headers: { "If-Match": `"${revision}"` } },
    );
  }

  /**
   * Save the reviewed steps, approve them, and open the session in one request.
   *
   * Reviewing and approving as two calls had a real failure mode: when the
   * second call failed, the reviewed revision was already persisted and this
   * client still held the previous revision number, so every retry was refused
   * with a conflict it could not explain. The idempotency key means a retry
   * after a dropped response reports the original approval instead of making
   * another one.
   */
  public reviewApproveAndStart(
    versionId: string,
    steps: ProtocolStep[],
    revision: number,
    idempotencyKey: string,
    label: string | null = null,
  ): Promise<ApproveAndStartResponse> {
    return this.request<ApproveAndStartResponse>(
      `/api/v1/protocol-versions/${versionId}/review-approve-and-start`,
      {
        method: "POST",
        headers: {
          "If-Match": `"${revision}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({ steps, label }),
      },
    );
  }

  /**
   * Run one allowlisted tool directly.
   *
   * The idempotency key is what makes a retry after a dropped response safe:
   * resubmitting the same key reports the recorded outcome instead of applying
   * the action twice.
   */
  public invokeTool(
    sessionId: string,
    tool: string,
    version: number,
    parameters: Record<string, string> = {},
    idempotencyKey?: string,
  ): Promise<ToolInvocation> {
    return this.request<ToolInvocation>(`/api/v1/sessions/${sessionId}/tools/${tool}`, {
      method: "POST",
      headers: {
        "If-Match": `"${version}"`,
        "Idempotency-Key": idempotencyKey ?? correlationId(),
      },
      body: JSON.stringify({ parameters }),
    });
  }

  public createRealtimeSession(sessionId: string): Promise<RealtimeGrant> {
    return this.request<RealtimeGrant>("/api/v1/realtime/sessions", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
  }

  public getSession(sessionId: string): Promise<SessionResponse> {
    return this.request<SessionResponse>(`/api/v1/sessions/${sessionId}`);
  }

  public async uploadProtocol(file: File, useOcr = false): Promise<ProtocolImportResponse> {
    const body = new FormData();
    body.append("file", file);
    const endpoint = `/api/v1/protocols/upload${useOcr ? "?ocr=true" : ""}`;
    return this.request<ProtocolImportResponse>(endpoint, {
      method: "POST",
      body,
    });
  }

  public reviewProtocol(
    versionId: string,
    steps: ProtocolStep[],
    revision: number,
  ): Promise<ProtocolVersion> {
    return this.request<ProtocolVersion>(`/api/v1/protocol-versions/${versionId}`, {
      method: "PUT",
      headers: { "If-Match": `"${revision}"` },
      body: JSON.stringify({ steps }),
    });
  }

  public approveProtocol(versionId: string, revision: number): Promise<ProtocolVersion> {
    return this.request<ProtocolVersion>(
      `/api/v1/protocol-versions/${versionId}/approve`,
      { method: "POST", headers: { "If-Match": `"${revision}"` } },
    );
  }

  public createSession(protocolVersionId: string, label: string | null = null): Promise<SessionResponse> {
    return this.request<SessionResponse>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ protocol_version_id: protocolVersionId, label }),
    });
  }

  public startSession(sessionId: string, version: number): Promise<SessionResponse> {
    return this.request<SessionResponse>(`/api/v1/sessions/${sessionId}/start`, {
      method: "POST",
      headers: { "If-Match": `"${version}"` },
    });
  }

  public submitTextCommand(
    sessionId: string,
    transcript: string,
    version: number,
    idempotencyKey: string,
  ): Promise<CommandResponse> {
    return this.request<CommandResponse>(`/api/v1/sessions/${sessionId}/commands/text`, {
      method: "POST",
      headers: {
        "If-Match": `"${version}"`,
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ transcript }),
    });
  }

  public submitAudioCommand(
    sessionId: string,
    audio: Blob,
    version: number,
    idempotencyKey: string,
    options: { requireWakePhrase?: boolean; listenMode?: "command" | "standby" | "barge_in" } = {},
  ): Promise<CommandResponse> {
    const body = new FormData();
    const extension = audio.type.includes("wav") ? "wav" : "webm";
    body.append("file", audio, `command.${extension}`);
    const query = new URLSearchParams();
    if (options.requireWakePhrase) query.set("require_wake_phrase", "true");
    if (options.listenMode !== undefined && options.listenMode !== "command") {
      query.set("listen_mode", options.listenMode);
    }
    const encoded = query.toString();
    const search = encoded === "" ? "" : `?${encoded}`;
    return this.request<CommandResponse>(`/api/v1/sessions/${sessionId}/commands/audio${search}`, {
      method: "POST",
      headers: {
        "If-Match": `"${version}"`,
        "Idempotency-Key": idempotencyKey,
      },
      body,
    });
  }

  public confirmAction(actionId: string, accepted: boolean): Promise<ConfirmationResponse> {
    return this.request<ConfirmationResponse>(`/api/v1/pending-actions/${actionId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ accepted }),
    });
  }

  /**
   * Answer a proposal with a recording of the operator speaking.
   *
   * The evidence is deliberately audio. Anything expressible as a function call
   * is something the realtime model could issue by itself, and the one thing it
   * must never be able to do is approve its own proposal. A recording of a
   * person is not something it can produce.
   */
  public confirmActionByVoice(
    actionId: string,
    audio: Blob,
    expectedVersion: number,
    idempotencyKey: string,
    options: { viaWakePhrase?: boolean } = {},
  ): Promise<SpokenConfirmation> {
    const body = new FormData();
    body.append("file", audio, "confirmation.webm");
    return this.request<SpokenConfirmation>(
      `/api/v1/pending-actions/${actionId}/spoken-confirmation` +
        `?expected_version=${encodeURIComponent(String(expectedVersion))}` +
        (options.viaWakePhrase === true ? "&via_wake_phrase=true" : ""),
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body,
      },
    );
  }

  public probeReagentLabel(
    sessionId: string,
    frame: Blob,
    requirementId: string | null,
  ): Promise<LabelProbe> {
    const body = new FormData();
    body.append("file", frame, "probe.jpg");
    const search = requirementId ? `?requirement_id=${encodeURIComponent(requirementId)}` : "";
    return this.request<LabelProbe>(
      `/api/v1/sessions/${sessionId}/reagent-scans/probe${search}`,
      { method: "POST", body },
    );
  }

  public decideReagentVerificationByVoice(
    verificationId: string,
    audio: Blob,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<SpokenReagentDecision> {
    const body = new FormData();
    body.append("file", audio, "decision.wav");
    return this.request<SpokenReagentDecision>(
      `/api/v1/reagent-verifications/${verificationId}/spoken-decision`,
      {
        method: "POST",
        headers: { "If-Match": `"${expectedVersion}"`, "Idempotency-Key": idempotencyKey },
        body,
      },
    );
  }

  public verifyReagent(
    sessionId: string,
    image: Blob,
    requirementId: string | null,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<ReagentVerification> {
    const body = new FormData();
    body.append("file", image, "visible-label.jpg");
    const search = requirementId
      ? `?requirement_id=${encodeURIComponent(requirementId)}`
      : "";
    return this.request<ReagentVerification>(
      `/api/v1/sessions/${sessionId}/reagent-verifications${search}`,
      {
        method: "POST",
        headers: {
          "If-Match": `"${expectedVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body,
      },
    );
  }

  public verifyReagentFrames(
    sessionId: string,
    frames: Blob[],
    requirementId: string | null,
    expectedVersion: number,
    idempotencyKey: string,
    capture?: CaptureMetadata,
  ): Promise<ReagentVerification> {
    const body = new FormData();
    frames.forEach((frame, index) => body.append("frames", frame, `label-frame-${index}.jpg`));
    const params = new URLSearchParams();
    if (requirementId) params.set("requirement_id", requirementId);
    if (capture !== undefined) {
      params.set("capture_mode", capture.mode);
      if (capture.scanRule !== undefined) params.set("scan_rule", capture.scanRule);
      if (capture.framesExamined !== undefined) {
        params.set("scan_frames_examined", String(capture.framesExamined));
      }
      if (capture.elapsedMs !== undefined) {
        params.set("scan_elapsed_ms", String(Math.round(capture.elapsedMs)));
      }
    }
    const search = params.size > 0 ? `?${params.toString()}` : "";
    return this.request<ReagentVerification>(
      `/api/v1/sessions/${sessionId}/reagent-verifications/frames${search}`,
      {
        method: "POST",
        headers: {
          "If-Match": `"${expectedVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body,
      },
    );
  }

  public confirmReagentVerification(
    verificationId: string,
    accepted: boolean,
    correctedObservedLabel: string | null,
    expectedVersion: number,
    idempotencyKey: string,
  ): Promise<ReagentVerification> {
    return this.request<ReagentVerification>(
      `/api/v1/reagent-verifications/${verificationId}/confirm`,
      {
        method: "POST",
        headers: {
          "If-Match": `"${expectedVersion}"`,
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          accepted,
          corrected_observed_label: correctedObservedLabel,
        }),
      },
    );
  }

  public getNotebook(sessionId: string): Promise<NotebookResponse> {
    return this.request<NotebookResponse>(`/api/v1/sessions/${sessionId}/notebook`);
  }

  public correctNotebookEntry(
    entryId: string,
    payload: Record<string, string>,
  ): Promise<NotebookEntry> {
    return this.request<NotebookEntry>(`/api/v1/notebook/${entryId}/corrections`, {
      method: "POST",
      body: JSON.stringify({ payload }),
    });
  }

  public exportNotebook(sessionId: string, format: "json" | "csv"): string {
    return `${this.baseUrl}/api/v1/sessions/${sessionId}/notebook/export?format=${format}`;
  }

  public deleteSession(sessionId: string, confirmation: string): Promise<DeletionReceipt> {
    return this.deleteAggregate("sessions", sessionId, confirmation);
  }

  public deleteProtocol(protocolId: string, confirmation: string): Promise<DeletionReceipt> {
    return this.deleteAggregate("protocols", protocolId, confirmation);
  }

  private deleteAggregate(
    collection: "sessions" | "protocols",
    id: string,
    confirmation: string,
  ): Promise<DeletionReceipt> {
    return this.request<DeletionReceipt>(`/api/v1/data/${collection}/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation }),
    });
  }

  public createBackup(): Promise<BackupPackage> {
    return this.request<BackupPackage>("/api/v1/data/backups", { method: "POST" });
  }

  public validateBackup(backupName: string): Promise<RestoreValidation> {
    return this.request<RestoreValidation>(
      `/api/v1/data/backups/${encodeURIComponent(backupName)}/validate`,
      { method: "POST" },
    );
  }

  public getConfiguration(): Promise<ConfigurationState> {
    return this.request<ConfigurationState>("/api/v1/settings");
  }

  public saveConfiguration(configuration: ModelConfiguration): Promise<ConfigurationState> {
    return this.request<ConfigurationState>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify(configuration),
    });
  }

  public restartLabJarvis(acknowledgeActiveSession: boolean): Promise<RestartAccepted> {
    return this.request<RestartAccepted>("/api/v1/settings/restart", {
      method: "POST",
      body: JSON.stringify({ acknowledge_active_session: acknowledgeActiveSession }),
    });
  }

  public async synthesizeSpeech(
    text: string,
    options: { voice?: string; signal?: AbortSignal } = {},
  ): Promise<ArrayBuffer> {
    const response = await this.send("/api/v1/speech-output/synthesize", {
      method: "POST",
      body: JSON.stringify(options.voice === undefined ? { text } : { text, voice: options.voice }),
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    });
    if (!response.ok) {
      throw new ApiProblem({
        type: "about:blank",
        title: "Speech unavailable",
        status: response.status,
        detail: "The local voice could not speak this response.",
        code: "SPEECH_OUTPUT_UNAVAILABLE",
        correlation_id: response.headers.get("X-Correlation-ID") ?? "unavailable",
      });
    }
    return response.arrayBuffer();
  }
}
