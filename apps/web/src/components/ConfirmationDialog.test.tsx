import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingAction } from "../types";
import { ConfirmationDialog } from "./ConfirmationDialog";

describe("ConfirmationDialog", () => {
  it("shows the server-captured unresolved label-review warning", () => {
    const action: PendingAction = {
      id: "action-1",
      session_id: "session-1",
      intent: "COMPLETE_STEP",
      payload: {
        step_id: "step-1",
        requirement_progress: '{"req-1":"confirmed","req-2":"unverified"}',
        requirement_warning:
          "1 of 2 visible-label reviews are unresolved. Completing the step will record that status.",
      },
      source: "typed_input",
      status: "PENDING",
      expected_session_version: 1,
      created_at: "2026-08-19T06:00:00Z",
      correlation_id: "correlation-1",
    };

    render(<ConfirmationDialog action={action} busy={false} onConfirm={vi.fn()} />);

    expect(screen.getByText(/1 of 2 visible-label reviews are unresolved/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm action/i })).toBeEnabled();
  });
});
