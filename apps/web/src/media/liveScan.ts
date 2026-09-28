/**
 * The live-scan stability rule, `stable-asymmetric-v3` - a mirror of
 * `apps/api/src/labjarvis/domain/live_scan.py`, checked against the same
 * vectors (`apps/api/tests/fixtures/live_scan_vectors.json`).
 *
 * A MATCH is stable when `requiredConsecutive` readable probes among the most
 * recent `requiredConsecutive + TOLERATED_OUTLIERS` agree on the outcome. Other
 * outcomes also need the same normalised text: different OCR guesses while a
 * correct label moves into view must not settle as a MISMATCH.
 * v1 demanded three consecutive character-identical readings, which a
 * hand-held label read by real OCR (one frame in four is a small misreading or
 * unreadable) almost never produced. Stability authorises nothing: it only
 * chooses which frames - exactly the agreeing ones - are submitted to the
 * ordinary multi-frame verification, whose pending reading still needs an
 * explicit confirmation.
 */

export const LIVE_SCAN_RULE = "stable-asymmetric-v3";
export const TOLERATED_OUTLIERS = 2;

export interface ScanRule {
  requiredConsecutive: number;
  intervalMs: number;
  timeoutMs: number;
}

export interface ProbeReading {
  outcome: string;
  normalised: string;
}

export type ScanState =
  | { status: "scanning"; framesExamined: number; prompt: string }
  | {
      status: "stable";
      framesExamined: number;
      outcome: string;
      normalised: string;
      /** Indexes into the readings of the agreeing probes, oldest first. */
      frameIndexes: number[];
    }
  | {
      status: "timeout";
      framesExamined: number;
      reason: "unreadable" | "unsteady";
      prompt: string;
    };

export function maxFrames(rule: ScanRule): number {
  return Math.max(rule.requiredConsecutive, Math.ceil(rule.timeoutMs / rule.intervalMs));
}

function readable(reading: ProbeReading): boolean {
  return reading.outcome !== "UNREADABLE" && reading.normalised.trim() !== "";
}

function agreement(
  readings: ProbeReading[],
  rule: ScanRule,
): { outcome: string; indexes: number[] } | null {
  const start = Math.max(0, readings.length - (rule.requiredConsecutive + TOLERATED_OUTLIERS));
  const byReading = new Map<string, { outcome: string; indexes: number[] }>();
  for (let index = start; index < readings.length; index += 1) {
    const reading = readings[index];
    if (reading === undefined || !readable(reading)) continue;
    const key = JSON.stringify([
      reading.outcome,
      reading.outcome === "MATCH" ? "" : reading.normalised,
    ]);
    const group = byReading.get(key) ?? { outcome: reading.outcome, indexes: [] };
    group.indexes.push(index);
    byReading.set(key, group);
  }
  for (const { outcome, indexes } of byReading.values()) {
    // With a window of required + 2, at most one outcome can qualify.
    if (indexes.length >= rule.requiredConsecutive) {
      return { outcome, indexes: indexes.slice(-rule.requiredConsecutive) };
    }
  }
  return null;
}

export function evaluateScan(
  readings: ProbeReading[],
  elapsedMs: number,
  rule: ScanRule,
): ScanState {
  const count = readings.length;
  const agreed = agreement(readings, rule);
  if (agreed !== null) {
    const texts = agreed.indexes.map((index) => readings[index]?.normalised ?? "");
    const tally = new Map<string, number>();
    for (const text of texts) tally.set(text, (tally.get(text) ?? 0) + 1);
    const top = Math.max(...tally.values());
    // Commonest agreeing text; ties go to the most recent reading.
    const normalised = [...texts].reverse().find((text) => tally.get(text) === top) ?? "";
    return {
      status: "stable",
      framesExamined: count,
      outcome: agreed.outcome,
      normalised,
      frameIndexes: agreed.indexes,
    };
  }
  if (count >= maxFrames(rule) || elapsedMs >= rule.timeoutMs) {
    const unreadable = readings.filter((reading) => !readable(reading)).length;
    const reason = unreadable * 2 >= Math.max(count, 1) ? "unreadable" : "unsteady";
    return {
      status: "timeout",
      framesExamined: count,
      reason,
      prompt:
        reason === "unreadable"
          ? "I could not read the label. Move it closer, avoid glare, and check again."
          : "The label reading kept changing. Hold it still and check again.",
    };
  }
  const recent = readings.slice(-3);
  let prompt = "";
  if (recent.length === 3 && !recent.some(readable)) {
    prompt = "Move the label closer to the camera.";
  } else if (
    recent.length === 3 &&
    new Set(recent.filter(readable).map((reading) => reading.outcome)).size > 1
  ) {
    prompt = "Hold the label steady.";
  }
  return { status: "scanning", framesExamined: count, prompt };
}
