import { useCallback, useEffect, useRef, useState } from "react";

import type { ClientPort } from "../../api/client";
import { errorSentence } from "../../errors";
import {
  MicrophoneIsolationFailed,
  ReadbackAbandoned,
  RecordingRefused,
  WebRtcRealtimeSession,
  openConfirmationCapture,
  type RealtimeActivity,
  type RealtimeCallbacks,
  type RealtimeSessionFactory,
  type RealtimeSessionPort,
  type RealtimeToolCall,
} from "../../media/realtimeSession";
import type { PendingAction, RealtimeStatus } from "../../types";

/**
 * The realtime voice controller.
 *
 * The transport module knows how to hold a WebRTC connection open. This hook
 * owns everything that decides *what happens* around it, which is where the
 * safety properties live:
 *
 * - **The model never confirms its own proposal.** When the backend answers a
 *   tool call with a pending action, this hook reads the proposal back and then
 *   records a fresh clip of the operator answering. That recording is the
 *   evidence the backend requires. There is no tool the model could call to
 *   supply it.
 * - **Tool calls always carry the current session version.** The version lives
 *   in a ref that tracks the latest snapshot, not in the closure created when
 *   the connection was opened, so a second action after a state change no
 *   longer fails with a conflict.
 * - **Reconnecting actually reconnects.** A dropped peer is torn down, the
 *   session is re-read from the database, a new short-lived grant is minted,
 *   and a new peer is built, with bounded attempts and backoff. The status only
 *   says connected when a new connection exists.
 * - **Nothing in flight is replayed.** Recovery re-reads authority. Tool calls
 *   are keyed by the provider call id, so a redelivered call reports the
 *   recorded outcome instead of repeating it.
 */

export interface RealtimeVoiceOptions {
  client: ClientPort;
  sessionId: string;
  /** The authoritative version from the most recent snapshot. */
  sessionVersion: number;
  pendingAction: PendingAction | null;
  /** Re-read the authoritative snapshot. */
  onRefresh: () => Promise<unknown> | void;
  /** Injected in tests; production builds a real WebRTC session. */
  createSession?: RealtimeSessionFactory | undefined;
  /** How long a confirmation recording may run. */
  confirmationWindowMs?: number;
  maxReconnectAttempts?: number;
  /** Injected in tests so backoff does not make the suite slow. */
  delay?: (milliseconds: number) => Promise<void>;
  /** How many times an unclear confirmation is re-asked before deferring. */
  maxConfirmationAttempts?: number;
}

export interface RealtimeVoiceController {
  status: RealtimeStatus;
  detail: string | null;
  /** Which reconnection attempt is in progress; 0 when not reconnecting. */
  reconnectAttempt: number;
  maxReconnectAttempts: number;
  consented: boolean;
  /** The last sentence LabJarvis was asked to say, shown as a visible alternative. */
  spoken: string | null;
  start: () => Promise<void>;
  stop: () => void;
  retry: () => Promise<void>;
  setMuted: (muted: boolean) => void;
}

const DEFAULT_CONFIRMATION_WINDOW_MS = 5000;
const DEFAULT_MAX_RECONNECT_ATTEMPTS = 3;
const DEFAULT_MAX_CONFIRMATION_ATTEMPTS = 2;

const ACTIVITY_STATUS: Record<RealtimeActivity, RealtimeStatus> = {
  listening: "listening",
  operator_speaking: "listening",
  processing: "processing",
  speaking: "speaking",
};

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, milliseconds);
  });
}

