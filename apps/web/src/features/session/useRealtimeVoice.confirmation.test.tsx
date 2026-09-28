import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fakeClient } from "../../test/fixtures";
import type { PendingAction, SpokenConfirmation } from "../../types";
import { blobText, scriptedRealtime, type HarnessOptions } from "./realtimeTestHarness";
import { useRealtimeVoice } from "./useRealtimeVoice";

/**
 * The confirmation turn boundary, and the isolation around it.
 *
 * Two defects are covered here, found in that order.
 *
 * **1. Recording began before the readback finished.** `say()` used to be
 * fire-and-forget, so the recorder opened while the assistant was still reading
 * the proposal back. The readback contains the exact phrase the backend accepts
 * - "Say: confirm 10 mL" - so on a laptop the assistant's own voice could
 * travel speaker-to-microphone into the confirmation clip, both transcription
 * readers would agree, and a measurement would commit that no person confirmed.
 *
 * **2. The provider was listening while the recorder was open.** Microphone
 * forwarding was restored the instant the readback finished, before capture.
 * Default provider VAD can hear the operator finish and answer on its own, so
 * the assistant could start speaking into the evidence recording. The
 * `provider isolation during confirmation capture` suite below covers this.
 *
 * The harness models the room *and* the provider: `captureUtterance` opens a
 * recorder for a real interval, whatever is audible during it lands in the Blob,
 * and the provider can only answer when the sender is attached. A test that
 * captures too early, or that leaves the provider listening, therefore gets the
 * echo, and says so.
 */

const measurement: PendingAction = {
  id: "action-1",
  session_id: "session-1",
  intent: "RECORD_MEASUREMENT",
  payload: { kind: "volume", value: "10", unit: "mL" },
  source: "MODEL_PROPOSAL",
  status: "PENDING",
  expected_session_version: 3,
  created_at: "2026-07-16T12:07:00Z",
  correlation_id: "corr-1",
};

const secondAction: PendingAction = {
  ...measurement,
  id: "action-2",
  payload: { kind: "volume", value: "25", unit: "mL" },
};

const grant = {
  client_secret: "ek_not_a_real_credential",
  expires_at: "2026-07-16T12:10:00Z",
  model_name: "fake-realtime-v1",
  voice: "alloy",
  transport: "webrtc" as const,
  provider: "deterministic-fake-realtime",
  provider_session_id: "fake-realtime-session",
  tool_names: [],
  max_utterance_seconds: 30,
  idle_timeout_seconds: 120,
  snapshot: {} as never,
};

function spoken(overrides: Partial<SpokenConfirmation> = {}): SpokenConfirmation {
  return {
    decision: "confirm",
    committed: true,
    session: {} as never,
    pending_action: { ...measurement, status: "ACCEPTED" },
    entry: null,
    response_text: "Recorded 10 mL for volume.",
    utterance_id: "utterance-1",
    heard: "confirm 10 mL",
    reasons: [],
    replayed: false,
    ...overrides,
  };
}

interface SetupOptions extends HarnessOptions {
  pending?: PendingAction | null;
  version?: number;
  confirm?: ReturnType<typeof vi.fn>;
  maxConfirmationAttempts?: number;
}

/** Reconnect backoff is real time in production and pointless delay in a test. */
const noDelay = () => Promise.resolve();

