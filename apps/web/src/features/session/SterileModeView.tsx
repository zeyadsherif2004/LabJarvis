import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  Clock3,
  FileText,
  Mic,
  Pause,
  Play,
  RotateCcw,
  Send,
  SkipForward,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";

import type { ClientPort } from "../../api/client";
import type { ActionResult } from "../../actions";
import { ConfirmationDialog } from "../../components/ConfirmationDialog";
import { WarningBanner } from "../../components/WarningBanner";
import type { AudioCapturePort } from "../../media/audioCapture";
import {
  PUSH_TO_TALK_KEYS,
  loadPushToTalkKey,
  savePushToTalkKey,
  usePushToTalkKey,
  type PushToTalkKey,
} from "../../media/pushToTalk";
import type { RealtimeSessionFactory } from "../../media/realtimeSession";
import type {
  CaptureMetadata,
  GroundedAnswer,
  LabelProbe,
  NotebookEntry,
  ProtocolStep,
  ReadinessResponse,
  ReagentVerification,
  SessionSnapshot,
  ToolDescriptor,
} from "../../types";
import { HandsFreeListeningPanel, type ListenMode } from "./HandsFreeListeningPanel";
import { ProtocolQuestionPanel } from "./ProtocolQuestionPanel";
import { ReagentVerificationView } from "./ReagentVerificationView";
import { reagentVoicePhrase, routeWakeSegment, voiceConfirmationPhrase } from "./voicePhrase";
import { RealtimeVoicePanel } from "./RealtimeVoicePanel";
import { TimerPanel } from "./TimerPanel";
import { useRealtimeVoice } from "./useRealtimeVoice";

interface SterileModeViewProps {
  client: ClientPort;
  readiness: ReadinessResponse;
  snapshot: SessionSnapshot;
  toolRegistry: ReadonlyMap<string, ToolDescriptor>;
  activeStep: ProtocolStep | null;
  verification: ReagentVerification | null;
  message: string | null;
  media: AudioCapturePort;
  busy: boolean;
  muted: boolean;
  onMutedChange: (muted: boolean) => void;
  onInvokeTool: (tool: string, parameters?: Record<string, string>) => Promise<ActionResult>;
  onTextCommand: (transcript: string) => Promise<ActionResult>;
  onAudioCommand: (
    audio: Blob,
    options?: { requireWakePhrase?: boolean; listenMode?: ListenMode },
  ) => Promise<ActionResult>;
  /**
   * A recording made while a proposal is pending answers it by voice: push-to-talk,
   * or (voice-only mode) a wake-phrase segment with `viaWakePhrase`.
   */
  onAudioConfirm?:
    | ((audio: Blob, options?: { viaWakePhrase?: boolean }) => Promise<ActionResult>)
    | undefined;
  /** Voice-only mode: a wake-phrase answer to a pending label reading. */
  onVoiceReagentDecision?: ((audio: Blob) => Promise<ActionResult>) | undefined;
  /** Frames for a hands-free label check (see ReagentVerificationView). */
  onVerifyFrames?:
    | ((frames: Blob[], requirementId: string, capture?: CaptureMetadata) => Promise<ActionResult>)
    | undefined;
  /** Live scan: one unrecorded probe of a frame with the local OCR. */
  onProbeLabel?: ((frame: Blob, requirementId: string) => Promise<LabelProbe | null>) | undefined;
  /** Incremented when a spoken "check reagent" should start the camera. */
  verificationRequest?: number | undefined;
  /** The requirement the spoken request named (resolved by the server). */
  verificationRequirementId?: string | null | undefined;
  /** Hands-free lifecycle owned by the app so it survives view changes. */
  handsFree?: HandsFreeLifecycle | undefined;
  /** Silence sounding timer alarms (on screen; by voice it is "stop the alarm"). */
  onAcknowledgeAlarm?: (() => void) | undefined;
  speak?: ((text: string) => void) | undefined;
  isSpeaking?: (() => boolean) | undefined;
  onConfirm: (accepted: boolean) => Promise<ActionResult>;
  onAsk: (question: string) => Promise<ActionResult<GroundedAnswer>>;
  onVerify: (image: Blob, requirementId: string) => Promise<ActionResult>;
  onConfirmVerification: (
    accepted: boolean,
    correctedObservedLabel: string | null,
  ) => Promise<ActionResult>;
  onReplay: () => void;
  onRefresh: () => Promise<unknown> | void;
  onOpenNotebook: () => void;
  /** Injected in tests so the realtime transport can be driven deterministically. */
  createRealtimeSession?: RealtimeSessionFactory | undefined;
}

