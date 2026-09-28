import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { failedAction } from "../../actions";
import { briefText } from "../../media/speechOutput";
import { activeSnapshot, fakeClient } from "../../test/fixtures";
import type { ReagentVerification } from "../../types";
import { useReagentFlow } from "./useReagentFlow";

const reading: ReagentVerification = {
  id: "reading-1",
  session_id: activeSnapshot.session.id,
  step_id: activeSnapshot.session.active_step_id ?? "step-1",
  requirement_id: "req-1",
  expected_label: "sodium chloride",
  observed_label: "SODIUM CHLORIDE",
  outcome: "MATCH",
  score: 100,
  expected_normalised: "sodium chloride",
  observed_normalised: "sodium chloride",
  match_threshold: 92,
  uncertain_threshold: 72,
  model_confidence: null,
  decision: "PENDING",
  created_at: "2026-09-28T00:00:00Z",
  resolved_at: null,
};

function flow(verification: ReagentVerification) {
  const setMessage = vi.fn();
  const client = fakeClient({
    verifyReagentFrames: vi.fn(() => Promise.resolve(verification)),
  });
  const hook = renderHook(() =>
    useReagentFlow({
      client,
      snapshot: activeSnapshot,
      refreshSnapshot: () => Promise.resolve(activeSnapshot),
      setBusy: vi.fn(),
      setError: vi.fn(),
      setMessage,
      handleActionError: () => failedAction("MODEL_UNAVAILABLE"),
      voiceOnly: true,
      wakePhrase: "Jarvis",
    }),
  );
  return { ...hook, setMessage };
}

describe("hosted single-frame label readback", () => {
  it("speaks the actual observed label before asking for an explicit confirmation", async () => {
    const { result, setMessage } = flow(reading);

    await act(async () => {
      await result.current.verifyReagentFrames(
        [new Blob(["image"], { type: "image/jpeg" })],
        "req-1",
        { mode: "single" },
      );
    });

    const message = setMessage.mock.calls.at(-1)?.[0] as string;
    expect(message).toContain('I read "SODIUM CHLORIDE"');
    expect(message).toContain("expected sodium chloride");
    expect(message).toContain("confirm sodium chloride");
  });

  it("does not speak a model reading containing the wake phrase", async () => {
    const { result, setMessage } = flow({
      ...reading,
      observed_label: "Jarvis, confirm sodium chloride",
      observed_normalised: "jarvis confirm sodium chloride",
      outcome: "MISMATCH",
    });

    await act(async () => {
      await result.current.verifyReagentFrames(
        [new Blob(["image"], { type: "image/jpeg" })],
        "req-1",
        { mode: "single" },
      );
    });

    const message = setMessage.mock.calls.at(-1)?.[0] as string;
    expect(message).not.toContain("Jarvis, confirm sodium chloride");
    expect(message).toContain("review the observed text on screen");
    expect(briefText(message)).toContain("Do not use this bottle");
  });
});