function setup(options: SetupOptions = {}) {
  const harness: HarnessOptions = {
    ...(options.echoText === undefined ? {} : { echoText: options.echoText }),
    ...(options.operatorText === undefined ? {} : { operatorText: options.operatorText }),
    ...(options.enforceCaptureGuard === undefined
      ? {}
      : { enforceCaptureGuard: options.enforceCaptureGuard }),
    ...(options.manualCapture === undefined ? {} : { manualCapture: options.manualCapture }),
    ...(options.failSuppression === undefined
      ? {}
      : { failSuppression: options.failSuppression }),
    ...(options.failRestoration === undefined
      ? {}
      : { failRestoration: options.failRestoration }),
    ...(options.manualSuppression === undefined
      ? {}
      : { manualSuppression: options.manualSuppression }),
  };
  const { factory, created, completeSuppression } = scriptedRealtime(harness);
  const confirmActionByVoice = options.confirm ?? vi.fn().mockResolvedValue(spoken());
  const client = fakeClient({
    confirmActionByVoice,
    createRealtimeSession: vi.fn().mockResolvedValue(grant),
    invokeTool: vi.fn().mockResolvedValue({
      committed: false,
      message: "Proposed record measurement.",
      pending_action: null,
      data: {},
    }),
  });

  const rendered = renderHook(
    (props: { pending: PendingAction | null; version: number; sessionId: string }) =>
      useRealtimeVoice({
        client,
        sessionId: props.sessionId,
        sessionVersion: props.version,
        pendingAction: props.pending,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
        delay: noDelay,
        ...(options.maxConfirmationAttempts === undefined
          ? {}
          : { maxConfirmationAttempts: options.maxConfirmationAttempts }),
      }),
    {
      initialProps: {
        pending: options.pending ?? null,
        version: options.version ?? 3,
        sessionId: "session-1",
      },
    },
  );

  return { ...rendered, created, confirmActionByVoice, client, completeSuppression };
}

async function connect(result: { current: { start: () => Promise<void> } }) {
  await act(async () => {
    await result.current.start();
  });
}