function idempotencyKey(prefix: string): string {
  const suffix =
    globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random()}`;
  return `${prefix}-${suffix}`;
}

/**
 * Say what went wrong, and make clear that nothing was recorded.
 *
 * Every branch here ends in the same place - the proposal is still open and the
 * on-screen controls still work - because every one of these is a case where
 * the system could not establish that a person answered.
 */
export function explainConfirmationFailure(caught: unknown): string {
  if (caught instanceof MicrophoneIsolationFailed) {
    // Deliberately does not say the connection is fine. Isolation is what keeps
    // the provider from answering the operator's confirmation, so without it
    // there is no safe hands-free path and the fallback has to be stated.
    return (
      "LabJarvis could not keep the microphone away from the voice service, so it " +
      "did not record a confirmation. Nothing was recorded. Confirm on screen " +
      "instead, and restart hands-free voice to reconnect."
    );
  }
  if (caught instanceof ReadbackAbandoned) {
    switch (caught.reason) {
      case "interrupted":
        return (
          "You spoke while LabJarvis was still reading the proposal back, so that " +
          "recording was discarded. Nothing was recorded. It will read the " +
          "proposal again, or you can confirm on screen."
        );
      case "timeout":
        return (
          "LabJarvis could not finish reading the proposal back, so it did not " +
          "start listening. Nothing was recorded. Confirm on screen instead."
        );
      case "provider_error":
        return (
          "The voice service failed while reading the proposal back. Nothing was " +
          "recorded. Confirm on screen instead."
        );
      case "closed":
        return (
          "The voice connection closed while reading the proposal back. Nothing " +
          "was recorded. Confirm on screen instead."
        );
      // The three documented non-success terminal states of `response.done`.
      // Kept apart from each other because "it was cut short" and "it was
      // cancelled" mean different things to someone deciding what to do next.
      case "cancelled":
        return (
          "The readback was cancelled before it finished, so LabJarvis did not " +
          "start listening. Nothing was recorded. Confirm on screen instead."
        );
      case "failed":
        return (
          "The voice service could not read the proposal back. Nothing was " +
          "recorded. Confirm on screen instead."
        );
      case "incomplete":
        return (
          "The readback stopped part-way through, so you may not have heard the " +
          "whole proposal. Nothing was recorded. Confirm on screen instead."
        );
      case "unknown_status":
        return (
          "LabJarvis could not tell whether the proposal was read back in full, " +
          "so it did not start listening. Nothing was recorded. Confirm on " +
          "screen instead."
        );
      case "playback_unverifiable":
        return (
          "LabJarvis could not confirm that the speaker had gone quiet, so it did " +
          "not start listening - a recording made over the readback could contain " +
          "its own words. Nothing was recorded. Confirm on screen instead."
        );
      case "busy":
        return (
          "LabJarvis was already reading something back. Nothing was recorded. " +
          "Confirm on screen instead."
        );
    }
  }
  if (caught instanceof RecordingRefused) {
    return `${caught.message} Nothing was recorded. Confirm on screen instead.`;
  }
  return `${errorSentence(caught)} Nothing was recorded. Confirm on screen instead.`;
}

/** The exact sentence the operator must hear before anything is recorded. */
export function proposalReadback(action: PendingAction): string {
  const { payload } = action;
  switch (action.intent) {
    case "RECORD_MEASUREMENT":
      return (
        `I heard ${payload.value ?? ""} ${payload.unit ?? ""} for ` +
        `${payload.kind ?? "this measurement"}. ` +
        `Nothing is recorded yet. Say: confirm ${payload.value ?? ""} ${payload.unit ?? ""}. ` +
        `Or say: reject.`
      );
    case "RECORD_OBSERVATION":
      return `I heard: ${payload.observation ?? ""}. Say confirm to record it, or reject.`;
    case "COMPLETE_STEP":
      return "This would mark the current step complete. Say confirm, or reject.";
    case "SKIP_STEP_WITH_REASON":
      return (
        `This would skip the step permanently, recording: ${payload.reason ?? ""}. ` +
        `Say confirm, or reject.`
      );
    case "END_SESSION":
      return "This would end the session early and cannot be undone. Say confirm, or reject.";
    default:
      return `This would ${action.intent.replaceAll("_", " ").toLowerCase()}. Say confirm, or reject.`;
  }
}

export function useRealtimeVoice(options: RealtimeVoiceOptions): RealtimeVoiceController {
  const {
    client,
    sessionId,
    sessionVersion,
    pendingAction,
    onRefresh,
    createSession,
    confirmationWindowMs = DEFAULT_CONFIRMATION_WINDOW_MS,
    maxReconnectAttempts = DEFAULT_MAX_RECONNECT_ATTEMPTS,
    delay = sleep,
    maxConfirmationAttempts = DEFAULT_MAX_CONFIRMATION_ATTEMPTS,
  } = options;

  const [status, setStatus] = useState<RealtimeStatus>("idle");
  const [detail, setDetail] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [consented, setConsented] = useState(false);
  const [spoken, setSpoken] = useState<string | null>(null);

  const session = useRef<RealtimeSessionPort | null>(null);
  // The authoritative version, kept current. Capturing it in the callback that
  // opened the connection meant the second consequential action of a session
  // was submitted against a version the database had already moved past.
  const versionRef = useRef(sessionVersion);
  versionRef.current = sessionVersion;
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;
  const stoppedRef = useRef(false);
  const reconnectingRef = useRef(false);
  const mutedRef = useRef(false);
  // Attempts already spent asking about one specific proposal, so an unclear
  // answer cannot put the interface in a loop.
  const confirmationAttempts = useRef<{ actionId: string; attempts: number }>({
    actionId: "",
    attempts: 0,
  });
  const handlingConfirmation = useRef(false);
  // The proposal and session currently on screen. A readback takes seconds, and
  // in that time the operator can navigate away, the session can advance, or a
  // different proposal can arrive; a clip recorded for one must never be
  // submitted against another.
  const pendingActionRef = useRef<PendingAction | null>(pendingAction);
  pendingActionRef.current = pendingAction;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  // Bumped whenever a cycle finishes, so a proposal that arrived while another
  // was being handled still gets its turn.
  const [cycleTick, setCycleTick] = useState(0);

  const handleToolCall = useCallback(
    async (call: RealtimeToolCall): Promise<Record<string, unknown>> => {
      try {
        const result = await client.invokeTool(
          sessionId,
          call.name,
          versionRef.current,
          call.parameters,
          // Keyed by the provider's call id, so a reconnect that redelivers the
          // same call reports the recorded outcome instead of repeating it.
          `realtime-${call.callId}`,
        );
        await refreshRef.current();
        return {
          committed: result.committed,
          awaiting_confirmation: result.pending_action !== null,
          message: result.message,
          data: result.data,
        };
      } catch (caught) {
        return { committed: false, error: errorSentence(caught) };
      }
    },
    [client, sessionId],
  );

  const reconnectRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const reconnect = useCallback(async (): Promise<void> => {
    await reconnectRef.current();
  }, []);

  const buildCallbacks = useCallback(
    (): RealtimeCallbacks => ({
      onStatusChange: (next) => {
        if (stoppedRef.current) return;
        if (next === "connecting") setStatus("connecting");
        else if (next === "connected") {
          setStatus(mutedRef.current ? "muted" : "listening");
          setDetail(null);
        } else if (next === "interrupted" || next === "failed") {
          // Losing the peer is not itself a reconnection. The reconnect loop
          // below decides, and the status only claims success once a new
          // connection exists.
          void reconnect();
        } else setStatus("stopped");
      },
      onActivity: (activity) => {
        if (stoppedRef.current) return;
        setStatus((current) =>
          current === "awaiting_confirmation" || current === "reconnecting"
            ? current
            : ACTIVITY_STATUS[activity],
        );
      },
      onToolCall: handleToolCall,
      onTranscript: () => undefined,
      onError: (message) => {
        setDetail(message);
        setStatus("error");
      },
    }),
    // `reconnect` reaches the current implementation through a ref, so it is
    // deliberately not a dependency: making it one would recreate the callbacks
    // on every render and tear the connection down with them.
    [handleToolCall, reconnect],
  );

  const openConnection = useCallback(async (): Promise<void> => {
    const grant = await client.createRealtimeSession(sessionId);
    const factory: RealtimeSessionFactory =
      createSession ?? ((callbacks) => new WebRtcRealtimeSession(callbacks));
    const connection = factory(buildCallbacks());
    session.current = connection;
    await connection.connect(grant);
    connection.setMuted(mutedRef.current);
  }, [buildCallbacks, client, createSession, sessionId]);

  reconnectRef.current = async (): Promise<void> => {
    if (stoppedRef.current || reconnectingRef.current) return;
    reconnectingRef.current = true;
    setStatus("reconnecting");
    try {
      for (let attempt = 1; attempt <= maxReconnectAttempts; attempt += 1) {
        if (stoppedRef.current) return;
        setReconnectAttempt(attempt);
        // Tear the old peer down first. Leaving it half-open was what made the
        // previous "reconnecting" label a claim with nothing behind it.
        session.current?.stop();
        session.current = null;
        await delay(Math.min(2 ** (attempt - 1), 8) * 1000);
        if (stoppedRef.current) return;
        try {
          // Re-read authority before speaking again, so the model never
          // resumes from what the conversation appeared to establish.
          await refreshRef.current();
          await openConnection();
          setReconnectAttempt(0);
          setDetail(null);
          setStatus(mutedRef.current ? "muted" : "listening");
          return;
        } catch (caught) {
          setDetail(errorSentence(caught));
        }
      }
      setStatus("error");
      setDetail(
        `Hands-free voice could not reconnect after ${maxReconnectAttempts} attempts. ` +
          "Nothing was lost, and nothing unconfirmed was repeated. " +
          "Push-to-talk and typed commands still work.",
      );
    } finally {
      reconnectingRef.current = false;
    }
  };

  const stop = useCallback(() => {
    stoppedRef.current = true;
    session.current?.stop();
    session.current = null;
    setReconnectAttempt(0);
    setStatus("stopped");
  }, []);

  const start = useCallback(async (): Promise<void> => {
    stoppedRef.current = false;
    setConsented(true);
    setDetail(null);
    setStatus("requesting_permission");
    try {
      await openConnection();
    } catch (caught) {
      setDetail(errorSentence(caught));
      setStatus("error");
    }
  }, [openConnection]);

  const retry = useCallback(async (): Promise<void> => {
    stoppedRef.current = false;
    reconnectingRef.current = false;
    setReconnectAttempt(0);
    await reconnect();
  }, [reconnect]);

  const setMuted = useCallback((muted: boolean) => {
    mutedRef.current = muted;
    session.current?.setMuted(muted);
    setStatus((current) =>
      current === "listening" || current === "muted"
        ? muted
          ? "muted"
          : "listening"
        : current,
    );
  }, []);

  // --------------------------------------------------- the confirmation cycle

  useEffect(() => {
    if (pendingAction === null) {
      confirmationAttempts.current = { actionId: "", attempts: 0 };
      return;
    }
    if (session.current === null || stoppedRef.current) return;
    if (handlingConfirmation.current) return;

    const tracker = confirmationAttempts.current;
    if (tracker.actionId !== pendingAction.id) {
      confirmationAttempts.current = { actionId: pendingAction.id, attempts: 0 };
    }
    if (confirmationAttempts.current.attempts >= maxConfirmationAttempts) return;

    handlingConfirmation.current = true;
    const action = pendingAction;

    void (async () => {
      const connection = session.current;
      try {
        confirmationAttempts.current = {
          actionId: action.id,
          attempts: confirmationAttempts.current.attempts + 1,
        };
        if (connection === null) return;

        /**
         * Nothing on screen has changed under this cycle.
         *
         * Checked before recording and again before submitting, because a
         * readback takes seconds and a clip captured for one proposal must
         * never be sent against another - or against a reconnected session
         * whose version the clip was never checked against.
         */
        const stillOurs = (): boolean =>
          !stoppedRef.current &&
          session.current === connection &&
          sessionIdRef.current === sessionId &&
          pendingActionRef.current?.id === action.id;

        setStatus("awaiting_confirmation");

        /**
         * One transaction owns microphone isolation, start to finish.
         *
         * The provider must be deaf for the *whole* cycle, not just the
         * readback. Restoring forwarding as soon as the readback finished -
         * which is what the previous implementation did - left the sender
         * attached while the local recorder was open, so default provider VAD
         * could hear the operator finish speaking, generate a response by
         * itself, and talk into the confirmation clip.
         *
         * The local track stays live throughout, because the confirmation is
         * recorded from it. `openConfirmationCapture` rejects if the track
         * cannot be detached, and then nothing is spoken and nothing recorded.
         */
        const capture = await openConfirmationCapture(connection);
        let audio: Blob;
        let isolationFailure: MicrophoneIsolationFailed | null = null;
        try {
          // The whole turn-boundary property is in this await. It resolves only
          // once the response *correlated to this request* has completed with
          // status `completed`, and a working playback monitor has positively
          // observed the speaker go quiet. Every other outcome rejects.
          setSpoken(proposalReadback(action));
          await connection.say(proposalReadback(action));

          if (!stillOurs()) return;

          setStatus("capturing_confirmation");
          // A non-speech cue, so the operator knows the turn is theirs. Speech
          // here would be another thing the microphone could pick up.
          await connection.playCaptureCue();
          if (!stillOurs()) return;

          audio = await connection.captureUtterance(confirmationWindowMs);
        } finally {
          // The only place forwarding comes back, and it runs on every path:
          // success, readback failure, discarded clip, or identity change.
          isolationFailure = await capture.release();
        }

        if (!stillOurs()) return;
        if (audio.size === 0) {
          setDetail("Nothing was heard. Confirm on screen, or say it again.");
          setStatus("awaiting_confirmation");
          return;
        }

        const outcome = await client.confirmActionByVoice(
          action.id,
          audio,
          // Read at submission time, not at proposal time, so the version sent
          // is the one authority currently holds.
          versionRef.current,
          idempotencyKey(`spoken-${action.id}`),
        );
        await refreshRef.current();
        setSpoken(outcome.response_text);
        if (isolationFailure !== null) {
          // The clip was captured under correct isolation, so it is valid
          // evidence and was submitted. But the microphone no longer reaches
          // the provider, and reporting "listening" would be a false claim
          // about the connection.
          setStatus("error");
          setDetail(explainConfirmationFailure(isolationFailure));
          return;
        }
        setStatus(outcome.decision === "ambiguous" ? "awaiting_confirmation" : "listening");
        setDetail(outcome.decision === "ambiguous" ? outcome.response_text : null);
        // Speaking the outcome is best-effort; the authoritative wording is
        // already on screen. `announce` rather than `say` because this is not a
        // turn boundary and must not open a waiter.
        if (session.current === connection) {
          void connection.announce(outcome.response_text).catch(() => undefined);
        }
      } catch (caught) {
        setDetail(explainConfirmationFailure(caught));
        setStatus("awaiting_confirmation");
      } finally {
        handlingConfirmation.current = false;
        setCycleTick((tick) => tick + 1);
      }
    })();
  }, [
    client,
    confirmationWindowMs,
    cycleTick,
    maxConfirmationAttempts,
    pendingAction,
    sessionId,
  ]);

  useEffect(
    () => () => {
      stoppedRef.current = true;
      session.current?.stop();
      session.current = null;
    },
    [],
  );

  return {
    status,
    detail,
    reconnectAttempt,
    maxReconnectAttempts,
    consented,
    spoken,
    start,
    stop,
    retry,
    setMuted,
  };
}
