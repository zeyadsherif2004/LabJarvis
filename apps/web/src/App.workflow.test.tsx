import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  activeSnapshot,
  approvedProtocol,
  draftProtocol,
  fakeClient,
} from "./test/fixtures";

beforeEach(() => {
  globalThis.location.hash = "";
});

describe("App workflow", () => {
  it("moves an imported protocol into explicit operator review", async () => {
    const user = userEvent.setup();
    const uploadProtocol = vi.fn().mockResolvedValue({
      protocol_id: "protocol-1",
      title: "Development protocol",
      version: draftProtocol,
    });
    globalThis.location.hash = "#/import";
    render(
      <App
        client={fakeClient({
          uploadProtocol,
          getProtocolVersion: vi.fn().mockResolvedValue(draftProtocol),
        })}
      />,
    );

    const file = new File(["reviewed source"], "protocol.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    await user.upload(await screen.findByLabelText(/protocol document/i), file);

    expect(
      await screen.findByRole("heading", { name: /review imported protocol/i }),
    ).toBeVisible();
    expect(uploadProtocol).toHaveBeenCalledWith(file, false);
    expect(screen.getByDisplayValue("Add 10 mL ethanol.")).toBeVisible();
  });

  it("approves and opens a session in a single transactional step", async () => {
    const user = userEvent.setup();
    const readySession = {
      ...activeSnapshot.session,
      state: "READY" as const,
      active_step_id: null,
      version: 0,
      started_at: null,
      steps: [{ ...activeSnapshot.session.steps[0]!, state: "NOT_STARTED" }],
    };
    // Review, approval, and session creation commit as one request, so the
    // browser can no longer be left holding a revision the backend superseded.
    const reviewApproveAndStart = vi
      .fn()
      .mockResolvedValue({ version: approvedProtocol, session: readySession });
    const reviewProtocol = vi.fn().mockRejectedValue(new Error("must not be called"));
    const getSessionSnapshot = vi
      .fn()
      .mockResolvedValueOnce({ ...activeSnapshot, session: readySession })
      .mockResolvedValue(activeSnapshot);

    globalThis.location.hash = "#/import";
    render(
      <App
        client={fakeClient({
          uploadProtocol: vi.fn().mockResolvedValue({
            protocol_id: "protocol-1",
            title: "Development protocol",
            version: draftProtocol,
          }),
          getProtocolVersion: vi.fn().mockResolvedValue(draftProtocol),
          reviewProtocol,
          reviewApproveAndStart,
          getSessionSnapshot,
          startSession: vi.fn().mockResolvedValue(activeSnapshot.session),
        })}
      />,
    );

    await user.click(await screen.findByRole("button", { name: /load development sample/i }));
    await user.click(
      await screen.findByRole("button", { name: /approve reviewed protocol/i }),
    );

    expect(reviewApproveAndStart).toHaveBeenCalledWith(
      "version-1",
      expect.any(Array),
      0,
      expect.any(String),
      null,
    );
    // The two-call sequence is gone, not merely wrapped.
    expect(reviewProtocol).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: /start sterile session/i }));
    expect(await screen.findByRole("heading", { name: /add 10 ml ethanol/i })).toBeVisible();
    // The session is now URL-addressable, so a reload returns to exactly here.
    expect(globalThis.location.hash).toBe("#/sessions/session-1");
  });

  it("navigates between the session and its notebook without losing the session", async () => {
    const user = userEvent.setup();
    globalThis.location.hash = "#/sessions/session-1";
    render(
      <App
        client={fakeClient({
          getSessionSnapshot: vi.fn().mockResolvedValue(activeSnapshot),
        })}
      />,
    );

    await screen.findByRole("heading", { name: /add 10 ml ethanol/i });
    await user.click(screen.getByRole("button", { name: "Notebook" }));

    expect(await screen.findByRole("heading", { name: /session notebook/i })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Session" }));
    expect(await screen.findByRole("heading", { name: /add 10 ml ethanol/i })).toBeVisible();
  });
});