export interface HandsFreeLifecycle {
  /** Start listening on mount (autostart after the one required gesture). */
  autoStart: boolean;
  /** After "stop listening": only "start listening" is accepted. */
  standby: boolean;
  /** `null` while a session is open: no idle switch-off. */
  idleTimeoutMs: number | null;
  announce: (text: string) => void;
  isSpeaking: () => boolean;
}

function notebookSummary(entry: NotebookEntry): string {
  if (entry.entry_type === "measurement") {
    return [entry.payload.kind, entry.payload.value, entry.payload.unit]
      .filter(Boolean)
      .join(" ");
  }
  if (entry.entry_type === "step_skipped") {
    return `Skipped: ${entry.payload.reason ?? "reason recorded"}`;
  }
  if (entry.entry_type.startsWith("reagent_verification")) {
    return `${entry.payload.outcome ?? "Verification"}: ${entry.payload.observed ?? "label"}`;
  }
  return entry.payload.observation ?? entry.payload.tool ?? entry.entry_type;
}

export function SterileModeView(props: SterileModeViewProps) {
  const {
    client,
    readiness,
    snapshot,
    toolRegistry,
    activeStep,
    verification,
    message,
    media,
    busy,
    muted,
    onMutedChange,
    onInvokeTool,
    onTextCommand,
    onAudioCommand,
    onAudioConfirm,
    onVoiceReagentDecision,
    onProbeLabel,
    onVerifyFrames,
    verificationRequest = 0,
    verificationRequirementId = null,
    handsFree,
    onAcknowledgeAlarm,
    speak,
    isSpeaking,
    onConfirm,
    onAsk,
    onVerify,
    onConfirmVerification,
    onReplay,
    onRefresh,
    onOpenNotebook,
    createRealtimeSession,
  } = props;

  const { session, version, timers, pending_action: pendingAction, entries } = snapshot;
  const voiceOnly = readiness.interaction.voice_only_confirmation === true;
  const toolAvailable = (name: string): boolean => {
    const descriptor = toolRegistry.get(name);
    return descriptor !== undefined && descriptor.legal_session_states.includes(session.state);
  };
  const [typedCommand, setTypedCommand] = useState("");
  const [listening, setListening] = useState(false);
  const [microphoneError, setMicrophoneError] = useState<string | null>(null);
  const [skipping, setSkipping] = useState(false);
  const [skipReason, setSkipReason] = useState("");
  const [timerSeconds, setTimerSeconds] = useState("");
  const recording = useRef(false);
  const [pushToTalkKey, setPushToTalkKey] = useState<PushToTalkKey>(() => loadPushToTalkKey());

  // ------------------------------------------------------------ realtime voice
  //
  // Everything about the realtime lifecycle - reconnection, barge-in, the
  // spoken confirmation cycle, and keeping tool calls on the current session
  // version - lives in the controller hook. This view only renders it.

  const realtime = useRealtimeVoice({
    client,
    sessionId: session.id,
    sessionVersion: session.version,
    pendingAction,
    onRefresh,
    createSession: createRealtimeSession,
    confirmationWindowMs: readiness.interaction.realtime_confirmation_seconds * 1000,
  });

  // ------------------------------------------------------------- push to talk

  const stopListening = useCallback(async (): Promise<void> => {
    if (!recording.current) return;
    recording.current = false;
    setListening(false);
    try {
      const audio = await media.stop();
      if (audio.size === 0) return;
      // While a proposal is waiting, speech answers it ("confirm 37 degrees",
      // "reject"); the server reads and checks the reply deterministically.
      if (pendingAction !== null && onAudioConfirm !== undefined) {
        await onAudioConfirm(audio);
      } else {
        await onAudioCommand(audio);
      }
    } catch {
      setMicrophoneError("Audio recording failed. Typed command remains available.");
    }
  }, [media, onAudioCommand, onAudioConfirm, pendingAction]);

  const cancelListening = useCallback(async (): Promise<void> => {
    if (!recording.current) return;
    recording.current = false;
    setListening(false);
    try {
      await (media.cancel?.() ?? media.stop().then(() => undefined));
    } catch {
      // Nothing was submitted either way.
    }
  }, [media]);

  async function startListening(): Promise<void> {
    if (recording.current || busy) return;
    setMicrophoneError(null);
    try {
      await media.start();
      recording.current = true;
      setListening(true);
    } catch (error) {
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      setMicrophoneError(
        denied
          ? "Microphone permission denied. Typed command remains available."
          : "Microphone capture is unavailable. Typed command remains available.",
      );
    }
  }

  useEffect(() => {
    if (!listening) return undefined;
    const timeout = window.setTimeout(() => void stopListening(), 30_000);
    return () => window.clearTimeout(timeout);
  }, [listening, stopListening]);

  // Hold-to-talk from anywhere on the page (not only the focused button), so a
  // USB foot pedal that emulates a key works with gloved hands off the device.
  const interactive = session.state === "ACTIVE" || session.state === "PAUSED";
  usePushToTalkKey({
    key: pushToTalkKey,
    enabled: interactive,
    onPress: () => void startListening(),
    onRelease: () => void stopListening(),
    onCancel: () => void cancelListening(),
  });

  const recentEntries = entries.slice(-4).reverse();
  const terminal = session.state === "COMPLETED" || session.state === "ABORTED";

  if (terminal) {
    return (
      <main className="terminal-session" id="main-content">
        <h1>
          {session.state === "COMPLETED"
            ? "Session complete"
            : "Session ended before the last step"}
        </h1>
        <p>
          {session.steps.filter((step) => step.state === "COMPLETED").length} of{" "}
          {session.steps.length} steps were completed
          {session.steps.some((step) => step.state === "SKIPPED_WITH_REASON")
            ? `, and ${session.steps.filter((step) => step.state === "SKIPPED_WITH_REASON").length} were skipped with a recorded reason`
            : ""}
          .
        </p>
        {session.state === "ABORTED" ? (
          <WarningBanner title="This session was ended early">
            Steps after the last completed one were never performed. The notebook records
            exactly what was and was not done.
          </WarningBanner>
        ) : null}
        <button className="button button--primary" type="button" onClick={onOpenNotebook}>
          <BookOpen aria-hidden="true" /> Open the notebook
        </button>
      </main>
    );
  }

  return (
    <main className="sterile-mode" id="main-content">
      <div className="sterile-workspace">
        <section className="current-step" aria-labelledby="active-step-title">
          <div className="step-position">
            <span />
            <p>
              Step {activeStep?.number ?? "-"} of {version.steps.length}
            </p>
            <span />
          </div>
          <h1 id="active-step-title">
            {activeStep?.instruction ?? "No step is active in this session."}
          </h1>
          {session.label ? <p>Run label: {session.label}</p> : null}

          {activeStep !== null ? (
            <>
              <div className="instruction-meta">
                <a href="#source-excerpt" className="source-bracket">
                  <FileText aria-hidden="true" /> Page {activeStep.source.page}
                  {activeStep.source.section ? ` - ${activeStep.source.section}` : ""}
                </a>
                {activeStep.duration_seconds !== null ? (
                  <span className="step-timer">
                    <Clock3 aria-hidden="true" /> Protocol suggests{" "}
                    {Math.round(activeStep.duration_seconds / 60)} min
                  </span>
                ) : null}
              </div>
              <p className="source-excerpt" id="source-excerpt">
                Source excerpt: {activeStep.source.excerpt}
              </p>
              {activeStep.safety_notes.map((note) => (
                <WarningBanner title="Safety note from the protocol" key={note}>
                  {note}
                </WarningBanner>
              ))}
            </>
          ) : null}

          {session.state === "PAUSED" ? (
            <WarningBanner title="Session paused">
              Recording is suspended until you resume. Timers keep their remaining time.
            </WarningBanner>
          ) : null}

          <div className={`voice-surface${listening ? " voice-surface--listening" : ""}`}>
            <span className="waveform" aria-hidden="true" />
            <button
              className="voice-button"
              type="button"
              disabled={busy}
              aria-pressed={listening}
              onPointerDown={() => void startListening()}
              onPointerUp={() => void stopListening()}
              onPointerCancel={() => void stopListening()}
              onKeyDown={(event) => {
                if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                  event.preventDefault();
                  void startListening();
                }
              }}
              onKeyUp={(event) => {
                if (event.key === " " || event.key === "Enter") {
                  event.preventDefault();
                  void stopListening();
                }
              }}
            >
              <Mic aria-hidden="true" />
              <strong>
                {listening
                  ? pendingAction !== null
                    ? "Listening for your answer... release to submit"
                    : "Listening... release to submit"
                  : "Hold to speak"}
              </strong>
              <small>
                Hold this button, or hold {pushToTalkKey} / a foot pedal anywhere on this page;
                maximum 30 seconds; Escape cancels. Optional prefix:{" "}
                {readiness.interaction.wake_phrase}.
                {pendingAction !== null
                  ? " A proposal is waiting: say \"confirm\" with the value, or \"reject\"."
                  : ""}
              </small>
            </button>
            <label className="ptt-key">
              Push-to-talk key or foot pedal
              <select
                value={pushToTalkKey}
                onChange={(event) => {
                  const next = event.target.value as PushToTalkKey;
                  setPushToTalkKey(next);
                  savePushToTalkKey(next);
                }}
              >
                {PUSH_TO_TALK_KEYS.map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </select>
            </label>
            <p className="sr-only" role="status" aria-live="assertive">
              {listening ? "Microphone is listening" : ""}
            </p>
          </div>
          {microphoneError !== null ? (
            <p className="microphone-error" role="alert">
              {microphoneError}
            </p>
          ) : null}

          <form
            className="typed-command"
            onSubmit={(event) => {
              event.preventDefault();
              const command = typedCommand.trim();
              if (!command) return;
              void onTextCommand(command).then((result) => {
                if (result.ok) setTypedCommand("");
              });
            }}
          >
            <label htmlFor="typed-command">Typed command</label>
            <div>
              <textarea
                id="typed-command"
                value={typedCommand}
                rows={2}
                disabled={busy}
                placeholder="Type your command here..."
                onChange={(event) => setTypedCommand(event.target.value)}
              />
              <button className="button button--primary" type="submit" disabled={busy}>
                <Send aria-hidden="true" /> Submit command
              </button>
            </div>
          </form>

          {message !== null ? (
            <p className="operator-feedback" role="status">
              {message}
            </p>
          ) : null}

          {readiness.interaction.hands_free_listening === true ? (
            <HandsFreeListeningPanel
              wakePhrase={readiness.interaction.wake_phrase}
              busy={busy || listening}
              autoStart={handsFree?.autoStart ?? false}
              standby={handsFree?.standby ?? false}
              announce={handsFree?.announce}
              {...(handsFree !== undefined
                ? { idleTimeoutMs: handsFree.idleTimeoutMs, isSpeaking: handsFree.isSpeaking }
                : {})}
              onBargeIn={
                handsFree === undefined
                  ? undefined
                  : (audio) =>
                      void onAudioCommand(audio, {
                        requireWakePhrase: true,
                        listenMode: "barge_in",
                      })
              }
              onSegment={(audio, options) => {
                // In standby only "start listening" is accepted, whatever waits.
                if (options?.listenMode === "standby") {
                  void onAudioCommand(audio, { requireWakePhrase: true, listenMode: "standby" });
                  return;
                }
                // Voice-only mode lets a value-bearing reply ("Jarvis, confirm
                // 10 millilitres") answer a waiting proposal or reading; the
                // server decides whether it counts.
                const route = routeWakeSegment({
                  proposalPending: pendingAction !== null,
                  readingPending: verification?.decision === "PENDING",
                  voiceOnly,
                });
                if (route === "confirm_proposal") {
                  void onAudioConfirm?.(audio, { viaWakePhrase: true });
                } else if (route === "decide_reading") {
                  void onVoiceReagentDecision?.(audio);
                } else if (route === "command") {
                  void onAudioCommand(audio, { requireWakePhrase: true });
                }
              }}
            />
          ) : null}

          <ProtocolQuestionPanel
            busy={busy}
            available={toolAvailable("answer_protocol_question")}
            onAsk={onAsk}
          />

          {pendingAction !== null ? (
            <ConfirmationDialog
              action={pendingAction}
              busy={busy}
              onConfirm={onConfirm}
              readbackOnly={readiness.study?.enabled === true && readiness.study.condition === "readback"}
            />
          ) : null}
          {pendingAction !== null && voiceOnly ? (
            <p className="voice-only-hint" role="note">
              {(() => {
                const phrase = voiceConfirmationPhrase(pendingAction, snapshot, {
                  voiceObservations: readiness.interaction.voice_observations === true,
                });
                return phrase === null
                  ? "This proposal cannot be confirmed voice-only: use push-to-talk or the buttons."
                  : `Hands-free: say "${readiness.interaction.wake_phrase}, ${phrase}" - or "${readiness.interaction.wake_phrase}, reject".`;
              })()}
            </p>
          ) : null}
        </section>

        <aside className="protocol-rail" aria-labelledby="protocol-rail-title">
          <h2 id="protocol-rail-title">Protocol steps</h2>
          <ol>
            {version.steps.map((step) => {
              const state =
                session.steps.find((item) => item.step_id === step.id)?.state ?? "NOT_STARTED";
              const current = step.id === session.active_step_id;
              return (
                <li
                  className={
                    current ? "protocol-rail-step protocol-rail-step--current" : "protocol-rail-step"
                  }
                  key={step.id}
                >
                  <span className="rail-number">{step.number}</span>
                  <div>
                    <strong>{step.instruction}</strong>
                    <small>
                      Page {step.source.page}
                      {step.source.section ? ` - ${step.source.section}` : ""}
                    </small>
                    <span className="rail-state">
                      {state === "COMPLETED" ? <CheckCircle2 aria-hidden="true" /> : null}
                      {state.replaceAll("_", " ").toLowerCase()}
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>

          <TimerPanel
            timers={timers}
            steps={version.steps}
            onAcknowledgeAlarm={onAcknowledgeAlarm}
            busy={busy}
            canPause={toolAvailable("pause_timer")}
            canResume={toolAvailable("resume_timer")}
            canCancel={toolAvailable("cancel_timer")}
            onPause={(timerId) => void onInvokeTool("pause_timer", { timer_id: timerId })}
            onResume={(timerId) => void onInvokeTool("resume_timer", { timer_id: timerId })}
            onCancel={(timerId) => void onInvokeTool("cancel_timer", { timer_id: timerId })}
          />

          <form
            className="timer-form"
            onSubmit={(event) => {
              event.preventDefault();
              const seconds = Number.parseInt(timerSeconds, 10);
              if (!Number.isSafeInteger(seconds) || seconds < 1) return;
              void onInvokeTool("set_timer", {
                duration_seconds: String(seconds),
                label: activeStep?.title ?? "timer",
              }).then((result) => {
                if (result.ok) setTimerSeconds("");
              });
            }}
          >
            <label htmlFor="timer-seconds">Start a timer (seconds)</label>
            <div>
              <input
                id="timer-seconds"
                type="number"
                min={1}
                max={604800}
                value={timerSeconds}
                disabled={
                  busy || session.state !== "ACTIVE" || !toolAvailable("set_timer")
                }
                onChange={(event) => setTimerSeconds(event.target.value)}
              />
              <button
                className="button button--secondary"
                type="submit"
                disabled={
                  busy || session.state !== "ACTIVE" || !toolAvailable("set_timer")
                }
              >
                <Clock3 aria-hidden="true" /> Start timer
              </button>
            </div>
          </form>

          <ReagentVerificationView
            requirements={activeStep?.requirements ?? []}
            progress={snapshot.requirement_progress}
            result={verification}
            mode={readiness.mode}
            busy={busy}
            onVerify={onVerify}
            onConfirm={onConfirmVerification}
            onVerifyFrames={onVerifyFrames}
            autoCaptureRequest={verificationRequest}
            autoCaptureRequirementId={verificationRequirementId}
            speak={speak}
            isSpeaking={isSpeaking}
            hostedSingleCapture={readiness.stages?.vision?.locality === "hosted"}
            onProbe={onProbeLabel}
            liveScan={
              readiness.interaction.live_label_scan === true
                ? {
                    requiredConsecutive: readiness.interaction.live_scan_required_consecutive ?? 3,
                    intervalMs: readiness.interaction.live_scan_interval_ms ?? 400,
                    timeoutMs: readiness.interaction.live_scan_timeout_ms ?? 12_000,
                  }
                : null
            }
            voiceOnlyHint={
              voiceOnly && verification?.decision === "PENDING"
                ? (() => {
                    const phrase = reagentVoicePhrase(verification);
                    const wake = readiness.interaction.wake_phrase;
                    return phrase === null
                      ? `Hands-free: a ${verification.outcome} reading can only be rejected - say "${wake}, reject".`
                      : `Hands-free: say "${wake}, ${phrase}" - or "${wake}, reject".`;
                  })()
                : null
            }
          />

          <RealtimeVoicePanel
            enabled={readiness.interaction.realtime_enabled}
            status={realtime.status}
            consented={realtime.consented}
            muted={muted}
            modelName={readiness.interaction.realtime_model}
            provider={readiness.hosted_provider ?? "the configured provider"}
            detail={realtime.detail}
            spoken={realtime.spoken}
            reconnectAttempt={realtime.reconnectAttempt}
            maxReconnectAttempts={realtime.maxReconnectAttempts}
            onConsent={() => void realtime.start()}
            onStop={realtime.stop}
            onRetry={() => void realtime.retry()}
            onMutedChange={(next) => {
              onMutedChange(next);
              realtime.setMuted(next);
            }}
          />
        </aside>
      </div>

      <div className="session-action-bar" aria-label="Session actions">
        {/* These call typed application endpoints directly. A button already
            states its intent, so routing it through a language model would add
            latency and a failure mode without adding any safety. */}
        <button
          className="button button--secondary"
          type="button"
          disabled={
            busy ||
            pendingAction !== null ||
            !toolAvailable(session.state === "PAUSED" ? "resume_session" : "pause_session")
          }
          onClick={() =>
            void onInvokeTool(session.state === "PAUSED" ? "resume_session" : "pause_session")
          }
        >
          {session.state === "PAUSED" ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
          {session.state === "PAUSED" ? "Resume session" : "Pause session"}
        </button>
        <button
          className="button button--secondary"
          type="button"
          disabled={
            busy ||
            pendingAction !== null ||
            session.state !== "ACTIVE" ||
            !toolAvailable("complete_step")
          }
          onClick={() => void onInvokeTool("complete_step")}
        >
          <SkipForward aria-hidden="true" /> Complete current step
        </button>
        <button
          className="button button--secondary"
          type="button"
          disabled={
            busy ||
            pendingAction !== null ||
            session.state !== "ACTIVE" ||
            !toolAvailable("skip_step_with_reason")
          }
          onClick={() => setSkipping(true)}
        >
          <SkipForward aria-hidden="true" /> Skip step with a reason
        </button>
        <button
          className="button button--secondary"
          type="button"
          disabled={busy || pendingAction !== null || !toolAvailable("end_session")}
          onClick={() => void onInvokeTool("end_session")}
        >
          <Square aria-hidden="true" /> End session early
        </button>
        <button className="button button--secondary" type="button" onClick={onReplay}>
          <RotateCcw aria-hidden="true" /> Replay response
        </button>
        <button
          className="button button--secondary"
          type="button"
          aria-pressed={muted}
          onClick={() => onMutedChange(!muted)}
        >
          {muted ? <VolumeX aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
          {muted ? "Unmute" : "Mute"}
        </button>
      </div>

      {skipping ? (
        <form
          className="skip-form"
          aria-label="Skip this step"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = skipReason.trim();
            if (!reason) return;
            void onInvokeTool("skip_step_with_reason", { reason }).then((result) => {
              if (result.ok) {
                setSkipping(false);
                setSkipReason("");
              }
            });
          }}
        >
          <label htmlFor="skip-reason">
            Why is this step being skipped? The reason is recorded permanently.
          </label>
          <textarea
            id="skip-reason"
            rows={2}
            required
            value={skipReason}
            onChange={(event) => setSkipReason(event.target.value)}
          />
          <div className="skip-actions">
            <button
              className="button button--primary"
              type="submit"
              disabled={busy || !toolAvailable("skip_step_with_reason")}
            >
              Skip step and record the reason
            </button>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => {
                setSkipping(false);
                setSkipReason("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      <section className="activity-rail" aria-labelledby="activity-title">
        <h2 id="activity-title">
          Notebook activity <span>(append-only)</span>
        </h2>
        {recentEntries.length === 0 ? (
          <p className="empty-activity">
            Confirmed actions and verification results appear here.
          </p>
        ) : (
          <ol>
            {recentEntries.map((entry) => (
              <li key={entry.id}>
                <time dateTime={entry.created_at}>
                  {new Date(entry.created_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
                <strong>{entry.entry_type.replaceAll("_", " ")}</strong>
                <span>{notebookSummary(entry)}</span>
                <small>{entry.source.replaceAll("_", " ")}</small>
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}
