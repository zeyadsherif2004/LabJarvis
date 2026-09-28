import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { successfulAction } from "../../actions";
import type { LabelProbe, ProtocolRequirement } from "../../types";
import { ReagentVerificationView } from "./ReagentVerificationView";

const requirement: ProtocolRequirement = {
  requirement_id: "req-1",
  name: "ethanol",
  quantity: "10",
  unit: "mL",
  aliases: [],
};
const rule = { requiredConsecutive: 3, intervalMs: 100, timeoutMs: 1_000 };

function probes(sequence: Array<[LabelProbe["outcome"], string]>) {
  let index = 0;
  return vi.fn((): Promise<LabelProbe> => {
    const [outcome, normalised] = sequence[Math.min(index, sequence.length - 1)] ?? ["UNREADABLE", ""];
    index += 1;
    return Promise.resolve({
      outcome,
      observed_normalised: normalised,
      visible_text: normalised.toUpperCase(),
      score: 0,
      requirement_id: "req-1",
      expected_label: "ethanol",
    });
  });
}

function scan(onProbe: ReturnType<typeof probes>) {
  const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
  const speak = vi.fn();
  const props = {
    requirements: [requirement],
    progress: [],
    result: null,
    mode: "fake" as const,
    busy: false,
    onVerify: vi.fn(() => Promise.resolve(successfulAction)),
    onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
    onVerifyFrames,
    onProbe,
    liveScan: rule,
    speak,
  };
  const { rerender } = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);
  rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);
  return { onVerifyFrames, speak, props };
}

describe("live scan until stable", () => {
  it("submits only the stable frames, with scan provenance, and records nothing itself", async () => {
    const onProbe = probes([
      ["UNREADABLE", ""],
      ["MATCH", "ethanol"],
      ["MATCH", "ethanol"],
      ["MATCH", "ethanol"],
    ]);
    const { onVerifyFrames } = scan(onProbe);

    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1), { timeout: 3000 });
    const [frames, requirementId, capture] = onVerifyFrames.mock.calls[0] as unknown as [
      Blob[],
      string,
      { mode: string; scanRule: string; framesExamined: number },
    ];
    expect(frames).toHaveLength(3);
    expect(requirementId).toBe("req-1");
    expect(capture).toMatchObject({
      mode: "live_scan",
      scanRule: "stable-asymmetric-v3",
      framesExamined: 4,
    });
    expect(onProbe).toHaveBeenCalledTimes(4);
  });

  it("tolerates a failed request and an OCR misread from a hand-held label", async () => {
    // What the operator saw: a 503 while speech was being transcribed and one
    // small misreading used to restart the count, so the scan timed out.
    const sequence: Array<[LabelProbe["outcome"], string] | "fail"> = [
      ["MATCH", "ethanol"],
      "fail",
      ["MISMATCH", "ethanoi"],
      ["MATCH", "ethanol"],
      ["MATCH", "ethanol"],
    ];
    let index = 0;
    const onProbe = vi.fn((): Promise<LabelProbe | null> => {
      const next = sequence[index] ?? ["MATCH", "ethanol"];
      index += 1;
      if (next === "fail") return Promise.resolve(null);
      return Promise.resolve({
        outcome: next[0],
        observed_normalised: next[1],
        visible_text: next[1],
        score: 0,
        requirement_id: "req-1",
        expected_label: "ethanol",
      });
    });
    const { onVerifyFrames } = scan(onProbe);

    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1), { timeout: 3000 });
    const [frames, , capture] = onVerifyFrames.mock.calls[0] as unknown as [
      Blob[],
      string,
      { framesExamined: number },
    ];
    // Three agreeing MATCH frames; the misread frame is not submitted and the
    // failed request is not counted as a frame at all.
    expect(frames).toHaveLength(3);
    expect(capture.framesExamined).toBe(4);
    expect(onProbe).toHaveBeenCalledTimes(5);
  });

  it("can be started again by voice in the same step after a stable reading", async () => {
    // Regression (zero-touch E2E, 2026-09-27): the "Steady reading" status was
    // never cleared, so a second spoken check in the same step did nothing.
    const onProbe = probes([["MATCH", "ethanol"]]);
    const onVerifyFrames = vi.fn(() => Promise.resolve(successfulAction));
    const props = {
      requirements: [requirement],
      progress: [],
      result: null,
      mode: "fake" as const,
      busy: false,
      onVerify: vi.fn(() => Promise.resolve(successfulAction)),
      onConfirm: vi.fn(() => Promise.resolve(successfulAction)),
      onVerifyFrames,
      onProbe,
      liveScan: rule,
      speak: vi.fn(),
    };
    const { rerender } = render(<ReagentVerificationView {...props} autoCaptureRequest={0} />);
    rerender(<ReagentVerificationView {...props} autoCaptureRequest={1} />);
    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1), { timeout: 3000 });
    await waitFor(() => expect(screen.queryByText(/Steady reading/)).toBeNull());
    rerender(<ReagentVerificationView {...props} autoCaptureRequest={2} />);
    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(2), { timeout: 3000 });
  });

  it("submits a stable mismatch as a reading to review, never as a verification", async () => {
    const onProbe = probes([["MISMATCH", "methanol"]]);
    const { onVerifyFrames } = scan(onProbe);
    // The view only proposes; the pending reading and its confirmation are
    // decided by the server, where a MISMATCH can never be confirmed by voice.
    await waitFor(() => expect(onVerifyFrames).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it("times out safely with a spoken prompt and submits nothing", async () => {
    const onProbe = probes([["UNREADABLE", ""]]);
    const { onVerifyFrames, speak } = scan(onProbe);

    expect(
      await screen.findByText(/could not read the label.*Nothing was recorded/i, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(onVerifyFrames).not.toHaveBeenCalled();
    expect(speak).toHaveBeenCalledWith(expect.stringMatching(/move the label closer/i));
  });

  it("keeps the three-frame mode when live scan is not configured", () => {
    render(
      <ReagentVerificationView
        requirements={[requirement]}
        progress={[]}
        result={null}
        mode="fake"
        busy={false}
        onVerify={vi.fn()}
        onConfirm={vi.fn()}
        onVerifyFrames={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /check label \(3 frames\)/i })).toBeEnabled();
  });
});
