import { useCallback } from "react";

import {
  failedAction,
  successfulAction,
  successfulResult,
  type ActionFailure,
  type ActionResult,
} from "../../actions";
import type { ClientPort } from "../../api/client";
import type { Route } from "../../routing";
import type { CommandResponse, GroundedAnswer, SessionSnapshot } from "../../types";

interface SessionCommandOptions {
  client: ClientPort;
  snapshot: SessionSnapshot | null;
  refreshSnapshot: () => Promise<SessionSnapshot | null>;
  reloadNotebook: () => Promise<unknown>;
  navigate: (route: Route) => void;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  setMessage: (message: string | null) => void;
  handleActionError: (caught: unknown) => ActionFailure;
  /** Told about every interpreted command, e.g. to start a voice-requested label check. */
  onCommandResult?: (result: CommandResponse) => void;
  /**
   * Interface controls said out loud ("stop listening", "mute", "say that
   * again", "stop", "stop the alarm"). They never touch the record; the view
   * applies them and decides whether the response is spoken.
   */
  onVoiceControl?: (control: string, responseText: string) => void;
}

/** Intents that are interface controls rather than commands. */
export const VOICE_CONTROLS = new Set([
  "STOP_LISTENING",
  "START_LISTENING",
  "MUTE_SPEECH",
  "UNMUTE_SPEECH",
  "REPEAT_LAST_SPOKEN",
  "STOP_SPEAKING",
  "ACKNOWLEDGE_ALARM",
]);

/** Segments the server discarded without trace: nothing to say or show. */
const SILENT_INTENTS = new Set(["WAKE_PHRASE_ABSENT", "LISTEN_MODE_IGNORED"]);

function idempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-session-${Date.now().toString(36)}`;
}

export function useSessionCommands(options: SessionCommandOptions) {
  const {
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
  } = options;

  const startSession = useCallback(async (): Promise<ActionResult> => {
    if (snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      await client.startSession(snapshot.session.id, snapshot.session.version);
      await refreshSnapshot();
      setMessage("Session started. The first step is active.");
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  const invokeTool = useCallback(
    async (
      tool: string,
      parameters: Record<string, string> = {},
    ): Promise<ActionResult> => {
      if (snapshot === null) return failedAction("STALE_VIEW");
      setBusy(true);
      setError(null);
      try {
        const result = await client.invokeTool(
          snapshot.session.id,
          tool,
          snapshot.session.version,
          parameters,
          idempotencyKey(),
        );
        setMessage(result.message);
        await refreshSnapshot();
        return successfulAction;
      } catch (caught) {
        return handleActionError(caught);
      } finally {
        setBusy(false);
      }
    },
    [client, handleActionError, refreshSnapshot, setBusy, setError, setMessage, snapshot],
  );

  const submitTextCommand = useCallback(async (transcript: string): Promise<ActionResult> => {
    if (snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const result = await client.submitTextCommand(
        snapshot.session.id,
        transcript,
        snapshot.session.version,
        idempotencyKey(),
      );
      setMessage(result.response_text);
      await refreshSnapshot();
      onCommandResult?.(result);
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, onCommandResult, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  const submitAudioCommand = useCallback(async (
    audio: Blob,
    options: { requireWakePhrase?: boolean; listenMode?: "command" | "standby" | "barge_in" } = {},
  ): Promise<ActionResult> => {
    if (snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const result = await client.submitAudioCommand(
        snapshot.session.id,
        audio,
        snapshot.session.version,
        idempotencyKey(),
        options,
      );
      // Speech that was not addressed to LabJarvis is discarded silently.
      if (SILENT_INTENTS.has(result.intent)) return successfulAction;
      if (VOICE_CONTROLS.has(result.intent)) {
        onVoiceControl?.(result.intent, result.response_text);
        if (result.intent === "ACKNOWLEDGE_ALARM") await refreshSnapshot();
        return successfulAction;
      }
      setMessage(result.response_text);
      await refreshSnapshot();
      onCommandResult?.(result);
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, onCommandResult, onVoiceControl, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  /** Answer the pending proposal with a spoken reply ("confirm 37 degrees"). */
  const confirmByVoice = useCallback(async (
    audio: Blob,
    options: { viaWakePhrase?: boolean } = {},
  ): Promise<ActionResult> => {
    if (snapshot?.pending_action == null) return failedAction("STALE_VIEW");
    const sessionId = snapshot.session.id;
    setBusy(true);
    setError(null);
    try {
      const result = await client.confirmActionByVoice(
        snapshot.pending_action.id,
        audio,
        snapshot.session.version,
        idempotencyKey(),
        options,
      );
      // Speech not addressed to LabJarvis is ignored silently: nothing changed.
      if (result.decision === "not_addressed") return successfulAction;
      if (result.decision === "control" && result.control) {
        onVoiceControl?.(result.control, result.response_text);
        if (result.control === "ACKNOWLEDGE_ALARM") await refreshSnapshot();
        return successfulAction;
      }
      setMessage(result.response_text);
      const restored = await refreshSnapshot();
      if (restored?.session.state === "COMPLETED" || restored?.session.state === "ABORTED") {
        navigate({ name: "notebook", sessionId });
      }
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, navigate, onVoiceControl, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  const askProtocolQuestion = useCallback(
    async (question: string): Promise<ActionResult<GroundedAnswer>> => {
      if (snapshot === null) return failedAction("STALE_VIEW");
      setBusy(true);
      setError(null);
      try {
        const result = await client.invokeTool(
          snapshot.session.id,
          "answer_protocol_question",
          snapshot.session.version,
          { question },
          idempotencyKey(),
        );
        const abstained = result.data.abstained === "true";
        const pageText = result.data.source_page;
        const relevanceText = result.data.relevance;
        return successfulResult({
          text: abstained ? "" : result.message,
          abstained,
          citation: abstained ? null : (result.data.citation ?? null),
          page: pageText === undefined ? null : Number(pageText),
          stepId: result.data.source_step_id || null,
          relevance: relevanceText === undefined ? null : Number(relevanceText),
        });
      } catch (caught) {
        return handleActionError(caught);
      } finally {
        setBusy(false);
      }
    },
    [client, handleActionError, setBusy, setError, snapshot],
  );

  const confirmAction = useCallback(async (accepted: boolean): Promise<ActionResult> => {
    if (snapshot?.pending_action == null) return failedAction("STALE_VIEW");
    const sessionId = snapshot.session.id;
    setBusy(true);
    setError(null);
    try {
      await client.confirmAction(snapshot.pending_action.id, accepted);
      const restored = await refreshSnapshot();
      setMessage(
        accepted ? "Confirmed and recorded." : "Proposal rejected. Nothing was recorded.",
      );
      if (restored?.session.state === "COMPLETED" || restored?.session.state === "ABORTED") {
        navigate({ name: "notebook", sessionId });
      }
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, navigate, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  const correctEntry = useCallback(async (
    entryId: string,
    payload: Record<string, string>,
  ): Promise<ActionResult> => {
    setBusy(true);
    setError(null);
    try {
      await client.correctNotebookEntry(entryId, payload);
      await reloadNotebook();
      await refreshSnapshot();
      setMessage("Correction appended. The original entry remains intact.");
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, refreshSnapshot, reloadNotebook, setBusy, setError, setMessage]);

  return {
    askProtocolQuestion,
    confirmAction,
    confirmByVoice,
    correctEntry,
    invokeTool,
    startSession,
    submitAudioCommand,
    submitTextCommand,
  };
}
