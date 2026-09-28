import { vi } from "vitest";

import type { ClientPort } from "../api/client";
import type {
  ProtocolVersion,
  ReadinessResponse,
  SessionResponse,
  SessionSnapshot,
} from "../types";

const interaction = {
  wake_phrase: "Jarvis",
  realtime_enabled: false,
  realtime_transport: "webrtc",
  realtime_voice: "alloy",
  realtime_model: "",
  realtime_confirmation_seconds: 5,
} as const;

const readyStage = { status: "ready", detail: "satisfied" } as const;
const fixtureStage = { status: "not_applicable", detail: "deterministic fixture" } as const;
const fixtureStages = {
  configured: readyStage,
  installed: fixtureStage,
  cached: fixtureStage,
  loaded: fixtureStage,
  reachable: fixtureStage,
  inference_verified: fixtureStage,
  first_blocking_stage: null,
} as const;

export const readyFakeMode: ReadinessResponse = {
  status: "ready",
  mode: "fake",
  tool_contract_version: "tools-v1",
  interaction,
  privacy: {
    raw_media_retention: false,
    transcript_retention: true,
    external_model_processing: false,
  },
  adapters: {
    speech: { ready: true, detail: "deterministic fixture", ...fixtureStages },
    text: { ready: true, detail: "deterministic fixture", ...fixtureStages },
    vision: { ready: true, detail: "deterministic fixture", ...fixtureStages },
  },
  database: { ready: true },
  latency_window: {
    max_samples: 200,
    sample_count: 3,
    modalities: {
      text: {
        sample_count: 3,
        failed_count: 0,
        average_ms: 4,
        p50_ms: 4,
        p95_ms: 6,
      },
    },
  },
};

export const readyLocalMode: ReadinessResponse = {
  status: "ready",
  mode: "local",
  tool_contract_version: "tools-v1",
  interaction,
  privacy: {
    raw_media_retention: false,
    transcript_retention: true,
    external_model_processing: false,
  },
  adapters: {
    speech: {
      ready: true,
      adapter: "faster-whisper",
      model_name: "small.en",
      configured: readyStage,
      installed: readyStage,
      cached: readyStage,
      loaded: { status: "not_applicable", detail: "loads on first use" },
    },
    text: {
      ready: true,
      adapter: "ollama-json-schema",
      model_name: "qwen2.5:1.5b",
      configured: readyStage,
      installed: readyStage,
      cached: readyStage,
      reachable: readyStage,
    },
    vision: {
      ready: true,
      adapter: "easyocr",
      model_name: "english_g2",
      configured: readyStage,
      installed: readyStage,
      cached: readyStage,
      loaded: { status: "not_applicable", detail: "loads on first use" },
    },
  },
  database: { ready: true },
};

/** Dependency present but weights absent: ready must be false, not true. */
export const notReadyLocalMode: ReadinessResponse = {
  ...readyLocalMode,
  status: "not_ready",
  adapters: {
    speech: {
      ready: false,
      adapter: "faster-whisper",
      model_name: "small.en",
      detail: "weights are not cached and downloads are disabled",
      configured: readyStage,
      installed: readyStage,
      cached: { status: "blocked", detail: "weights are not cached and downloads are disabled" },
      loaded: { status: "blocked", detail: "weights are not cached and downloads are disabled" },
      first_blocking_stage: "cached",
    },
    text: {
      ready: false,
      adapter: "ollama-json-schema",
      model_name: "qwen2.5:1.5b",
      detail: "Ollama is not reachable on the configured loopback URL",
      configured: readyStage,
      reachable: { status: "blocked", detail: "Ollama is not reachable on the configured loopback URL" },
      first_blocking_stage: "reachable",
    },
    vision: readyLocalMode.adapters.vision,
  },
};

export const readyHostedMode: ReadinessResponse = {
  status: "ready",
  mode: "hosted",
  hosted_provider: "openai",
  tool_contract_version: "tools-v1",
  interaction,
  privacy: {
    raw_media_retention: false,
    transcript_retention: true,
    external_model_processing: true,
  },
  adapters: {
    speech: {
      ready: true,
      adapter: "openai-transcriptions",
      model_name: "gpt-4o-transcribe",
      detail: "hosted model reachable",
      configured: readyStage,
      reachable: readyStage,
    },
    text: {
      ready: true,
      adapter: "openai-responses-json-schema",
      model_name: "gpt-5.6-terra",
      detail: "hosted model reachable",
      configured: readyStage,
      reachable: readyStage,
    },
    vision: {
      ready: true,
      adapter: "openai-responses-vision",
      model_name: "gpt-5.6-terra",
      detail: "hosted model reachable",
      configured: readyStage,
      reachable: readyStage,
    },
  },
  database: { ready: true },
};

