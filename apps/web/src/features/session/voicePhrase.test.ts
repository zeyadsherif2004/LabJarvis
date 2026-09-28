import { describe, expect, it } from "vitest";

import { activeSnapshot } from "../../test/fixtures";
import type { PendingAction, ReagentVerification } from "../../types";
import {
  reagentVoicePhrase,
  routeWakeSegment,
  spokenDuration,
  voiceConfirmationPhrase,
} from "./voicePhrase";

function action(intent: string, payload: Record<string, string> = {}): PendingAction {
  return {
    id: "a",
    session_id: activeSnapshot.session.id,
    intent,
    payload,
    source: "model_proposal",
    status: "PENDING",
    expected_session_version: 1,
    created_at: "2026-09-27T00:00:00Z",
    correlation_id: "c",
  };
}

describe("voice-only confirmation phrases", () => {
  it("restates the value and unit of a measurement", () => {
    expect(
      voiceConfirmationPhrase(
        action("RECORD_MEASUREMENT", { value: "37", unit: "degC" }),
        activeSnapshot,
      ),
    ).toBe("confirm 37 degrees Celsius");
  });

  it("restates a timer as a spoken duration", () => {
    expect(
      voiceConfirmationPhrase(action("SET_TIMER", { duration_seconds: "5400" }), activeSnapshot),
    ).toBe("confirm 1 hour 30 minutes");
    expect(spokenDuration(61)).toBe("1 minute 1 second");
  });

  it("names the active step number for completion", () => {
    const step = activeSnapshot.version.steps.find(
      (candidate) => candidate.id === activeSnapshot.session.active_step_id,
    );
    expect(voiceConfirmationPhrase(action("COMPLETE_STEP"), activeSnapshot)).toBe(
      `confirm step ${step?.number}`,
    );
  });

  it("has no phrase for proposals whose content cannot be restated", () => {
    expect(
      voiceConfirmationPhrase(action("RECORD_OBSERVATION", { observation: "x" }), activeSnapshot),
    ).toBeNull();
  });

  it("never contains a wake phrase", () => {
    for (const intent of ["RECORD_MEASUREMENT", "SET_TIMER", "PAUSE_SESSION", "END_SESSION"]) {
      const phrase = voiceConfirmationPhrase(
        action(intent, { value: "1", unit: "mL", duration_seconds: "60" }),
        activeSnapshot,
      );
      expect(phrase?.toLowerCase()).not.toContain("jarvis");
    }
  });

  it("offers a voice confirmation only for a matching label", () => {
    const base = { expected_label: "Ethanol" } as ReagentVerification;
    expect(reagentVoicePhrase({ ...base, outcome: "MATCH" })).toBe("confirm Ethanol");
    expect(reagentVoicePhrase({ ...base, outcome: "MISMATCH" })).toBeNull();
    expect(reagentVoicePhrase({ ...base, outcome: "UNCERTAIN" })).toBeNull();
  });
});

describe("wake-phrase segment routing", () => {
  it("answers a waiting proposal only in voice-only mode", () => {
    expect(routeWakeSegment({ proposalPending: true, readingPending: false, voiceOnly: true })).toBe(
      "confirm_proposal",
    );
    expect(
      routeWakeSegment({ proposalPending: true, readingPending: false, voiceOnly: false }),
    ).toBe("ignore");
  });

  it("answers a waiting reading only in voice-only mode", () => {
    expect(routeWakeSegment({ proposalPending: false, readingPending: true, voiceOnly: true })).toBe(
      "decide_reading",
    );
  });

  it("otherwise treats the segment as a command", () => {
    expect(
      routeWakeSegment({ proposalPending: false, readingPending: false, voiceOnly: true }),
    ).toBe("command");
  });
});
