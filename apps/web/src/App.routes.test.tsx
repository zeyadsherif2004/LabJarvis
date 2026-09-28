import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "./api/client";
import { App } from "./App";
import {
  activeSnapshot,
  approvedProtocol,
  draftProtocol,
  fakeClient,
} from "./test/fixtures";
import type { ProblemDetail, SessionSnapshot } from "./types";

/**
 * Route identity regressions.
 *
 * The audited failure was concrete: opening a URL for a session that does not
 * exist left the *previous* session rendered with working controls, so a button
 * press mutated an experiment the URL did not name. These tests assert the two
 * things that matter - what is on screen, and what the client was asked to do.
 */

function problem(status: number, code: string): ApiProblem {
  const detail: ProblemDetail = {
    type: "about:blank",
    title: "Not found",
    status,
    detail: "no such resource",
    code,
    correlation_id: "corr-1",
  };
  return new ApiProblem(detail);
}

const secondSnapshot: SessionSnapshot = {
  ...activeSnapshot,
  session: {
    ...activeSnapshot.session,
    id: "session-2",
    version: 9,
  },
  version: {
    ...approvedProtocol,
    steps: [
      {
        ...approvedProtocol.steps[0]!,
        id: "step-1",
        instruction: "Centrifuge for 15 minutes.",
      },
    ],
  },
};

const secondDraft = {
  ...draftProtocol,
  id: "version-2",
  steps: [
    {
      ...draftProtocol.steps[0]!,
      id: "step-b1",
      instruction: "Second draft instruction.",
    },
  ],
};

beforeEach(() => {
  globalThis.location.hash = "";
});