describe("confirmation turn boundary", () => {
  it("does not start recording when the readback is merely requested", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });

    // The sentence has been requested. Nothing has been recorded.
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("records nothing for as long as the assistant is still speaking", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => created[0]!.beginPlayback());
    // Give any incorrect implementation every chance to record.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
    expect(result.current.status).toBe("awaiting_confirmation");
  });

  it("records only after the readback has completed, and captures the operator", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.beginPlayback());
    expect(created[0]!.captureCount).toBe(0);

    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    expect(created[0]!.captureCount).toBe(1);

    // The decisive assertion: what reached the backend is the operator's reply,
    // not the assistant's readback echoing back off the room.
    const submitted = confirmActionByVoice.mock.calls[0] as [string, Blob, number, string];
    const captured = await blobText(submitted[1]);
    expect(captured).toBe("confirm 10 mL");
    expect(captured).not.toContain("Say:");
  });

  it("cannot capture the assistant's own readback, even with the transport guard off", async () => {
    /**
     * The defect, isolated.
     *
     * Two things independently prevent self-echo: the transport refuses to
     * record while the assistant may be audible, and the controller does not
     * ask until the readback has completed. Every other test here has both in
     * place, so a controller regression could hide behind the transport guard.
     *
     * With the guard removed, the room is recorded exactly as it sounds: the
     * assistant's words during playback, the operator's afterwards. If the
     * controller records too early, the clip submitted to the backend is the
     * readback - "Say: confirm 10 mL" - which contains the value, the unit, and
     * the confirmation word the backend accepts. That is the whole failure.
     */

    const { result, rerender, created, confirmActionByVoice } = setup({
      enforceCaptureGuard: false,
      maxConfirmationAttempts: 1,
    });
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.beginPlayback());

    // Plenty of opportunity for a fire-and-forget implementation to record.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();

    act(() => created[0]!.finishReadback());
    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());

    const submitted = confirmActionByVoice.mock.calls[0] as [string, Blob, number, string];
    const captured = await blobText(submitted[1]);
    expect(captured).toBe("confirm 10 mL");
    // The assistant's readback never became the operator's answer.
    expect(captured).not.toContain("Say:");
  });

  it("suppresses microphone forwarding for the whole confirmation transaction", async () => {
    /**
     * This test previously asserted that forwarding was restored as soon as the
     * readback finished, and passed - which is how the isolation defect
     * survived review. Restoring there leaves the provider listening while the
     * evidence recorder is open, so it could answer the operator by itself and
     * speak into the clip. Suppression now spans the whole transaction, and the
     * interval assertions live in the isolation suite below.
     */
    const { result, rerender, created, confirmActionByVoice } = setup({
      maxConfirmationAttempts: 1,
    });
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    // While the assistant speaks, the provider gets no input at all, so its own
    // voice cannot come back as an operator turn.
    expect(created[0]!.outgoingSuppressed).toBe(true);

    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    // Exactly one detach and one reattach, and the reattach is last.
    expect(created[0]!.suppressionLog).toEqual(["suppressed", "resumed"]);
    expect(created[0]!.forwardingAttachedDuringCapture).toBe(false);
  });

  it("plays a non-speech cue before listening", async () => {
    const { result, rerender, created } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(created[0]!.cueCount).toBe(1));
  });

  // ----------------------------------------------------------- failure paths

  it("records nothing when the readback fails", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => created[0]!.failReadback("provider_error"));

    await waitFor(() => expect(result.current.detail).toContain("Nothing was recorded"));
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
    // The on-screen control is the fallback, and the proposal stays open.
    expect(result.current.detail).toContain("Confirm on screen");
    expect(result.current.status).toBe("awaiting_confirmation");
  });

  it("records nothing when the readback never completes", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    // A stuck readback is a failure, never permission to start recording.
    act(() => created[0]!.failReadback("timeout"));

    await waitFor(() => expect(result.current.detail).toContain("Nothing was recorded"));
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("never turns an interrupted readback into a confirmation", async () => {
    /**
     * Defence in depth, not a reachable path.
     *
     * Microphone forwarding is detached for the whole protected window, so the
     * provider receives nothing and cannot raise `input_audio_buffer.speech_started`
     * against the readback - the transport suite asserts exactly that. This
     * covers the controller's handling if the transport reports an
     * interruption anyway. Speech over the readback is *not* claimed to be
     * detected; it is simply never submitted as a confirmation, because the
     * recording has not started yet.
     */
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.beginPlayback());

    act(() => created[0]!.bargeIn());

    await waitFor(() => expect(result.current.detail).toContain("discarded"));
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("takes no recording from audio that overlapped the readback", async () => {
    /**
     * The production-reachable version of the case above. The operator starts
     * talking while the assistant is mid-sentence; nothing is listening, so
     * nothing is captured and nothing commits. The proposal stays open and is
     * re-read, or confirmed on screen.
     */
    const { result, rerender, created, confirmActionByVoice } = setup({
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.beginPlayback());

    // Whatever the operator says here reaches neither the provider nor a
    // recorder: the sender is detached and capture has not begun.
    expect(created[0]!.outgoingSuppressed).toBe(true);
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
    expect(result.current.status).toBe("awaiting_confirmation");
  });

  it("keeps the proposal open and lets a second attempt succeed", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup({
      maxConfirmationAttempts: 3,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.bargeIn());
    await waitFor(() => expect(result.current.detail).toContain("discarded"));

    // A fresh cycle starts for the same, still-open proposal.
    await waitFor(() => expect(created[0]!.said.length).toBeGreaterThanOrEqual(2));
    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalledTimes(1));
    expect(created[0]!.captureCount).toBe(1);
  });

  // -------------------------------------------------------------- identity

  it("discards the attempt when the proposal changes during the readback", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    // Action A is superseded by action B while A is still being read out.
    act(() => {
      rerender({ pending: secondAction, version: 3, sessionId: "session-1" });
    });
    act(() => created[0]!.finishReadback());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    // Nothing was submitted for A. Whatever happens next belongs to B.
    for (const call of confirmActionByVoice.mock.calls) {
      expect(call[0]).not.toBe("action-1");
    }
  });

  it("discards the attempt when the routed session changes during the readback", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-2" });
    });
    act(() => created[0]!.finishReadback());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("discards the attempt when the connection is replaced during the readback", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    // The peer drops mid-sentence and the controller rebuilds it.
    act(() => created[0]!.callbacks.onStatusChange("interrupted"));
    await waitFor(() => expect(created.length).toBeGreaterThan(1));
    act(() => created[0]!.finishReadback());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    // A clip belonging to the old connection is never submitted.
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("cancels outstanding work when the component unmounts", async () => {
    const { result, rerender, created, unmount, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => unmount());

    expect(created[0]!.stopCount).toBeGreaterThan(0);
    await act(async () => {
      await Promise.resolve();
    });
    expect(confirmActionByVoice).not.toHaveBeenCalled();
  });

  it("submits the session version authority holds now, not the one at proposal time", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup();
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    // The session advances while the proposal is being read back.
    act(() => {
      rerender({ pending: measurement, version: 11, sessionId: "session-1" });
    });
    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    const submitted = confirmActionByVoice.mock.calls[0] as [string, Blob, number, string];
    expect(submitted[2]).toBe(11);
  });

  it("reports an ambiguous outcome without committing anything", async () => {
    const confirm = vi.fn().mockResolvedValue(
      spoken({
        decision: "ambiguous",
        committed: false,
        response_text: "I could not act on that. Say: confirm 10 mL.",
      }),
    );
    const { result, rerender, created } = setup({ confirm, maxConfirmationAttempts: 1 });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.finishReadback());

    await waitFor(() => expect(result.current.detail).toContain("confirm 10 mL"));
    expect(result.current.status).toBe("awaiting_confirmation");
  });
});

