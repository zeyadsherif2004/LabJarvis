import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiProblem, type ClientPort } from "./api/client";
import {
  actionFailure,
  failedAction,
  successfulAction,
  type ActionFailure,
  type ActionResult,
} from "./actions";
import { AppShell } from "./components/AppShell";
import { ResourceGate } from "./components/ResourceGate";
import { describeError, errorSentence } from "./errors";
import { DashboardView } from "./features/dashboard/DashboardView";
import { useDashboardDiscovery } from "./features/dashboard/useDashboardDiscovery";
import { DataManagementView } from "./features/data/DataManagementView";
import { DiagnosticsView } from "./features/diagnostics/DiagnosticsView";
import { ProtocolApprovedView } from "./features/protocol/ProtocolApprovedView";
import { ProtocolOverviewView } from "./features/protocol/ProtocolOverviewView";
import { ProtocolReviewView } from "./features/protocol/ProtocolReviewView";
import { SetupView } from "./features/setup/SetupView";
import { SettingsView } from "./features/settings/SettingsView";
import { SessionReviewView } from "./features/session/SessionReviewView";
import { ConfirmationDialog } from "./components/ConfirmationDialog";
import { HandsFreeListeningPanel } from "./features/session/HandsFreeListeningPanel";
import { HandsFreeStartPrompt } from "./features/session/HandsFreeStartPrompt";
import { SterileModeView, type HandsFreeLifecycle } from "./features/session/SterileModeView";
import { useTimerAlarms } from "./features/session/timerAlarms";
import { voiceConfirmationPhrase } from "./features/session/voicePhrase";
import { useReagentFlow } from "./features/session/useReagentFlow";
import { useSessionCommands } from "./features/session/useSessionCommands";
import { BrowserAudioCapture, type AudioCapturePort } from "./media/audioCapture";
import { createSpeechOutput, type SpeechOutput } from "./media/speechOutput";
import { sessionIdOf } from "./routing";
import { useHashRoute } from "./state/useHashRoute";
import { useRoutedResource } from "./state/useRoutedResource";
import { useToolRegistry } from "./state/useToolRegistry";
import type {
  CommandResponse,
  NotebookResponse,
  ProtocolStep,
  ProtocolRecord,
  ProtocolVersion,
  ReadinessResponse,
  SessionSnapshot,
  SessionSummary,
} from "./types";

