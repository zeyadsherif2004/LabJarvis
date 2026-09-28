import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  MicrophoneIsolationFailed,
  ReadbackAbandoned,
  RecordingRefused,
  WebRtcRealtimeSession,
  openConfirmationCapture,
  type PlaybackMonitor,
  type PlaybackMonitorState,
  type RealtimeActivity,
  type RealtimeCallbacks,
  type RealtimeConnectionStatus,
} from "./realtimeSession";

/**
 * Transport behaviour, driven by the provider events the browser would receive.
 *
 * The turn boundary is the important part. `say()` must resolve only when the
 * response *it asked for* has completed **successfully** and that response's
 * audio has stopped, because the caller uses the promise to decide when it is
 * safe to record the operator's confirmation.
 *
 * Three separate ways of getting that wrong are covered here, each of which was
 * present in the implementation the audit examined:
 *
 * - binding to whatever `response.created` arrived next, so an automatic VAD
 *   turn or a tool-call follow-up could be adopted as the readback;
 * - treating `response.done` as success, when it is emitted for every terminal
 *   state including `cancelled`, `failed`, and `incomplete`;
 * - treating "no level meter" as "silence", so a browser that could not measure
 *   anything looked exactly like a quiet speaker.
 */

interface Recorded {
  statuses: RealtimeConnectionStatus[];
  activities: RealtimeActivity[];
  sent: Record<string, unknown>[];
  errors: string[];
  toolCalls: { name: string; parameters: Record<string, string> }[];
}

class StubMonitor implements PlaybackMonitor {
  public audible = false;
  public state: PlaybackMonitorState = "running";
  public prepare(): Promise<void> {
    return Promise.resolve();
  }
  public attach(): void {
    return undefined;
  }
  public isAudible(): boolean {
    return this.audible;
  }
  public close(): void {
    this.state = "closed";
  }
}

function build(
  toolResult: Record<string, unknown> = { committed: false },
  timings: { settleMs?: number; timeoutMs?: number } = { settleMs: 0 },
) {
  const recorded: Recorded = {
    statuses: [],
    activities: [],
    sent: [],
    errors: [],
    toolCalls: [],
  };
  const callbacks: RealtimeCallbacks = {
    onStatusChange: (status) => recorded.statuses.push(status),
    onActivity: (activity) => recorded.activities.push(activity),
    onToolCall: (call) => {
      recorded.toolCalls.push({ name: call.name, parameters: call.parameters });
      return Promise.resolve(toolResult);
    },
    onTranscript: () => undefined,
    onError: (message) => recorded.errors.push(message),
  };
  const monitor = new StubMonitor();
  const session = new WebRtcRealtimeSession(callbacks, monitor, timings);
  const audio = { pause: vi.fn(), srcObject: {} as MediaStream | null };
  const track = { stop: vi.fn(), enabled: true } as unknown as MediaStreamTrack;
  const sender = { replaceTrack: vi.fn(() => Promise.resolve()) };
  // The transport is exercised through the events it would receive over the
  // data channel; the peer connection itself is not the thing under test.
  Object.assign(session as unknown as Record<string, unknown>, {
    channel: {
      readyState: "open",
      send: (payload: string) =>
        recorded.sent.push(JSON.parse(payload) as Record<string, unknown>),
      close: vi.fn(),
    },
    audio,
    sender,
    stream: { getAudioTracks: () => [track], getTracks: () => [track] },
  });
  const deliver = async (event: Record<string, unknown>) => {
    await (
      session as unknown as { handleEvent: (raw: string) => Promise<void> }
    ).handleEvent(JSON.stringify(event));
  };

  /**
   * The correlation identifier the session put on its most recent
   * `response.create`. The provider echoes this back, and it is the only thing
   * that ties a response to the readback that asked for it.
   */
  const readbackId = (): string => {
    const request = [...recorded.sent]
      .reverse()
      .find((event) => event.type === "response.create");
    const response = request?.response as
      | { metadata?: Record<string, unknown> }
      | undefined;
    const value = response?.metadata?.labjarvis_readback_id;
    return typeof value === "string" ? value : "";
  };

  /** `response.created` for our readback, bound to `responseId`. */
  const created = (responseId: string) =>
    deliver({
      type: "response.created",
      response: {
        id: responseId,
        status: "in_progress",
        metadata: { labjarvis_readback_id: readbackId() },
      },
    });

  /** `response.done` for our readback, in whichever terminal state. */
  const done = (responseId: string, status: string | null = "completed") =>
    deliver({
      type: "response.done",
      response: {
        id: responseId,
        ...(status === null ? {} : { status }),
        metadata: { labjarvis_readback_id: readbackId() },
      },
    });

  return { session, recorded, deliver, audio, monitor, sender, readbackId, created, done };
}