/**
 * Provider isolation across the whole recording interval.
 *
 * The second defect found in this subsystem. Microphone forwarding was restored
 * the moment the readback finished:
 *
 *     await connection.say(readback);
 *     connection.resumeOutgoingAudio();      // <- here
 *     await connection.captureUtterance();   // recorder open, provider listening
 *
 * With the sender reattached, default provider VAD hears the operator finish
 * speaking and generates a response by itself. The assistant then talks while
 * the evidence recorder is still open, and its words - which include the exact
 * phrase the backend accepts - land in the confirmation clip.
 *
 * Suppression therefore has to span the entire transaction: readback, quiet
 * verification, cue, and every millisecond the recorder is open.
 */
describe("provider isolation during confirmation capture", () => {
  it("keeps microphone forwarding detached for the whole recording interval", async () => {
    const { result, rerender, created } = setup({
      manualCapture: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    expect(created[0]!.outgoingSuppressed).toBe(true);

    act(() => created[0]!.finishReadback());
    await waitFor(() => expect(created[0]!.recorderOpen).toBe(true));

    // The decisive assertion: the recorder is open and the provider is still
    // receiving nothing at all.
    expect(created[0]!.outgoingSuppressed).toBe(true);
    expect(created[0]!.suppressionLog).toEqual(["suppressed"]);

    act(() => created[0]!.finishCapture());
    await waitFor(() => expect(created[0]!.outgoingSuppressed).toBe(false));
    expect(created[0]!.forwardingAttachedDuringCapture).toBe(false);
  });

  it("restores forwarding only after the recorder has closed", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup({
      manualCapture: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.finishReadback());
    await waitFor(() => expect(created[0]!.recorderOpen).toBe(true));
    expect(created[0]!.suppressionLog).toEqual(["suppressed"]);

    act(() => created[0]!.finishCapture());

    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    expect(created[0]!.suppressionLog).toEqual(["suppressed", "resumed"]);
  });

  it("gives the provider nothing to answer while the operator is recorded", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup({
      manualCapture: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.finishReadback());
    await waitFor(() => expect(created[0]!.recorderOpen).toBe(true));

    // Default VAD would answer the operator's "confirm 10 mL" right here.
    act(() => created[0]!.providerRespondsSpontaneously());

    expect(created[0]!.spontaneousResponsesBlocked).toBe(1);
    expect(created[0]!.assistantSpokeDuringCapture).toBe(false);

    act(() => created[0]!.finishCapture());
    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    const submitted = confirmActionByVoice.mock.calls[0] as [string, Blob, number, string];
    const captured = await blobText(submitted[1]);
    expect(captured).toBe("confirm 10 mL");
    expect(captured).not.toContain("Say:");
  });

  it("discards the clip if the assistant somehow starts speaking mid-recording", async () => {
    /**
     * Belt and braces. Suppression should make this unreachable, so the
     * isolation is deliberately broken to force the situation the guard exists
     * for. A clip spanning assistant audio is not evidence of anything.
     */
    const { result, rerender, created, confirmActionByVoice } = setup({
      manualCapture: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.finishReadback());
    await waitFor(() => expect(created[0]!.recorderOpen).toBe(true));

    act(() => {
      created[0]!.outgoingSuppressed = false;
      created[0]!.providerRespondsSpontaneously();
    });

    await waitFor(() => expect(result.current.detail).toContain("Nothing was recorded"));
    expect(confirmActionByVoice).not.toHaveBeenCalled();
    expect(result.current.detail).toContain("Confirm on screen");
  });

  it("records nothing when the microphone cannot be detached", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup({
      failSuppression: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });

    await waitFor(() => expect(result.current.detail).toContain("Nothing was recorded"));
    // The readback is never even requested: without isolation there is no safe
    // way to speak the phrase the backend accepts out loud.
    expect(created[0]!.said).toHaveLength(0);
    expect(created[0]!.captureCount).toBe(0);
    expect(confirmActionByVoice).not.toHaveBeenCalled();
    expect(result.current.detail).toContain("Confirm on screen");
  });

  it("does not speak the readback until suppression has taken effect", async () => {
    const { result, rerender, created, completeSuppression } = setup({
      manualSuppression: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);

    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // replaceTrack(null) has not resolved, so the sender may still be live.
    expect(created[0]!.outgoingSuppressed).toBe(false);
    expect(created[0]!.said).toHaveLength(0);

    act(() => completeSuppression());
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    expect(created[0]!.outgoingSuppressed).toBe(true);
  });

  it("still submits a safely captured clip when restoration fails, and says so", async () => {
    const { result, rerender, created, confirmActionByVoice } = setup({
      failRestoration: true,
      maxConfirmationAttempts: 1,
    });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));

    act(() => created[0]!.finishReadback());

    // The clip was taken under correct isolation, so it is valid evidence and
    // is not thrown away.
    await waitFor(() => expect(confirmActionByVoice).toHaveBeenCalled());
    // But the connection is degraded now, and must not be reported as healthy.
    await waitFor(() => expect(result.current.detail).toContain("microphone"));
    expect(result.current.status).toBe("error");
  });

  it("restores forwarding even when the readback fails", async () => {
    const { result, rerender, created } = setup({ maxConfirmationAttempts: 1 });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    expect(created[0]!.outgoingSuppressed).toBe(true);

    act(() => created[0]!.failReadback("provider_error"));

    await waitFor(() => expect(created[0]!.outgoingSuppressed).toBe(false));
    expect(created[0]!.suppressionLog).toEqual(["suppressed", "resumed"]);
  });

  it("speaks the outcome without opening another safety-critical readback", async () => {
    const { result, rerender, created } = setup({ maxConfirmationAttempts: 1 });
    await connect(result);
    act(() => {
      rerender({ pending: measurement, version: 3, sessionId: "session-1" });
    });
    await waitFor(() => expect(created[0]!.said).toHaveLength(1));
    act(() => created[0]!.finishReadback());

    // The outcome is read out best-effort. It is not a turn boundary, so it
    // goes through `announce()` and cannot leave a waiter dangling.
    await waitFor(() =>
      expect(created[0]!.announced).toContain("Recorded 10 mL for volume."),
    );
    expect(created[0]!.said).toHaveLength(1);
  });
});
