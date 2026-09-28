import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ProtocolRequirement, ReagentVerification } from "../../types";
import { ReagentVerificationView } from "./ReagentVerificationView";

const ethanol: ProtocolRequirement = {
  requirement_id: "step-1-req-1",
  name: "ethanol",
  quantity: "10",
  unit: "mL",
  aliases: [],
};

const buffer: ProtocolRequirement = {
  requirement_id: "step-1-req-2",
  name: "buffer",
  quantity: "250",
  unit: "uL",
  aliases: [],
};

const mismatch: ReagentVerification = {
  id: "verification-1",
  session_id: "session-1",
  step_id: "step-1",
  requirement_id: "step-1-req-1",
  expected_label: "ethanol",
  observed_label: "methanol",
  outcome: "MISMATCH",
  score: 88.5,
  expected_normalised: "ethanol",
  observed_normalised: "methanol",
  match_threshold: 92,
  uncertain_threshold: 72,
  model_confidence: 0.9,
  decision: "PENDING",
  created_at: "2026-07-16T12:00:00Z",
  resolved_at: null,
};

describe("ReagentVerificationView", () => {
  it("renders nothing when the active step lists no reagent", () => {
    // Showing a verification panel for a step with no reagent invited the
    // operator to photograph a bottle the protocol never asked about.
    const { container } = render(
      <ReagentVerificationView
        requirements={[]}
        progress={[]}
        result={null}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows the single expected reagent without asking the operator to choose", () => {
    render(
      <ReagentVerificationView
        requirements={[ethanol]}
        progress={[]}
        result={null}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText(/10 mL ethanol/i)).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("requires an explicit choice when a step lists several reagents", async () => {
    const onVerify = vi.fn();
    render(
      <ReagentVerificationView
        requirements={[ethanol, buffer]}
        progress={[]}
        result={null}
        mode="fake"
        busy={false}
        onVerify={onVerify}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("radio")).toHaveLength(2);
    await userEvent.click(screen.getByRole("radio", { name: /250 uL buffer/i }));
    await userEvent.click(screen.getByRole("button", { name: /verify visible label/i }));

    // The captured image is attributed to the reagent the operator selected,
    // never to whichever requirement happened to be first.
    expect(onVerify).toHaveBeenCalledWith(expect.anything(), "step-1-req-2");
  });

  it("never communicates a mismatch by colour alone", () => {
    render(
      <ReagentVerificationView
        requirements={[ethanol]}
        progress={[]}
        result={mismatch}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText(/do not proceed/i)).toBeInTheDocument();
    // Shown as observed text and pre-filled into the correction field.
    expect(screen.getAllByText(/methanol/i).length).toBeGreaterThan(0);
  });

  it("requires an explicit operator decision before anything is recorded", async () => {
    const onConfirm = vi.fn();
    render(
      <ReagentVerificationView
        requirements={[ethanol]}
        progress={[]}
        result={mismatch}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /reject/i }));

    expect(onConfirm).toHaveBeenCalledWith(false, null);
  });

  it("renders every authoritative label-review status after rehydration", () => {
    render(
      <ReagentVerificationView
        requirements={[ethanol, buffer]}
        progress={[
          {
            requirement_id: ethanol.requirement_id,
            name: ethanol.name,
            status: "confirmed",
            verification_id: "verification-1",
          },
          {
            requirement_id: buffer.requirement_id,
            name: buffer.name,
            status: "rejected",
            verification_id: "verification-2",
          },
        ]}
        result={null}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText("label review confirmed")).toBeInTheDocument();
    expect(screen.getByText("label review rejected")).toBeInTheDocument();
    expect(screen.queryByText("checked")).not.toBeInTheDocument();
  });
});