/** Let the settle poll run. */
function tick(milliseconds = 80): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/**
 * Observe a promise for `window` milliseconds.
 *
 * The window is adjustable because some tests configure a very short readback
 * timeout; watching such a promise for longer than that would report the
 * timeout rather than the thing under test.
 */
function settled(
  promise: Promise<void>,
  window = 100,
): Promise<"resolved" | "pending" | "rejected"> {
  return Promise.race([
    promise.then(
      () => "resolved" as const,
      () => "rejected" as const,
    ),
    tick(window).then(() => "pending" as const),
  ]);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("realtime transport: the readback turn boundary", () => {
  it("does not resolve until the correct response has completed", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });

    expect(await settled(speaking)).toBe("pending");

    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1");

    expect(await settled(speaking)).toBe("resolved");
  });

  it("puts a correlation identifier on the request it expects back", () => {
    const { session, recorded, readbackId } = build();

    void session.say("Say: confirm 10 mL").catch(() => undefined);

    const request = recorded.sent[0] as {
      type: string;
      response: { metadata: Record<string, string>; instructions: string };
    };
    expect(request.type).toBe("response.create");
    expect(request.response.metadata.labjarvis_readback_id).toBe(readbackId());
    expect(readbackId()).not.toBe("");
    session.stop();
  });

  // -------------------------------------------------- response status gating

  it("resolves only for status completed", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1", "completed");

    expect(await settled(speaking)).toBe("resolved");
  });

  it.each([
    ["cancelled", "cancelled"],
    ["failed", "failed"],
    ["incomplete", "incomplete"],
  ])("rejects a %s response rather than treating done as success", async (status, reason) => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1", status);

    await expect(speaking).rejects.toMatchObject({ reason });
  });

  it("rejects a response.done that carries no status at all", async () => {
    const { session, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1", null);

    // Missing is not benign. Assuming success here is exactly the failure mode.
    await expect(speaking).rejects.toMatchObject({ reason: "unknown_status" });
  });

  it("rejects a status this build does not recognise", async () => {
    const { session, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1", "some_future_state");

    await expect(speaking).rejects.toMatchObject({ reason: "unknown_status" });
  });

  it("does not leak provider internals into the failure message", async () => {
    const { session, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1", "failed");

    const caught = await speaking.catch((error: ReadbackAbandoned) => error);
    expect(caught).toBeInstanceOf(ReadbackAbandoned);
    expect((caught as ReadbackAbandoned).message).not.toContain("resp-1");
  });

  // ------------------------------------------------------------ correlation

  it("ignores a response.done belonging to a different response", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });

    // A tool-call follow-up finishing. It is not the readback.
    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await deliver({
      type: "response.done",
      response: { id: "resp-other", status: "completed" },
    });

    expect(await settled(speaking)).toBe("pending");

    await done("resp-1");
    expect(await settled(speaking)).toBe("resolved");
  });

  it("does not bind to an unrelated response.created that arrives first", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    // Default VAD opens its own turn before ours. Binding to it - which the
    // previous "next response.created" rule did - would mean the readback
    // completed as soon as *that* response finished.
    await deliver({
      type: "response.created",
      response: { id: "resp-vad", status: "in_progress" },
    });
    await deliver({
      type: "response.done",
      response: { id: "resp-vad", status: "completed" },
    });

    expect(await settled(speaking)).toBe("pending");

    await created("resp-1");
    await done("resp-1");
    expect(await settled(speaking)).toBe("resolved");
  });

  it("is not satisfied by an unrelated response that succeeds while ours is cancelled", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({
      type: "response.done",
      response: { id: "resp-other", status: "completed" },
    });
    await done("resp-1", "cancelled");

    await expect(speaking).rejects.toMatchObject({ reason: "cancelled" });
  });

  it("rejects when two responses claim the same readback", async () => {
    const { session, created } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await created("resp-2");

    // Neither can be trusted to be the one actually being spoken.
    await expect(speaking).rejects.toBeInstanceOf(ReadbackAbandoned);
  });

  it("tolerates a duplicate response.created for the same response", async () => {
    const { session, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await created("resp-1");
    await done("resp-1");

    expect(await settled(speaking)).toBe("resolved");
  });

  it("tolerates a duplicate response.done", async () => {
    const { session, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1");
    await done("resp-1");

    expect(await settled(speaking)).toBe("resolved");
  });

  it("cleans up every mapping when a readback times out", async () => {
    const { session, created, done, recorded } = build(
      { committed: false },
      { timeoutMs: 40, settleMs: 0 },
    );

    const speaking = session.say("Say: confirm 10 mL");
    const id = recorded.sent.length;
    await created("resp-1");
    await expect(speaking).rejects.toMatchObject({ reason: "timeout" });

    // Late events for the abandoned response must not satisfy anything, and
    // must not satisfy a *later* request either.
    await done("resp-1");
    expect(id).toBeGreaterThan(0);
    expect(session.assistantOutputActive).toBe(false);
  });

  it("does not let a late event from a timed-out readback satisfy the next one", async () => {
    const { session, created, done } = build(
      { committed: false },
      { timeoutMs: 40, settleMs: 0 },
    );

    const first = session.say("first");
    const firstId = (
      session as unknown as { waiters: Map<string, unknown> }
    ).waiters.keys().next().value as string;
    await created("resp-1");
    await expect(first).rejects.toMatchObject({ reason: "timeout" });

    const second = session.say("second");
    // A stale `response.done` naming the *first* correlation id.
    await (
      session as unknown as { handleEvent: (raw: string) => Promise<void> }
    ).handleEvent(
      JSON.stringify({
        type: "response.done",
        response: {
          id: "resp-1",
          status: "completed",
          metadata: { labjarvis_readback_id: firstId },
        },
      }),
    );

    // Watched briefly: this session's readback timeout is 40 ms, and the point
    // is that the stale event did not satisfy it, not that it never times out.
    expect(await settled(second, 10)).toBe("pending");
    await created("resp-2");
    await done("resp-2");
    expect(await settled(second, 10)).toBe("resolved");
  });

  it("refuses a second readback while one is in flight", async () => {
    const { session } = build();

    const first = session.say("first");
    const second = session.say("second");

    // Explicit readbacks are serialised: two audible at once would make the
    // quiet gate meaningless.
    await expect(second).rejects.toMatchObject({ reason: "busy" });
    session.stop();
    await expect(first).rejects.toBeInstanceOf(ReadbackAbandoned);
  });

  // ------------------------------------------------------- audio lifecycle

  it("does not resolve when audio stops before the right response completes", async () => {
    const { session, deliver, created } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });
    // Silence alone proves nothing: generation may still be running.
    await deliver({ type: "output_audio_buffer.stopped", response_id: "resp-1" });

    expect(await settled(speaking)).toBe("pending");
  });

  it("does not let another response's audio-done mark ours quiet", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });
    await done("resp-1");

    // Response B finishing its audio says nothing about response A, which is
    // still playing. A single global flag got this wrong.
    await deliver({ type: "response.output_audio.done", response_id: "resp-other" });
    expect(await settled(speaking)).toBe("pending");

    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    expect(await settled(speaking)).toBe("resolved");
  });

  it("treats an undocumented buffer event as corroboration only", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });
    await done("resp-1");

    // `output_audio_buffer.stopped` is WebRTC-only and undocumented. Without an
    // id it cannot clear a response we are tracking.
    await deliver({ type: "output_audio_buffer.stopped" });
    expect(await settled(speaking)).toBe("pending");

    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    expect(await settled(speaking)).toBe("resolved");
  });

  // --------------------------------------------- playback must be verifiable

  it("does not resolve while a level meter still hears the assistant", async () => {
    const { session, deliver, monitor, created, done } = build();
    monitor.audible = true;

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1");

    // The provider says it has finished; the speaker is still making sound.
    expect(await settled(speaking)).toBe("pending");

    monitor.audible = false;
    expect(await settled(speaking)).toBe("resolved");
  });

  it("waits out the quiet settle window after completion", async () => {
    const { session, created, done } = build({ committed: false }, { settleMs: 400 });

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1");

    // A debounce *after* confirmed completion, not instead of it.
    expect(await settled(speaking)).toBe("pending");
    await tick(450);
    expect(await settled(speaking)).toBe("resolved");
  });

  it("restarts the settle window if audio returns during it", async () => {
    const { session, deliver, monitor, created, done } = build(
      { committed: false },
      { settleMs: 200 },
    );

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1");
    await tick(100);

    // Buffered playback lasting longer than the window: the speaker starts
    // making sound again before it elapses.
    monitor.audible = true;
    await tick(150);
    expect(await settled(speaking)).toBe("pending");

    monitor.audible = false;
    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await tick(250);
    expect(await settled(speaking)).toBe("resolved");
  });

  it.each<PlaybackMonitorState>(["unavailable", "failed", "suspended", "closed"])(
    "refuses to speak at all when the playback monitor is %s",
    async (state) => {
      const { session, monitor, recorded } = build();
      monitor.state = state;

      const speaking = session.say("Say: confirm 10 mL");

      // Fail closed: without a working meter there is no way to establish that
      // the speaker went quiet, so the readback is never even requested.
      await expect(speaking).rejects.toMatchObject({ reason: "playback_unverifiable" });
      expect(recorded.sent).toHaveLength(0);
    },
  );

  it("rejects when the monitor stops working while a readback is in flight", async () => {
    const { session, monitor, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    monitor.state = "suspended";
    await done("resp-1");

    await expect(speaking).rejects.toMatchObject({ reason: "playback_unverifiable" });
  });

  it("rejects when the monitor closes while waiting out the settle window", async () => {
    const { session, monitor, created, done } = build({ committed: false }, { settleMs: 300 });

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await done("resp-1");
    monitor.close();

    await expect(speaking).rejects.toMatchObject({ reason: "playback_unverifiable" });
  });

  // -------------------------------------------------------- failing closed

  it("rejects on a provider error rather than leaving the caller waiting", async () => {
    const { session, deliver, created } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "error" });

    await expect(speaking).rejects.toMatchObject({ reason: "provider_error" });
  });

  it("rejects when the session stops mid-sentence", async () => {
    const { session } = build();

    const speaking = session.say("Say: confirm 10 mL");
    session.stop();

    await expect(speaking).rejects.toMatchObject({ reason: "closed" });
  });

  it("rejects a stuck readback rather than assuming it finished", async () => {
    const { session, created } = build({ committed: false }, { timeoutMs: 60, settleMs: 0 });

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    // The provider never sends response.done.

    await expect(speaking).rejects.toMatchObject({ reason: "timeout" });
  });

  it("refuses to speak at all when the channel is not open", async () => {
    const { session } = build();
    Object.assign(session as unknown as Record<string, unknown>, {
      channel: { readyState: "closed", send: vi.fn() },
    });

    await expect(session.say("anything")).rejects.toBeInstanceOf(ReadbackAbandoned);
  });

  // ------------------------------------------------------- recording guard

  it("refuses to record while the assistant may still be audible", async () => {
    const { session, deliver, created, done } = build();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });

    // This is the invariant the confirmation design rests on.
    await expect(session.captureUtterance(1000)).rejects.toBeInstanceOf(RecordingRefused);
    expect(session.assistantOutputActive).toBe(true);

    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1");
    await speaking;
    expect(session.assistantOutputActive).toBe(false);
  });

  it("refuses to record while a readback is merely pending", async () => {
    const { session } = build();

    void session.say("Say: confirm 10 mL").catch(() => undefined);

    // The sentence has been requested and may begin at any moment.
    await expect(session.captureUtterance(1000)).rejects.toBeInstanceOf(RecordingRefused);
    session.stop();
  });

  // ---------------------------------------------------- microphone isolation

  it("detaches the outgoing track and waits for the browser to confirm it", async () => {
    const { session, sender } = build();
    let resolveReplace: (() => void) | null = null;
    sender.replaceTrack = vi.fn(
      () => new Promise<void>((resolve) => (resolveReplace = () => resolve())),
    );

    const suppressing = session.suppressOutgoingAudio();
    expect(sender.replaceTrack).toHaveBeenCalledWith(null);
    // Not suppressed until the browser says so; the promise used to be
    // discarded with `void`, which made a slow or failed detach invisible.
    expect(session.outgoingAudioSuppressed).toBe(false);

    resolveReplace!();
    await suppressing;
    expect(session.outgoingAudioSuppressed).toBe(true);
  });

  it("reports a failed detach instead of continuing as if it worked", async () => {
    const { session, sender } = build();
    sender.replaceTrack = vi.fn(() => Promise.reject(new Error("sender is closed")));

    await expect(session.suppressOutgoingAudio()).rejects.toBeInstanceOf(
      MicrophoneIsolationFailed,
    );
    expect(session.outgoingAudioSuppressed).toBe(false);
  });

  it("reports a missing sender as an isolation failure", async () => {
    const { session } = build();
    Object.assign(session as unknown as Record<string, unknown>, { sender: null });

    await expect(session.suppressOutgoingAudio()).rejects.toBeInstanceOf(
      MicrophoneIsolationFailed,
    );
  });

  it("puts the same live track back, because the recording comes from it", async () => {
    const { session, sender } = build();

    await session.suppressOutgoingAudio();
    await session.resumeOutgoingAudio();

    expect(sender.replaceTrack).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: true }),
    );
    expect(session.outgoingAudioSuppressed).toBe(false);
  });

  it("reports a failed restore rather than claiming the microphone is back", async () => {
    const { session, sender } = build();
    await session.suppressOutgoingAudio();
    sender.replaceTrack = vi.fn(() => Promise.reject(new Error("sender is closed")));

    await expect(session.resumeOutgoingAudio()).rejects.toBeInstanceOf(
      MicrophoneIsolationFailed,
    );
    expect(session.outgoingAudioSuppressed).toBe(true);
  });

  it("treats a connection replaced during suppression as nothing left to restore", async () => {
    const { session } = build();
    await session.suppressOutgoingAudio();

    // stop() tears the sender and stream down; restoring must not throw.
    session.stop();
    await expect(session.resumeOutgoingAudio()).resolves.toBeUndefined();
    expect(session.outgoingAudioSuppressed).toBe(false);
  });

  it("survives stopping while a replaceTrack is still outstanding", async () => {
    const { session, sender } = build();
    let resolveReplace: (() => void) | null = null;
    sender.replaceTrack = vi.fn(
      () => new Promise<void>((resolve) => (resolveReplace = () => resolve())),
    );

    const suppressing = session.suppressOutgoingAudio();
    session.stop();
    resolveReplace!();

    await expect(suppressing).resolves.toBeUndefined();
  });

  it("releases the capture transaction exactly once, on every path", async () => {
    const { session, sender } = build();

    const capture = await openConfirmationCapture(session);
    expect(session.outgoingAudioSuppressed).toBe(true);

    expect(await capture.release()).toBeNull();
    expect(await capture.release()).toBeNull();
    // One detach, one reattach - not two of either.
    expect(sender.replaceTrack).toHaveBeenCalledTimes(2);
  });

  it("hands back the isolation failure rather than throwing out of release", async () => {
    const { session, sender } = build();
    const capture = await openConfirmationCapture(session);
    sender.replaceTrack = vi.fn(() => Promise.reject(new Error("sender is closed")));

    const failure = await capture.release();

    // A recording taken under correct isolation may already exist; throwing
    // here would discard valid evidence over a restore problem.
    expect(failure).toBeInstanceOf(MicrophoneIsolationFailed);
  });

  it("refuses to open a capture at all when the track cannot be detached", async () => {
    const { session, sender } = build();
    sender.replaceTrack = vi.fn(() => Promise.reject(new Error("sender is closed")));

    await expect(openConfirmationCapture(session)).rejects.toBeInstanceOf(
      MicrophoneIsolationFailed,
    );
  });

  // ------------------------------------------------- interruption semantics

  it("does not claim barge-in detection while provider input is suppressed", async () => {
    const { session, deliver, created, done } = build();
    await session.suppressOutgoingAudio();

    const speaking = session.say("Say: confirm 10 mL");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });

    // The provider is receiving no audio, so this event cannot be a real
    // detection of the operator talking. Acting on it would be claiming a
    // capability the design does not have. The readback is non-interruptible
    // for its protected window; nothing is recorded either way.
    await deliver({ type: "input_audio_buffer.speech_started" });
    expect(await settled(speaking)).toBe("pending");

    await deliver({ type: "response.output_audio.done", response_id: "resp-1" });
    await done("resp-1");
    expect(await settled(speaking)).toBe("resolved");
  });

  it("still interrupts ordinary conversation when input is attached", async () => {
    const { session, deliver, created } = build();

    const speaking = session.say("some ordinary sentence");
    await created("resp-1");
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });
    await deliver({ type: "input_audio_buffer.speech_started" });

    await expect(speaking).rejects.toMatchObject({ reason: "interrupted" });
  });

  it("cuts off the assistant when the operator starts talking over it", async () => {
    const { recorded, deliver, audio } = build();

    await deliver({ type: "output_audio_buffer.started", response_id: "resp-1" });
    expect(recorded.activities.at(-1)).toBe("speaking");

    await deliver({ type: "input_audio_buffer.speech_started" });

    const types = recorded.sent.map((event) => event.type);
    expect(types).toContain("response.cancel");
    expect(types).toContain("output_audio_buffer.clear");
    expect(audio.pause).toHaveBeenCalled();
    expect(audio.srcObject).toBeNull();
    expect(recorded.activities.at(-1)).toBe("listening");
  });

  // ------------------------------------------------------------- behaviour

  it("moves through processing and back to listening on provider events", async () => {
    const { recorded, deliver } = build();

    await deliver({ type: "input_audio_buffer.speech_stopped" });
    await deliver({
      type: "response.created",
      response: { id: "resp-1", status: "in_progress" },
    });
    await deliver({ type: "response.output_audio.delta", response_id: "resp-1" });
    await deliver({
      type: "response.done",
      response: { id: "resp-1", status: "completed" },
    });

    expect(recorded.activities).toEqual([
      "processing",
      "processing",
      "speaking",
      "listening",
    ]);
  });

  it("returns the backend result to the model verbatim", async () => {
    const backendResult = {
      committed: false,
      awaiting_confirmation: true,
      message: "Proposed record measurement. Confirm to apply it.",
    };
    const { recorded, deliver } = build(backendResult);

    await deliver({
      type: "response.function_call_arguments.done",
      call_id: "call-1",
      name: "record_measurement",
      arguments: JSON.stringify({ kind: "volume", value: "10", unit: "mL" }),
    });

    expect(recorded.toolCalls).toEqual([
      { name: "record_measurement", parameters: { kind: "volume", value: "10", unit: "mL" } },
    ]);
    const output = recorded.sent.find(
      (event) => event.type === "conversation.item.create",
    ) as { item: { output: string } };
    // The model is handed the database's answer, not an opportunity to write
    // its own account of what happened.
    expect(JSON.parse(output.item.output)).toEqual(backendResult);
  });

  it("still submits a malformed tool call so the backend can reject it", async () => {
    const { recorded, deliver } = build();

    await deliver({
      type: "response.function_call_arguments.done",
      call_id: "call-2",
      name: "record_measurement",
      arguments: "{not json",
    });

    expect(recorded.toolCalls).toEqual([{ name: "record_measurement", parameters: {} }]);
  });

  it("asks the model to voice an exact sentence", () => {
    const { session, recorded } = build();

    void session.say("Recorded 10 mL for volume.").catch(() => undefined);

    const request = recorded.sent[0] as {
      type: string;
      response: { instructions: string };
    };
    expect(request.type).toBe("response.create");
    expect(request.response.instructions).toContain("word for word");
    expect(request.response.instructions).toContain("Recorded 10 mL for volume.");
    session.stop();
  });

  it("announces an outcome without opening a waiter", async () => {
    const { session, recorded } = build();

    await session.announce("Recorded 10 mL for volume.");

    const request = recorded.sent[0] as {
      type: string;
      response: { instructions: string; metadata?: unknown };
    };
    expect(request.type).toBe("response.create");
    expect(request.response.instructions).toContain("Recorded 10 mL for volume.");
    // No correlation metadata, because nothing is waiting on it, and nothing
    // blocks recording afterwards on its account.
    expect(request.response.metadata).toBeUndefined();
    expect(session.assistantOutputActive).toBe(false);
  });

  it("announces nothing once the session has stopped", async () => {
    const { session, recorded } = build();
    session.stop();

    await expect(session.announce("anything")).resolves.toBeUndefined();

    expect(recorded.sent).toHaveLength(0);
  });

  it("surfaces a provider error rather than continuing silently", async () => {
    const { recorded, deliver } = build();

    await deliver({ type: "error" });

    expect(recorded.errors).toEqual(["The realtime provider reported an error."]);
  });

  it("ignores an unparsable event instead of throwing", async () => {
    const { session, recorded } = build();

    await (
      session as unknown as { handleEvent: (raw: string) => Promise<void> }
    ).handleEvent("not json at all");

    expect(recorded.errors).toEqual([]);
    expect(recorded.activities).toEqual([]);
  });
});