describe("route identity", () => {
  it("never leaves the previous session on screen when the next one is missing", async () => {
    globalThis.location.hash = "#/sessions/session-1";
    const getSessionSnapshot = vi.fn((sessionId: string) =>
      sessionId === "session-1"
        ? Promise.resolve(activeSnapshot)
        : Promise.reject(problem(404, "SESSION_NOT_FOUND")),
    );
    const invokeTool = vi.fn();
    render(<App client={fakeClient({ getSessionSnapshot, invokeTool })} />);

    await screen.findByRole("heading", { name: /add 10 ml ethanol/i });

    globalThis.location.hash = "#/sessions/not-a-real-session";

    expect(
      await screen.findByRole("heading", { name: /that session does not exist/i }),
    ).toBeVisible();
    // The previous experiment is gone, not merely covered up.
    expect(screen.queryByRole("heading", { name: /add 10 ml ethanol/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /complete current step/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /end session early/i })).toBeNull();
    // And nothing was ever sent against the session that is no longer routed.
    expect(invokeTool).not.toHaveBeenCalled();
    expect(screen.getByText("not-a-real-session")).toBeVisible();
  });

  it("swaps one session for another without ever showing a mixture", async () => {
    globalThis.location.hash = "#/sessions/session-1";
    const getSessionSnapshot = vi.fn((sessionId: string) =>
      Promise.resolve(sessionId === "session-1" ? activeSnapshot : secondSnapshot),
    );
    render(<App client={fakeClient({ getSessionSnapshot })} />);

    await screen.findByRole("heading", { name: /add 10 ml ethanol/i });
    globalThis.location.hash = "#/sessions/session-2";

    expect(
      await screen.findByRole("heading", { name: /centrifuge for 15 minutes/i }),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: /add 10 ml ethanol/i })).toBeNull();
    expect(getSessionSnapshot).toHaveBeenCalledWith("session-2");
  });

  it("shows a not-found state for a missing protocol instead of the previous one", async () => {
    globalThis.location.hash = "#/protocols/version-1";
    const getProtocolVersion = vi.fn((versionId: string) =>
      versionId === "version-1"
        ? Promise.resolve(draftProtocol)
        : Promise.reject(problem(404, "PROTOCOL_VERSION_NOT_FOUND")),
    );
    render(<App client={fakeClient({ getProtocolVersion })} />);

    await screen.findByRole("heading", { name: /review imported protocol/i });
    globalThis.location.hash = "#/protocols/nope";

    expect(
      await screen.findByRole("heading", { name: /that protocol does not exist/i }),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: /review imported protocol/i })).toBeNull();
    expect(
      screen.queryByRole("button", { name: /approve reviewed protocol/i }),
    ).toBeNull();
  });

  it("resets the editor when the route moves between two drafts", async () => {
    const user = userEvent.setup();
    globalThis.location.hash = "#/protocols/version-1";
    const getProtocolVersion = vi.fn((versionId: string) =>
      Promise.resolve(versionId === "version-1" ? draftProtocol : secondDraft),
    );
    render(<App client={fakeClient({ getProtocolVersion })} />);

    const instruction = await screen.findByDisplayValue("Add 10 mL ethanol.");
    await user.clear(instruction);
    await user.type(instruction, "Locally edited text");
    expect(await screen.findByDisplayValue("Locally edited text")).toBeVisible();

    globalThis.location.hash = "#/protocols/version-2";

    // The second draft must arrive with its own steps, not the first draft's
    // unsaved edits. Editor state is seeded from props, so it has to be
    // discarded when the identity behind those props changes.
    expect(await screen.findByDisplayValue("Second draft instruction.")).toBeVisible();
    expect(screen.queryByDisplayValue("Locally edited text")).toBeNull();
  });

  it("ties the notebook to the routed session, not a cached snapshot", async () => {
    globalThis.location.hash = "#/sessions/session-1/notebook";
    const getSessionSnapshot = vi.fn((sessionId: string) =>
      Promise.resolve(sessionId === "session-1" ? activeSnapshot : secondSnapshot),
    );
    const getNotebook = vi.fn((sessionId: string) =>
      Promise.resolve({
        protocol_version_id: "version-1",
        entries: [],
        inferences: [
          {
            id: `inference-${sessionId}`,
            modality: "speech",
            adapter: "fake",
            model_name: `model-for-${sessionId}`,
            status: "succeeded",
            confidence: 0.9,
            latency_ms: 12,
            input_sha256: "a".repeat(64),
            output: {},
            created_at: "2026-07-16T12:07:00Z",
          },
        ],
        verifications: [],
        audit_events: [],
        utterances: [],
      }),
    );
    render(<App client={fakeClient({ getSessionSnapshot, getNotebook })} />);

    expect(await screen.findByText("model-for-session-1")).toBeVisible();
    globalThis.location.hash = "#/sessions/session-2/notebook";

    expect(await screen.findByText("model-for-session-2")).toBeVisible();
    expect(screen.queryByText("model-for-session-1")).toBeNull();
    expect(getNotebook).toHaveBeenCalledWith("session-2");
  });

  it("drops a slow response for a route the operator already left", async () => {
    globalThis.location.hash = "#/sessions/session-1";
    let releaseFirst: (value: SessionSnapshot) => void = () => undefined;
    const getSessionSnapshot = vi.fn((sessionId: string) => {
      if (sessionId === "session-1") {
        return new Promise<SessionSnapshot>((resolve) => {
          releaseFirst = resolve;
        });
      }
      return Promise.resolve(secondSnapshot);
    });
    render(<App client={fakeClient({ getSessionSnapshot })} />);

    await screen.findByText(/restoring session/i);
    globalThis.location.hash = "#/sessions/session-2";
    await screen.findByRole("heading", { name: /centrifuge for 15 minutes/i });

    // Session 1 answers only now. An out-of-order response must not replace the
    // session the operator is actually looking at.
    releaseFirst(activeSnapshot);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /centrifuge for 15 minutes/i })).toBeVisible(),
    );
    expect(screen.queryByRole("heading", { name: /add 10 ml ethanol/i })).toBeNull();
  });

  it("offers recovery rather than a dead end when a session fails to load", async () => {
    const user = userEvent.setup();
    globalThis.location.hash = "#/sessions/session-1";
    const getSessionSnapshot = vi
      .fn()
      .mockRejectedValueOnce(problem(503, "MODEL_UNAVAILABLE"))
      .mockResolvedValue(activeSnapshot);
    render(<App client={fakeClient({ getSessionSnapshot })} />);

    expect(
      await screen.findByRole("heading", { name: /that session could not be opened/i }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("heading", { name: /add 10 ml ethanol/i })).toBeVisible();
  });
});
