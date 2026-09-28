import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { GroundedAnswer } from "../../types";
import { ProtocolQuestionPanel } from "./ProtocolQuestionPanel";

const answer: GroundedAnswer = {
  text: "Add 10 mL ethanol.",
  abstained: false,
  citation: "Page 1 · step step-ethanol",
  page: 1,
  stepId: "step-ethanol",
  relevance: 0.92,
};

describe("ProtocolQuestionPanel", () => {
  it("shows the exact grounded answer and citation on keyboard submit", async () => {
    const onAsk = vi.fn().mockResolvedValue({ ok: true, value: answer });
    render(<ProtocolQuestionPanel busy={false} onAsk={onAsk} />);

    const question = screen.getByLabelText(/question about this protocol/i);
    await userEvent.type(question, "How much ethanol?{Enter}");

    expect(await screen.findByText("Add 10 mL ethanol.")).toBeVisible();
    expect(screen.getByText(/page 1.*step-ethanol/i)).toBeVisible();
    expect(onAsk).toHaveBeenCalledWith("How much ethanol?");
  });

  it("states plainly when the approved protocol does not answer", async () => {
    const onAsk = vi.fn().mockResolvedValue({
      ok: true,
      value: { ...answer, text: "", abstained: true, citation: null, page: null, stepId: null },
    });
    render(<ProtocolQuestionPanel busy={false} onAsk={onAsk} />);

    await userEvent.type(
      screen.getByLabelText(/question about this protocol/i),
      "What is the vendor lot?",
    );
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    expect(
      await screen.findByText(/approved protocol does not answer this question/i),
    ).toBeVisible();
  });

  it("preserves the question when the request reports failure", async () => {
    const onAsk = vi.fn().mockResolvedValue({ ok: false, code: "API_UNREACHABLE" });
    render(<ProtocolQuestionPanel busy={false} onAsk={onAsk} />);
    const question = screen.getByLabelText(/question about this protocol/i);

    await userEvent.type(question, "Keep this question");
    await userEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    expect(question).toHaveValue("Keep this question");
  });
});
