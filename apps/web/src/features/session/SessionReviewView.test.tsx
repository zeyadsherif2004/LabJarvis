import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { activeSnapshot } from "../../test/fixtures";
import type { NotebookEntry } from "../../types";
import { SessionReviewView } from "./SessionReviewView";

const entry: NotebookEntry = {
  id: "entry-1",
  session_id: "session-1",
  entry_type: "measurement",
  source: "operator",
  payload: { value: "10 mL" },
  created_at: "2026-08-19T00:00:00Z",
  correlation_id: "correlation-1",
  supersedes_entry_id: null,
  model_inference_id: null,
};

describe("SessionReviewView", () => {
  it("preserves a correction when appending it reports failure", async () => {
    const onCorrect = vi.fn().mockResolvedValue({
      ok: false,
      code: "NETWORK_ERROR",
    });
    render(
      <SessionReviewView
        session={activeSnapshot.session}
        entries={[entry]}
        inferences={[]}
        jsonExportUrl="#json"
        csvExportUrl="#csv"
        busy={false}
        onCorrect={onCorrect}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /correct measurement entry/i }),
    );
    const correction = screen.getByLabelText(/append correction/i);
    await userEvent.clear(correction);
    await userEvent.type(correction, "keep this correction");
    await userEvent.click(
      screen.getByRole("button", { name: /append correction/i }),
    );

    await waitFor(() => expect(onCorrect).toHaveBeenCalled());
    expect(screen.getByLabelText(/append correction/i)).toHaveValue(
      "keep this correction",
    );
  });
});
