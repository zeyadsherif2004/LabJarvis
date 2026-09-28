import { describe, expect, it } from "vitest";

import vectors from "../../../api/tests/fixtures/live_scan_vectors.json";
import { evaluateScan, LIVE_SCAN_RULE, maxFrames, type ProbeReading } from "./liveScan";

const rule = {
  requiredConsecutive: vectors.rule.required_consecutive,
  intervalMs: vectors.rule.interval_ms,
  timeoutMs: vectors.rule.timeout_ms,
};

describe("live-scan stability rule (shared vectors with the backend)", () => {
  for (const testCase of vectors.cases) {
    it(testCase.name, () => {
      const readings: ProbeReading[] = testCase.readings.map(([outcome, normalised]) => ({
        outcome: outcome ?? "",
        normalised: normalised ?? "",
      }));
      const state = evaluateScan(readings, testCase.elapsed_ms, rule);
      const expected = testCase.expected as Record<string, string | number>;
      expect(state.status).toBe(expected.status);
      expect(state.framesExamined).toBe(expected.frames_examined);
      if (state.status === "stable") {
        expect(state.outcome).toBe(expected.outcome);
        expect(state.normalised).toBe(expected.normalised);
        const indexes = (testCase.expected as { frame_indexes?: number[] }).frame_indexes;
        if (indexes !== undefined) expect(state.frameIndexes).toEqual(indexes);
      }
      if (state.status === "timeout" && "reason" in expected) {
        expect(state.reason).toBe(expected.reason);
      }
      if ("prompt" in expected && state.status !== "stable") {
        expect(state.prompt).toBe(expected.prompt);
      }
    });
  }

  it("names the same rule version as the backend vectors", () => {
    expect(LIVE_SCAN_RULE).toBe(vectors.rule_version);
  });

  it("derives the same frame budget as the backend", () => {
    expect(maxFrames(rule)).toBe(10);
  });
});