interface AppProps {
  client: ClientPort;
  audioCapture?: AudioCapturePort;
  /** Injected in tests; the default speaks through the browser synthesiser. */
  speechOutput?: SpeechOutput;
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/**
 * Kiosk launch (`python scripts/tasks.py hands-free`) opens this page with
 * `?kiosk=1` in a browser started with the autoplay flag. It is honoured on
 * the local machine only; elsewhere it is ignored.
 */
function kioskRequested(): boolean {
  if (typeof window === "undefined") return false;
  const requested = new URLSearchParams(window.location.search).get("kiosk") === "1";
  return requested && LOOPBACK.has(window.location.hostname);
}

function idempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-command-${Date.now().toString(36)}`;
}

/** Codes meaning local state is behind authority and must be re-read. */
const CONFLICT_CODES = new Set([
  "SESSION_VERSION_CONFLICT",
  "PROTOCOL_VERSION_CONFLICT",
  "PENDING_ACTION_RESOLVED",
]);

function isConflict(caught: unknown): boolean {
  return caught instanceof ApiProblem && CONFLICT_CODES.has(caught.problem.code);
}

export function App({ client, audioCapture, speechOutput }: AppProps) {
  const [route, navigate] = useHashRoute();
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [degraded, setDegraded] = useState(false);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  const dashboard = useDashboardDiscovery(client, route.name === "dashboard");
  const toolRegistry = useToolRegistry(client);

  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [media] = useState<AudioCapturePort>(() => audioCapture ?? new BrowserAudioCapture());
  const spokenRef = useRef<string | null>(null);
  const [speech] = useState<SpeechOutput>(() => speechOutput ?? createSpeechOutput());
  // Hands-free lifecycle lives here so it survives the switch from the
  // approved (READY) screen to the sterile session view.
  const [handsFreeArmed, setHandsFreeArmed] = useState(false);
  const [listeningStandby, setListeningStandby] = useState(false);
  const [kiosk] = useState(kioskRequested);
  const [kioskActive, setKioskActive] = useState(false);
  const [kioskFallback, setKioskFallback] = useState(false);

  // ------------------------------------------------------------- routed state
  //
  // Every resource on screen is keyed by the identifier in the URL. Holding a
  // bare `snapshot | null` used to mean that a failed load left the *previous*
  // session rendered with live controls under a URL naming a different one, so
  // a button press mutated the wrong experiment. Resource identity now travels
  // with the value, and `value` is null unless it is what the route asked for.

  const routeSessionId = sessionIdOf(route);
  const routeVersionId = route.name === "protocol" ? route.versionId : null;
  const notebookSessionId = route.name === "notebook" ? route.sessionId : null;

  const loadSnapshot = useCallback(
    (sessionId: string) => client.getSessionSnapshot(sessionId),
    [client],
  );
  const sessionResource = useRoutedResource<SessionSnapshot>(routeSessionId, loadSnapshot);
  const snapshot = sessionResource.value;

  const loadVersion = useCallback(
    (versionId: string) => client.getProtocolVersion(versionId),
    [client],
  );
  const protocolResource = useRoutedResource<ProtocolVersion>(routeVersionId, loadVersion);
  const draft = protocolResource.value;

  const loadProtocolRecord = useCallback(
    (protocolId: string) => client.getProtocol(protocolId),
    [client],
  );
  const protocolRecordResource = useRoutedResource<ProtocolRecord>(
    draft?.protocol_id ?? null,
    loadProtocolRecord,
  );

  // Model provenance is part of the record rather than decoration: an operator
  // reviewing a measurement needs to see which adapter proposed it and how
  // confident it was. It loads with the notebook route, so it can never show
  // another session's inferences.
  const loadNotebook = useCallback(
    (sessionId: string) => client.getNotebook(sessionId),
    [client],
  );
  const notebookResource = useRoutedResource<NotebookResponse>(
    notebookSessionId,
    loadNotebook,
  );

  // An approved protocol offers a way back into its own session. Previously the
  // screen was handed whatever snapshot happened to be in memory, which had no
  // guaranteed relationship to the version named in the URL.
  const loadVersionSessions = useCallback(
    (versionId: string) => client.listSessions({ protocolVersionId: versionId }),
    [client],
  );
  const versionSessions = useRoutedResource<SessionSummary[]>(
    draft !== null && draft.status !== "DRAFT" ? draft.id : null,
    loadVersionSessions,
  );
  const resumableForVersion =
    versionSessions.value?.find((summary) => summary.resumable) ??
    versionSessions.value?.[0] ??
    null;

  const refreshSnapshot = sessionResource.reload;
  const reloadProtocol = protocolResource.reload;

  /** Re-read authority after a conflict, then explain what to do next. */
  const handleActionError = useCallback(
    (caught: unknown): ActionFailure => {
      setError(errorSentence(caught));
      if (isConflict(caught)) void refreshSnapshot();
      return actionFailure(caught);
    },
    [refreshSnapshot],
  );

  // --------------------------------------------------------------- speech out

  useEffect(() => {
    speech.setMuted(muted);
  }, [muted, speech]);

  useEffect(() => {
    speech.setDetail(readiness?.interaction.spoken_detail ?? "full");
  }, [readiness?.interaction.spoken_detail, speech]);

  // Kokoro speaks when the server has it ready; otherwise the browser voice.
  const kokoroReady =
    readiness?.speech_output?.engine === "kokoro" && readiness.speech_output.ready;
  useEffect(() => {
    speech.setNeuralVoice?.(
      kokoroReady
        ? { synthesize: (text, signal) => client.synthesizeSpeech(text, { signal }) }
        : null,
    );
  }, [client, kokoroReady, speech]);

  // Eyes-free: every outcome message is spoken (the screen shows the same).
  useEffect(() => {
    if (muted || message === null || message === spokenRef.current) return;
    spokenRef.current = message;
    speech.speak(message);
  }, [message, muted, speech]);

  // Errors are outcomes too; they are spoken in plain language.
  const spokenErrorRef = useRef<string | null>(null);
  useEffect(() => {
    if (muted || error === null || error === spokenErrorRef.current) return;
    spokenErrorRef.current = error;
    speech.speak(error);
  }, [error, muted, speech]);

  // ---------------------------------------------------------------- readiness

  const loadReadiness = useCallback(async () => {
    try {
      const probe = await client.getReadiness();
      setReadiness(probe.readiness);
      setDegraded(probe.degraded);
      setReadinessError(null);
    } catch (caught) {
      // A 503 is not a failure here - the client resolves it as `degraded`.
      // Reaching this branch means the API genuinely did not answer.
      setReadinessError(errorSentence(caught));
    }
  }, [client]);

  useEffect(() => {
    void loadReadiness();
  }, [loadReadiness]);

  // A transient action message belongs to the screen that produced it. Carrying
  // it across a route change made it look as though something had just happened
  // to the resource the operator had only now opened.
  useEffect(() => {
    setMessage(null);
    setError(null);
  }, [route.name, routeSessionId, routeVersionId]);


  // Timers advance on the server, so a session left open re-reads periodically
  // rather than trusting a local counter that a sleeping tab would have frozen.
  const patchSession = sessionResource.patch;
  useEffect(() => {
    if (route.name !== "session" || routeSessionId === null) return undefined;
    const handle = window.setInterval(() => {
      void client
        .getTimers(routeSessionId)
        .then((timers) => patchSession((current) => ({ ...current, timers })))
        .catch(() => undefined);
    }, 15_000);
    return () => window.clearInterval(handle);
  }, [client, patchSession, route.name, routeSessionId]);

  // ---------------------------------------------------------------- protocols

  const seedProtocol = protocolResource.seed;

  async function importProtocol(file: File, useOcr = false): Promise<ActionResult> {
    setBusy(true);
    setError(null);
    try {
      const imported = await client.uploadProtocol(file, useOcr);
      seedProtocol(imported.version.id, imported.version);
      setMessage(`${imported.title} parsed as a draft. Review it before it can be run.`);
      navigate({ name: "protocol", versionId: imported.version.id });
      return successfulAction;
    } catch (caught) {
      setError(errorSentence(caught));
      return actionFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  async function approveProtocol(
    steps: ProtocolStep[],
    revision: number,
    label: string | null,
  ): Promise<ActionResult> {
    if (draft === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      // Review, approval, and session creation commit as one server-side
      // transaction. Doing it as two calls left the backend holding a reviewed
      // revision the browser did not know about whenever the second one failed.
      const started = await client.reviewApproveAndStart(
        draft.id,
        steps,
        revision,
        idempotencyKey(),
        label,
      );
      seedProtocol(started.version.id, started.version);
      setMessage("Protocol approved. The session is ready to start.");
      navigate({ name: "session", sessionId: started.session.id });
      return successfulAction;
    } catch (caught) {
      setError(errorSentence(caught));
      if (isConflict(caught)) void reloadProtocol();
      return actionFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  const reloadNotebook = notebookResource.reload;
  // A spoken "check reagent" opens the camera and captures frames by itself.
  // The server resolves which requirement was named; with several reagents
  // and none named it asks instead, and no capture starts.
  const [verificationRequest, setVerificationRequest] = useState(0);
  const [verificationRequirementId, setVerificationRequirementId] = useState<string | null>(null);
  const onCommandResult = useCallback((result: CommandResponse) => {
    if (result.intent !== "VERIFY_REAGENT") return;
    const requirementId = result.parameters?.requirement_id ?? null;
    if (requirementId === null) return;
    setVerificationRequirementId(requirementId);
    setVerificationRequest((count) => count + 1);
  }, []);
  const speak = useCallback(
    (text: string) => {
      if (muted) return;
      speech.speak(text);
    },
    [muted, speech],
  );
  /** Interface controls said out loud. None of them touches the record. */
  const onVoiceControl = useCallback(
    (control: string, responseText: string) => {
      switch (control) {
        case "STOP_SPEAKING":
          speech.stop();
          return;
        case "REPEAT_LAST_SPOKEN":
          speech.repeat();
          return;
        case "MUTE_SPEECH":
          speech.speak(responseText);
          setMuted(true);
          setMessage(responseText);
          spokenRef.current = responseText;
          return;
        case "UNMUTE_SPEECH":
          setMuted(false);
          speech.setMuted(false);
          speech.speak(responseText);
          spokenRef.current = responseText;
          setMessage(responseText);
          return;
        case "STOP_LISTENING":
          setListeningStandby(true);
          setMessage(responseText);
          return;
        case "START_LISTENING":
          // Re-enabling is honoured only while this tab has focus, so a
          // background tab cannot be brought back to listening by a voice.
          if (typeof document !== "undefined" && !document.hasFocus()) return;
          setListeningStandby(false);
          setMessage(responseText);
          return;
        default:
          setMessage(responseText);
      }
    },
    [speech],
  );
  const sessionCommands = useSessionCommands({
    client,
    snapshot,
    refreshSnapshot,
    reloadNotebook,
    navigate,
    setBusy,
    setError,
    setMessage,
    handleActionError,
    onCommandResult,
    onVoiceControl,
  });
  const reagentFlow = useReagentFlow({
    client,
    snapshot,
    refreshSnapshot,
    setBusy,
    setError,
    setMessage,
    handleActionError,
    voiceOnly: readiness?.interaction.voice_only_confirmation === true,
    wakePhrase: readiness?.interaction.wake_phrase ?? "",
    onVoiceControl,
  });
  const {
    askProtocolQuestion,
    confirmAction,
    confirmByVoice,
    correctEntry,
    invokeTool,
    startSession,
    submitAudioCommand,
    submitTextCommand,
  } = sessionCommands;
  const invokeToolRef = useRef(invokeTool);
  invokeToolRef.current = invokeTool;
  const {
    confirmReagentVerification,
    decideReagentByVoice,
    probeLabel,
    verification,
    verifyReagent,
    verifyReagentFrames,
  } = reagentFlow;

  function replayResponse(): void {
    if (muted || message === null) return;
    speech.speak(message);
  }

  // ------------------------------------------------------------ hands-free

  const interaction = readiness?.interaction;
  const handsFreeOn = interaction?.hands_free_listening === true;
  const autostart = handsFreeOn && interaction?.hands_free_autostart === true;

  // Kiosk mode: audio may already be allowed without a gesture. Check once;
  // if the flag is missing, fall back to the one-tap prompt and say so.
  useEffect(() => {
    if (!autostart || !kiosk || handsFreeArmed) return;
    void speech.unlock().then((allowed) => {
      if (allowed) {
        setKioskActive(true);
        setHandsFreeArmed(true);
      } else {
        setKioskFallback(true);
      }
    });
  }, [autostart, handsFreeArmed, kiosk, speech]);

  const armHandsFree = useCallback(async (): Promise<void> => {
    // Ask for both permissions now, before gloving up.
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      stream.getTracks().forEach((track) => track.stop());
    } catch {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
        setError("Camera permission was not granted; label checks will need the screen.");
      } catch {
        setError(
          "Microphone permission was not granted. Push-to-talk and the screen remain available.",
        );
        return;
      }
    }
    await speech.unlock();
    setHandsFreeArmed(true);
    speech.speak("Hands-free is on. Say the wake word, then a command.");
  }, [speech]);

  const sessionOpen =
    snapshot !== null && ["READY", "ACTIVE", "PAUSED"].includes(snapshot.session.state);
  const handsFreeLifecycle: HandsFreeLifecycle | undefined = handsFreeOn
    ? {
        autoStart: autostart && handsFreeArmed,
        standby: listeningStandby,
        // No idle switch-off while a session is open (READY, ACTIVE or PAUSED).
        idleTimeoutMs: sessionOpen ? null : 10 * 60_000,
        announce: (text: string) => speech.speak(text),
        isSpeaking: () => speech.isSpeaking(),
      }
    : undefined;

  const warningSeconds = useMemo(
    () =>
      (interaction?.timer_warning_seconds ?? "")
        .split(",")
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value) && value > 0),
    [interaction?.timer_warning_seconds],
  );
  useTimerAlarms({
    timers: route.name === "session" ? (snapshot?.timers ?? []) : [],
    steps: snapshot?.version.steps ?? [],
    enabled: interaction?.timer_alarms === true,
    repeatMs: (interaction?.timer_alarm_repeat_seconds ?? 20) * 1000,
    warningSeconds,
    muted,
    speak: (text) => speech.speak(text),
    playTone: () => speech.playTone(),
    onDeadline: () => {
      if (routeSessionId === null) return;
      void client
        .getTimers(routeSessionId)
        .then((timers) => sessionResource.patch((current) => ({ ...current, timers })))
        .catch(() => undefined);
    },
  });
  const acknowledgeAlarm = useCallback(() => {
    void invokeToolRef.current("acknowledge_timer_alarm");
  }, []);

  // ------------------------------------------------------------------- render

  const activeStep = useMemo(() => {
    if (snapshot === null) return null;
    return (
      snapshot.version.steps.find((step) => step.id === snapshot.session.active_step_id) ??
      null
    );
  }, [snapshot]);

  const toDashboard = useCallback(() => navigate({ name: "dashboard" }), [navigate]);

  let content;
  if (readiness === null) {
    content =
      readinessError === null ? (
        <main className="loading-state" id="main-content" aria-busy="true">
          <p>Checking LabJarvis...</p>
        </main>
      ) : (
        <main className="connection-error" id="main-content">
          <h1>LabJarvis API did not respond</h1>
          <p>{readinessError}</p>
          <button
            className="button button--primary"
            type="button"
            onClick={() => void loadReadiness()}
          >
            Retry
          </button>
        </main>
      );
  } else if (route.name === "diagnostics") {
    content = (
      <DiagnosticsView
        readiness={readiness}
        degraded={degraded}
        onRetry={() => void loadReadiness()}
      />
    );
  } else if (route.name === "settings") {
    content = <SettingsView client={client} onRestarted={() => void loadReadiness()} />;
  } else if (route.name === "data") {
    content = (
      <DataManagementView
        client={client}
        readiness={readiness}
        sessionId={route.sessionId ?? null}
        onSessionDeleted={() => navigate({ name: "dashboard" })}
        onProtocolDeleted={() => navigate({ name: "dashboard" })}
      />
    );
  } else if (route.name === "import") {
    content = <SetupView readiness={readiness} busy={busy} onImport={importProtocol} />;
  } else if (route.name === "protocol") {
    content = (
      <ResourceGate
        state={protocolResource.state}
        kind="protocol"
        loadingLabel="Loading protocol..."
        onRetry={() => void reloadProtocol()}
        onGoBack={toDashboard}
      >
        {(version) =>
          version.status === "DRAFT" ? (
            <ProtocolReviewView
              // Remounting on version identity is what stops one draft's edited
              // steps being carried into another: the editor's local state is
              // seeded from props, so it must not survive the swap. Autosave
              // advances the revision, so only the identifier keys the mount.
              key={version.id}
              client={client}
              version={version}
              busy={busy}
              onApprove={approveProtocol}
              onRevisionChange={(next) =>
                protocolResource.patch((current) => ({ ...current, revision: next }))
              }
            />
          ) : (
            <ProtocolOverviewView
              protocol={version}
              archivedAt={protocolRecordResource.value?.archived_at ?? null}
              sessionSummary={resumableForVersion}
              onCopy={async () => {
                const copied = await client.copyProtocolVersion(version.id);
                navigate({ name: "protocol", versionId: copied.id });
              }}
              onArchive={async () => {
                const archived = await client.archiveProtocol(version.protocol_id);
                if (archived.archived_at === null) {
                  throw new Error("The protocol did not report an archived timestamp.");
                }
                protocolRecordResource.patch(() => archived);
                return archived.archived_at;
              }}
              onRestore={async () => {
                const restored = await client.restoreProtocol(version.protocol_id);
                protocolRecordResource.patch(() => restored);
                return null;
              }}
              onOpenSession={() =>
                resumableForVersion === null
                  ? toDashboard()
                  : navigate(
                      resumableForVersion.resumable
                        ? { name: "session", sessionId: resumableForVersion.session_id }
                        : { name: "notebook", sessionId: resumableForVersion.session_id },
                    )
              }
            />
          )
        }
      </ResourceGate>
    );
  } else if (route.name === "notebook") {
    content = (
      <ResourceGate
        state={sessionResource.state}
        kind="session"
        loadingLabel="Loading notebook..."
        onRetry={() => void refreshSnapshot()}
        onGoBack={toDashboard}
      >
        {(loaded) => (
          <SessionReviewView
            session={loaded.session}
            entries={notebookResource.value?.entries ?? loaded.entries}
            inferences={notebookResource.value?.inferences ?? []}
            verifications={notebookResource.value?.verifications ?? []}
            utterances={notebookResource.value?.utterances ?? []}
            provenanceLoading={notebookResource.loading}
            busy={busy}
            jsonExportUrl={client.exportNotebook(loaded.session.id, "json")}
            csvExportUrl={client.exportNotebook(loaded.session.id, "csv")}
            onCorrect={correctEntry}
          />
        )}
      </ResourceGate>
    );
  } else if (route.name === "session") {
    content = (
      <ResourceGate
        state={sessionResource.state}
        kind="session"
        loadingLabel="Restoring session..."
        onRetry={() => void refreshSnapshot()}
        onGoBack={toDashboard}
      >
        {(loaded) =>
          loaded.session.state === "READY" ? (
            <>
              {handsFreeLifecycle !== undefined && readiness !== null ? (
                <HandsFreeListeningPanel
                  wakePhrase={readiness.interaction.wake_phrase}
                  busy={busy}
                  autoStart={handsFreeLifecycle.autoStart}
                  standby={handsFreeLifecycle.standby}
                  idleTimeoutMs={handsFreeLifecycle.idleTimeoutMs}
                  announce={handsFreeLifecycle.announce}
                  isSpeaking={handsFreeLifecycle.isSpeaking}
                  onBargeIn={(audio) =>
                    void submitAudioCommand(audio, {
                      requireWakePhrase: true,
                      listenMode: "barge_in",
                    })
                  }
                  onSegment={(audio, options) => {
                    if (options?.listenMode === "standby") {
                      void submitAudioCommand(audio, {
                        requireWakePhrase: true,
                        listenMode: "standby",
                      });
                    } else if (loaded.pending_action !== null) {
                      if (readiness.interaction.voice_only_confirmation === true) {
                        void confirmByVoice(audio, { viaWakePhrase: true });
                      }
                    } else {
                      void submitAudioCommand(audio, { requireWakePhrase: true });
                    }
                  }}
                />
              ) : null}
              {loaded.pending_action !== null ? (
                <main className="ready-pending" id="main-content">
                  <ConfirmationDialog
                    action={loaded.pending_action}
                    busy={busy}
                    onConfirm={confirmAction}
                  />
                  {readiness?.interaction.voice_only_confirmation === true ? (
                    <p className="voice-only-hint" role="note">
                      {(() => {
                        const phrase = voiceConfirmationPhrase(loaded.pending_action, loaded);
                        const wake = readiness.interaction.wake_phrase;
                        return phrase === null
                          ? "This proposal cannot be confirmed voice-only."
                          : `Hands-free: say "${wake}, ${phrase}" - or "${wake}, reject".`;
                      })()}
                    </p>
                  ) : null}
                </main>
              ) : (
                <ProtocolApprovedView
                  protocol={loaded.version}
                  session={loaded.session}
                  busy={busy}
                  onStart={startSession}
                />
              )}
            </>
          ) : (
            <SterileModeView
              client={client}
              readiness={readiness}
              snapshot={loaded}
              toolRegistry={toolRegistry.byName}
              activeStep={activeStep}
              verification={verification}
              message={message}
              media={media}
              busy={busy}
              muted={muted}
              onMutedChange={setMuted}
              onInvokeTool={invokeTool}
              onTextCommand={submitTextCommand}
              onAudioCommand={submitAudioCommand}
              onAudioConfirm={confirmByVoice}
              onVoiceReagentDecision={decideReagentByVoice}
              onProbeLabel={probeLabel}
              onVerifyFrames={verifyReagentFrames}
              verificationRequest={verificationRequest}
              verificationRequirementId={verificationRequirementId}
              handsFree={handsFreeLifecycle}
              onAcknowledgeAlarm={acknowledgeAlarm}
              speak={speak}
              isSpeaking={() => speech.isSpeaking()}
              onConfirm={confirmAction}
              onAsk={askProtocolQuestion}
              onVerify={verifyReagent}
              onConfirmVerification={confirmReagentVerification}
              onReplay={replayResponse}
              onRefresh={refreshSnapshot}
              onOpenNotebook={() =>
                navigate({ name: "notebook", sessionId: loaded.session.id })
              }
            />
          )
        }
      </ResourceGate>
    );
  } else {
    content = (
      <DashboardView
        protocols={dashboard.protocols}
        sessions={dashboard.sessions}
        loading={dashboard.loading}
        authoritative={dashboard.authoritative}
        error={dashboard.error}
        onSearch={dashboard.search}
        onOpenProtocol={(summary) =>
          navigate({ name: "protocol", versionId: summary.latest_version_id })
        }
        onResumeSession={(summary) =>
          navigate(
            summary.resumable
              ? { name: "session", sessionId: summary.session_id }
              : { name: "notebook", sessionId: summary.session_id },
          )
        }
        onImport={() => navigate({ name: "import" })}
        onRetry={() => void dashboard.search()}
      />
    );
  }

  if (route.name === "session" && autostart && !handsFreeArmed && readiness !== null) {
    content = (
      <>
        <HandsFreeStartPrompt
          wakePhrase={readiness.interaction.wake_phrase}
          kioskFallback={kioskFallback}
          onStart={armHandsFree}
        />
        {content}
      </>
    );
  } else if (route.name === "session" && kioskActive) {
    content = (
      <>
        <p className="kiosk-banner" role="note">
          Kiosk mode (this computer only): audio started without a tap. Close the window to
          leave kiosk mode.
        </p>
        {content}
      </>
    );
  }

  return (
    <AppShell
      readiness={readiness}
      degraded={degraded}
      route={route}
      sessionId={routeSessionId}
      message={message}
      error={error === null ? null : describeError(new Error(error))}
      onNavigate={navigate}
    >
      {content}
    </AppShell>
  );
}
