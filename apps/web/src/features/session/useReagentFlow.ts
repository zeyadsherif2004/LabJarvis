import { useCallback, useEffect, useState } from "react";

import { failedAction, successfulAction, type ActionFailure, type ActionResult } from "../../actions";
import type { ClientPort } from "../../api/client";
import type {
  CaptureMetadata,
  LabelProbe,
  ReagentVerification,
  SessionSnapshot,
} from "../../types";

interface ReagentFlowOptions {
  client: ClientPort;
  snapshot: SessionSnapshot | null;
  refreshSnapshot: () => Promise<SessionSnapshot | null>;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  setMessage: (message: string | null) => void;
  handleActionError: (caught: unknown) => ActionFailure;
  /** Voice-only mode changes what the spoken result asks the operator to say. */
  voiceOnly?: boolean;
  /** Never read untrusted model text aloud if it contains the wake phrase. */
  wakePhrase?: string;
  /** Interface controls said while a reading waits ("stop", "stop the alarm"). */
  onVoiceControl?: (control: string, responseText: string) => void;
}

function idempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-reagent-${Date.now().toString(36)}`;
}

function safeSpokenLabel(text: string, wakePhrase: string): string | null {
  const label = text.trim();
  if (!/^[\p{L}\p{N} .-]{1,80}$/u.test(label)) return null;
  if (wakePhrase && label.toLocaleLowerCase().includes(wakePhrase.toLocaleLowerCase())) return null;
  return label;
}

export function useReagentFlow(options: ReagentFlowOptions) {
  const {
    client,
    snapshot,
    refreshSnapshot,
    setBusy,
    setError,
    setMessage,
    handleActionError,
    voiceOnly = false,
    wakePhrase = "",
    onVoiceControl,
  } = options;
  const [verification, setVerification] = useState<ReagentVerification | null>(null);

  useEffect(() => {
    if (snapshot === null) {
      setVerification(null);
      return;
    }
    setVerification((current) => {
      if (snapshot.pending_verification !== null) return snapshot.pending_verification;
      if (current === null) return null;
      return current.session_id === snapshot.session.id &&
        current.step_id === snapshot.session.active_step_id
        ? current
        : null;
    });
  }, [snapshot]);

  const verifyReagent = useCallback(async (
    image: Blob,
    requirementId: string,
  ): Promise<ActionResult> => {
    if (snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const result = await client.verifyReagent(
        snapshot.session.id,
        image,
        requirementId || null,
        snapshot.session.version,
        idempotencyKey(),
      );
      setVerification(result);
      setMessage(
        `Label comparison: ${result.outcome}. Review the reading and confirm or reject it.`,
      );
      await refreshSnapshot();
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, refreshSnapshot, setBusy, setError, setMessage, snapshot]);

  const verifyReagentFrames = useCallback(async (
    frames: Blob[],
    requirementId: string,
    capture?: CaptureMetadata,
  ): Promise<ActionResult> => {
    if (snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const result = await client.verifyReagentFrames(
        snapshot.session.id,
        frames,
        requirementId || null,
        snapshot.session.version,
        idempotencyKey(),
        capture,
      );
      setVerification(result);
      // The spoken proposal never says the wake phrase itself, so this
      // read-back can never be mistaken for the operator's own answer.
      const spoken: Record<string, string> = voiceOnly
        ? {
            MATCH: `Label check: match for ${result.expected_label}. To record it, say the wake word, then confirm ${result.expected_label}. Or say the wake word, then reject.`,
            MISMATCH: "Label check: mismatch. Do not use this bottle. Say the wake word, then reject, and check again.",
            UNCERTAIN: "Label check: uncertain. Say the wake word, then reject, and check again.",
          }
        : {
            MATCH: "Label check: match. The label agrees with the protocol. Review the reading on screen.",
            MISMATCH: "Label check: mismatch. Do not use this bottle. Review the reading on screen.",
            UNCERTAIN: "Label check: uncertain. Hold the label still and say check reagent again.",
          };
      if (voiceOnly && capture?.mode === "single") {
        const observed = safeSpokenLabel(result.observed_label, wakePhrase);
        const readback = observed === null
          ? `review the observed text on screen; expected ${result.expected_label}`
          : `I read "${observed}"; expected ${result.expected_label}`;
        const lead = result.outcome === "MATCH"
          ? `Label check: match; ${readback}.`
          : `Label check: ${result.outcome.toLowerCase()}; ${readback}; Do not use this bottle.`;
        const direction: Record<string, string> = {
          MATCH: `To record it, say the wake word, then confirm ${result.expected_label}. Or say the wake word, then reject.`,
          MISMATCH: "Say the wake word, then reject and check again.",
          UNCERTAIN: "Say the wake word, then reject and check again.",
        };
        setMessage(`${lead} ${direction[result.outcome]}`);
      } else {
        setMessage(spoken[result.outcome] ?? result.outcome);
      }
      await refreshSnapshot();
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, refreshSnapshot, setBusy, setError, setMessage, snapshot, voiceOnly, wakePhrase]);

  /** Live scan: one unrecorded probe of a frame; `null` when it failed. */
  const probeLabel = useCallback(async (
    frame: Blob,
    requirementId: string,
  ): Promise<LabelProbe | null> => {
    if (snapshot === null) return null;
    try {
      return await client.probeReagentLabel(snapshot.session.id, frame, requirementId || null);
    } catch {
      return null;
    }
  }, [client, snapshot]);

  const confirmReagentVerification = useCallback(async (
    accepted: boolean,
    correctedObservedLabel: string | null,
  ): Promise<ActionResult> => {
    if (verification === null || snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const resolved = await client.confirmReagentVerification(
        verification.id,
        accepted,
        correctedObservedLabel,
        snapshot.session.version,
        idempotencyKey(),
      );
      setVerification(resolved);
      setMessage(
        accepted
          ? `Label review recorded as ${resolved.decision}: ${resolved.outcome}.`
          : "Label proposal rejected. Check the bottle manually before proceeding.",
      );
      await refreshSnapshot();
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, refreshSnapshot, setBusy, setError, setMessage, snapshot, verification]);

  const decideReagentByVoice = useCallback(async (audio: Blob): Promise<ActionResult> => {
    if (verification === null || snapshot === null) return failedAction("STALE_VIEW");
    setBusy(true);
    setError(null);
    try {
      const result = await client.decideReagentVerificationByVoice(
        verification.id,
        audio,
        snapshot.session.version,
        idempotencyKey(),
      );
      if (result.decision === "not_addressed") return successfulAction;
      if (result.decision === "control" && result.control) {
        onVoiceControl?.(result.control, result.response_text);
        if (result.control === "ACKNOWLEDGE_ALARM") await refreshSnapshot();
        return successfulAction;
      }
      setVerification(result.verification);
      setMessage(result.response_text);
      await refreshSnapshot();
      return successfulAction;
    } catch (caught) {
      return handleActionError(caught);
    } finally {
      setBusy(false);
    }
  }, [client, handleActionError, onVoiceControl, refreshSnapshot, setBusy, setError, setMessage, snapshot, verification]);

  return {
    confirmReagentVerification,
    decideReagentByVoice,
    probeLabel,
    verification,
    verifyReagent,
    verifyReagentFrames,
  };
}
