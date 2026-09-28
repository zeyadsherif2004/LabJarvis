import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiProblem } from "../../api/client";
import { fakeClient } from "../../test/fixtures";
import type { PendingAction, ProblemDetail } from "../../types";
import { scriptedRealtime } from "./realtimeTestHarness";
import { proposalReadback, useRealtimeVoice } from "./useRealtimeVoice";

/**
 * Realtime behaviour with the provider replaced by a controllable double.
 *
 * The point of these tests is that the safety properties are properties of
 * LabJarvis, not of the provider: nothing here reaches a network, and every
 * assertion is about what the backend was asked to do.
 */

/**
 * The lifecycle tests use the shared scripted transport, whose `say()` is
 * genuinely asynchronous. The confirmation cycle itself is covered in
 * `useRealtimeVoice.confirmation.test.tsx`.
 */
function harness() {
  const { factory, created } = scriptedRealtime();
  return { factory, created };
}

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

function problem(status: number, code: string): ApiProblem {
  const detail: ProblemDetail = {
    type: "about:blank",
    title: code,
    status,
    detail: "refused",
    code,
    correlation_id: "corr-1",
  };
  return new ApiProblem(detail);
}

describe("realtime voice controller", () => {
  it("submits tool calls against the current session version, not the opening one", async () => {
    const { factory, created } = harness();
    const invokeTool = vi.fn().mockResolvedValue({
      tool: "get_current_step",
      committed: false,
      message: "Read the current step.",
      pending_action: null,
      data: {},
      session: {},
      timers: [],
    });
    const client = fakeClient({
      invokeTool,
      createRealtimeSession: vi.fn().mockResolvedValue(grant),
    });

    const { result, rerender } = renderHook(
      (props: { version: number }) =>
        useRealtimeVoice({
          client,
          sessionId: "session-1",
          sessionVersion: props.version,
          pendingAction: null,
          onRefresh: () => Promise.resolve(),
          createSession: factory,
        }),
      { initialProps: { version: 3 } },
    );

    await act(async () => {
      await result.current.start();
    });

    // The session moves on - a timer settles, a step completes - while the same
    // connection stays open. The next tool call must use the new version.
    rerender({ version: 7 });
    await act(async () => {
      await created[0]!.callbacks.onToolCall({
        callId: "call-1",
        name: "get_current_step",
        parameters: {},
      });
    });

    expect(invokeTool).toHaveBeenCalledWith(
      "session-1",
      "get_current_step",
      7,
      {},
      "realtime-call-1",
    );
  });

  it("keys a redelivered tool call so it cannot be applied twice", async () => {
    const { factory, created } = harness();
    const invokeTool = vi.fn().mockResolvedValue({
      committed: false,
      message: "Proposed record measurement.",
      pending_action: null,
      data: {},
    });
    const client = fakeClient({
      invokeTool,
      createRealtimeSession: vi.fn().mockResolvedValue(grant),
    });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
      }),
    );
    await act(async () => {
      await result.current.start();
    });

    const call = { callId: "call-9", name: "complete_step", parameters: {} };
    await act(async () => {
      await created[0]!.callbacks.onToolCall(call);
      await created[0]!.callbacks.onToolCall(call);
    });

    const keys = invokeTool.mock.calls.map((args) => String(args[4]));
    expect(keys).toEqual(["realtime-call-9", "realtime-call-9"]);
  });

  it("rebuilds the session on a dropped connection and only then reports listening", async () => {
    const { factory, created } = harness();
    const createRealtimeSession = vi.fn().mockResolvedValue(grant);
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    const client = fakeClient({ createRealtimeSession });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh,
        createSession: factory,
        delay: () => Promise.resolve(),
      }),
    );
    await act(async () => {
      await result.current.start();
    });
    expect(createRealtimeSession).toHaveBeenCalledTimes(1);

    act(() => {
      created[0]!.callbacks.onStatusChange("interrupted");
    });

    await waitFor(() => expect(result.current.status).toBe("listening"));
    // A genuinely new grant and a genuinely new peer, after re-reading state.
    expect(createRealtimeSession).toHaveBeenCalledTimes(2);
    expect(created).toHaveLength(2);
    expect(created[0]!.stopCount).toBeGreaterThan(0);
    expect(onRefresh).toHaveBeenCalled();
  });

  it("stops claiming to reconnect once the attempts are exhausted", async () => {
    const createRealtimeSession = vi
      .fn()
      .mockResolvedValueOnce(grant)
      .mockRejectedValue(new Error("provider unavailable"));
    const { factory, created } = harness();
    const client = fakeClient({ createRealtimeSession });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
        delay: () => Promise.resolve(),
        maxReconnectAttempts: 2,
      }),
    );
    await act(async () => {
      await result.current.start();
    });
    act(() => {
      created[0]!.callbacks.onStatusChange("interrupted");
    });

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.detail).toContain("could not reconnect after 2 attempts");
    expect(result.current.detail).toContain("Push-to-talk");
    expect(createRealtimeSession).toHaveBeenCalledTimes(3);
  });

  it("drives status from provider events rather than assuming them", async () => {
    const { factory, created } = harness();
    const client = fakeClient({
      createRealtimeSession: vi.fn().mockResolvedValue(grant),
    });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
      }),
    );
    await act(async () => {
      await result.current.start();
    });

    act(() => created[0]!.callbacks.onActivity("processing"));
    expect(result.current.status).toBe("processing");
    act(() => created[0]!.callbacks.onActivity("speaking"));
    expect(result.current.status).toBe("speaking");
    act(() => created[0]!.callbacks.onActivity("listening"));
    expect(result.current.status).toBe("listening");
  });

  it("reports a provider error without claiming anything was recorded", async () => {
    const { factory, created } = harness();
    const client = fakeClient({
      createRealtimeSession: vi.fn().mockResolvedValue(grant),
    });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
      }),
    );
    await act(async () => {
      await result.current.start();
    });
    act(() => created[0]!.callbacks.onError("The realtime provider reported an error."));

    expect(result.current.status).toBe("error");
    expect(result.current.detail).toContain("provider reported an error");
  });

  it("reports an expired or refused grant as an error, not as connected", async () => {
    const { factory } = harness();
    const client = fakeClient({
      createRealtimeSession: vi.fn().mockRejectedValue(problem(401, "REALTIME_UNAVAILABLE")),
    });
    const { result } = renderHook(() =>
      useRealtimeVoice({
        client,
        sessionId: "session-1",
        sessionVersion: 1,
        pendingAction: null,
        onRefresh: () => Promise.resolve(),
        createSession: factory,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.status).toBe("error");
  });

  it("spells the readback out with the value and the exact phrase to say", () => {
    const text = proposalReadback(measurement);
    expect(text).toContain("10 mL");
    expect(text).toContain("Say: confirm 10 mL");
    expect(proposalReadback({ ...measurement, intent: "END_SESSION" })).toContain(
      "cannot be undone",
    );
  });
});