export const draftProtocol: ProtocolVersion = {
  id: "version-1",
  protocol_id: "protocol-1",
  version_number: 1,
  revision: 0,
  status: "DRAFT",
  source_sha256: "a".repeat(64),
  created_at: "2026-07-16T12:00:00Z",
  approved_at: null,
  steps: [
    {
      id: "step-1",
      number: 1,
      title: "Add ethanol",
      instruction: "Add 10 mL ethanol.",
      requirements: [
        {
          requirement_id: "step-1-req-1",
          name: "ethanol",
          quantity: "10",
          unit: "mL",
          aliases: [],
        },
      ],
      duration_seconds: null,
      safety_notes: [],
      source: { page: 1, section: "Preparation", excerpt: "1. Add 10 mL ethanol." },
      parallel_allowed: false,
    },
  ],
};

export const approvedProtocol: ProtocolVersion = {
  ...draftProtocol,
  status: "APPROVED",
  revision: 2,
  approved_at: "2026-07-16T12:05:00Z",
};

export const activeSession: SessionResponse = {
  id: "session-1",
  protocol_version_id: "version-1",
  state: "ACTIVE",
  steps: [
    {
      step_id: "step-1",
      state: "IN_PROGRESS",
      started_at: "2026-07-16T12:06:00Z",
      completed_at: null,
      skipped_reason: null,
    },
  ],
  active_step_id: "step-1",
  version: 1,
  created_at: "2026-07-16T12:05:00Z",
  started_at: "2026-07-16T12:06:00Z",
  ended_at: null,
  label: null,
};

export const activeSnapshot: SessionSnapshot = {
  session: activeSession,
  protocol_id: "protocol-1",
  protocol_title: "Ethanol protocol",
  version: approvedProtocol,
  timers: [],
  pending_action: null,
  pending_verification: null,
  requirement_progress: [
    {
      requirement_id: "step-1-req-1",
      name: "ethanol",
      status: "unverified",
      verification_id: null,
    },
  ],
  entries: [],
};

export function fakeClient(overrides: Partial<ClientPort> = {}): ClientPort {
  const unused = () => Promise.reject(new Error("Unexpected client call"));
  return {
    getReadiness: () => Promise.resolve({ readiness: readyFakeMode, degraded: false }),
    getTools: () => Promise.resolve({ contract_version: "tools-v1", tools: [] }),
    listProtocols: () => Promise.resolve([]),
    listSessions: () => Promise.resolve([]),
    getSession: unused,
    getSessionSnapshot: unused,
    getTimers: () => Promise.resolve([]),
    uploadProtocol: unused,
    reviewProtocol: unused,
    approveProtocol: unused,
    approveAndStart: unused,
    reviewApproveAndStart: unused,
    getProtocolVersion: unused,
    getProtocol: () =>
      Promise.resolve({
        id: "protocol-1",
        title: "Ethanol protocol",
        created_at: "2026-07-16T12:00:00Z",
        archived_at: null,
      }),
    getProtocolSource: () =>
      Promise.resolve({
        version_id: "version-1",
        source_sha256: "a".repeat(64),
        text: "1. Add 10 mL ethanol.",
        character_count: 21,
      }),
    copyProtocolVersion: unused,
    archiveProtocol: unused,
    restoreProtocol: unused,
    createSession: unused,
    startSession: unused,
    invokeTool: unused,
    createRealtimeSession: unused,
    submitTextCommand: unused,
    submitAudioCommand: unused,
    confirmAction: unused,
    confirmActionByVoice: unused,
    decideReagentVerificationByVoice: unused,
    probeReagentLabel: unused,
    verifyReagent: unused,
    verifyReagentFrames: unused,
    confirmReagentVerification: unused,
    getNotebook: unused,
    correctNotebookEntry: unused,
    exportNotebook: vi.fn(() => "#notebook-export"),
    deleteSession: unused,
    deleteProtocol: unused,
    createBackup: unused,
    validateBackup: unused,
    getConfiguration: unused,
    saveConfiguration: unused,
    restartLabJarvis: unused,
    synthesizeSpeech: unused,
    ...overrides,
  };
}
