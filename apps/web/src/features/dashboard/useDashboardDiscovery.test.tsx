import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProtocolSummary, SessionSummary } from "../../types";
import { fakeClient } from "../../test/fixtures";
import { useDashboardDiscovery } from "./useDashboardDiscovery";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolver) => {
    resolve = resolver;
  });
  return { promise, resolve };
}

function protocol(id: string): ProtocolSummary {
  return {
    protocol_id: id,
    title: id,
    created_at: "2026-08-19T00:00:00Z",
    latest_version_id: `${id}-version`,
    latest_version_status: "APPROVED",
    latest_revision: 1,
    step_count: 1,
    session_count: 0,
    archived_at: null,
  };
}

function session(id: string): SessionSummary {
  return {
    session_id: id,
    protocol_id: `${id}-protocol`,
    protocol_title: id,
    protocol_version_id: `${id}-version`,
    state: "ACTIVE",
    version: 1,
    step_count: 1,
    completed_step_count: 0,
    active_step_number: 1,
    resumable: true,
    created_at: "2026-08-19T00:00:00Z",
    started_at: "2026-08-19T00:00:00Z",
    ended_at: null,
    label: null,
  };
}

describe("useDashboardDiscovery", () => {
  it("keeps the newest search authoritative when an older request finishes last", async () => {
    const firstProtocols = deferred<ProtocolSummary[]>();
    const firstSessions = deferred<SessionSummary[]>();
    const secondProtocols = deferred<ProtocolSummary[]>();
    const secondSessions = deferred<SessionSummary[]>();
    const listProtocols = vi
      .fn()
      .mockReturnValueOnce(firstProtocols.promise)
      .mockReturnValueOnce(secondProtocols.promise);
    const listSessions = vi
      .fn()
      .mockReturnValueOnce(firstSessions.promise)
      .mockReturnValueOnce(secondSessions.promise);
    const client = fakeClient({ listProtocols, listSessions });
    const { result } = renderHook(() => useDashboardDiscovery(client, false));

    let firstLoad!: Promise<void>;
    let secondLoad!: Promise<void>;
    act(() => {
      firstLoad = result.current.search({ query: "old" });
      secondLoad = result.current.search({ query: "new" });
    });

    secondProtocols.resolve([protocol("new-protocol")]);
    secondSessions.resolve([session("new-session")]);
    await act(() => secondLoad);

    expect(result.current.protocols[0]?.protocol_id).toBe("new-protocol");
    expect(result.current.sessions[0]?.session_id).toBe("new-session");
    expect(result.current.authoritative).toBe(true);

    firstProtocols.resolve([protocol("old-protocol")]);
    firstSessions.resolve([session("old-session")]);
    await act(() => firstLoad);

    expect(result.current.protocols[0]?.protocol_id).toBe("new-protocol");
    expect(result.current.sessions[0]?.session_id).toBe("new-session");
    expect(listProtocols.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
    expect(listProtocols.mock.calls[1]?.[1]).toBeInstanceOf(AbortSignal);
    expect((listProtocols.mock.calls[0]?.[1] as AbortSignal).aborted).toBe(true);
  });

  it("loads exactly once when the dashboard becomes active", async () => {
    const listProtocols = vi.fn().mockResolvedValue([]);
    const listSessions = vi.fn().mockResolvedValue([]);
    const client = fakeClient({ listProtocols, listSessions });
    renderHook(() => useDashboardDiscovery(client, true));

    await waitFor(() => expect(listProtocols).toHaveBeenCalledTimes(1));
    expect(listSessions).toHaveBeenCalledTimes(1);
  });
});
