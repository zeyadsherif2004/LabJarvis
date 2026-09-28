import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiUnreachable } from "./api/client";
import { App } from "./App";
import {
  activeSnapshot,
  fakeClient,
  notReadyLocalMode,
  readyFakeMode,
} from "./test/fixtures";
import type { ProtocolSummary, SessionSummary } from "./types";

const protocolSummary: ProtocolSummary = {
  protocol_id: "protocol-1",
  title: "Ethanol protocol",
  created_at: "2026-07-16T12:00:00Z",
  latest_version_id: "version-1",
  latest_version_status: "APPROVED",
  latest_revision: 2,
  step_count: 1,
  session_count: 1,
  archived_at: null,
};

const resumableSession: SessionSummary = {
  session_id: "session-1",
  protocol_id: "protocol-1",
  protocol_title: "Ethanol protocol",
  protocol_version_id: "version-1",
  state: "ACTIVE",
  version: 1,
  step_count: 1,
  completed_step_count: 0,
  active_step_number: 1,
  resumable: true,
  created_at: "2026-07-16T12:05:00Z",
  started_at: "2026-07-16T12:06:00Z",
  ended_at: null,
  label: null,
};

beforeEach(() => {
  globalThis.location.hash = "";
});

describe("App", () => {
  it("opens on a dashboard listing recent protocols and resumable sessions", async () => {
    render(
      <App
        client={fakeClient({
          listProtocols: vi.fn().mockResolvedValue([protocolSummary]),
          listSessions: vi.fn().mockResolvedValue([resumableSession]),
        })}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: /continue where you left off/i }),
    ).toBeVisible();
    // The title appears in both the resumable-session row and the protocol row.
    expect(await screen.findAllByText("Ethanol protocol")).toHaveLength(2);
    expect(screen.getByText(/on step 1 of 1/i)).toBeVisible();
  });

  it("restores a session from the URL rather than from memory", async () => {
    // A reload lands directly here with no prior React state, which is exactly
    // what used to lose an in-progress experiment.
    globalThis.location.hash = "#/sessions/session-1";
    const getSessionSnapshot = vi.fn().mockResolvedValue(activeSnapshot);
    render(<App client={fakeClient({ getSessionSnapshot })} />);

    expect(
      await screen.findByRole("heading", { name: /add 10 ml ethanol/i }),
    ).toBeVisible();
    expect(getSessionSnapshot).toHaveBeenCalledWith("session-1");
  });

  it("reports a degraded model as a dependency problem, not an unreachable API", async () => {
    render(
      <App
        client={fakeClient({
          getReadiness: () =>
            Promise.resolve({ readiness: notReadyLocalMode, degraded: true }),
        })}
      />,
    );

    // The service answered. Telling the operator it was unreachable sent them
    // to diagnose the wrong thing entirely.
    expect(await screen.findByText(/a model is not usable yet/i)).toBeVisible();
    expect(screen.queryByText(/did not respond/i)).toBeNull();
  });

  it("reports a genuinely unreachable API separately, with a retry", async () => {
    render(
      <App
        client={fakeClient({
          getReadiness: () => Promise.reject(new ApiUnreachable(new Error("refused"))),
        })}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: /api did not respond/i }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled();
  });

  it("surfaces a dashboard failure with an actionable retry", async () => {
    const listProtocols = vi
      .fn()
      .mockRejectedValueOnce(new ApiUnreachable(new Error("refused")))
      .mockResolvedValue([protocolSummary]);
    render(
      <App
        client={fakeClient({
          listProtocols,
          listSessions: vi.fn().mockResolvedValue([]),
        })}
      />,
    );

    const retry = await screen.findByRole("button", { name: /retry/i });
    await userEvent.click(retry);

    expect((await screen.findAllByText("Ethanol protocol")).length).toBeGreaterThan(0);
  });

  it("shows the hosted-processing badge only when data leaves the machine", async () => {
    render(<App client={fakeClient()} />);
    await screen.findByRole("heading", { name: /continue where you left off/i });
    expect(screen.queryByText(/sends data off this machine/i)).toBeNull();

    render(
      <App
        client={fakeClient({
          getReadiness: () =>
            Promise.resolve({
              readiness: {
                ...readyFakeMode,
                mode: "hosted",
                privacy: { ...readyFakeMode.privacy, external_model_processing: true },
              },
              degraded: false,
            }),
        })}
      />,
    );
    expect(await screen.findByText(/sends data off this machine/i)).toBeVisible();
  });

  it("makes data management reachable from navigation", async () => {
    const user = userEvent.setup();
    render(<App client={fakeClient()} />);
    await screen.findByRole("heading", { name: /continue where you left off/i });

    await user.click(screen.getByRole("button", { name: "Data" }));

    expect(await screen.findByRole("heading", { name: "Data management" })).toBeVisible();
    expect(globalThis.location.hash).toBe("#/data");
  });
});
