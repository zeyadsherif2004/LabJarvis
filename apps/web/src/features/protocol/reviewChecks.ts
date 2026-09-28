import type { ProtocolStep } from "../../types";

// The server refuses to approve any of these (see protocol_validation.py). They
// are repeated here only so the review screen can say so before the reviewer
// presses Approve, not as the enforcement itself.
export const PLACEHOLDER_STEP_TITLE = "Operator-added step";
export const PLACEHOLDER_STEP_INSTRUCTION = "Review and replace this instruction before approval.";
export const PLACEHOLDER_REQUIREMENT_NAME = "review required";

export function isPlaceholder(value: string, placeholder: string): boolean {
  return value.trim().toLowerCase() === placeholder.toLowerCase();
}

/** Short names for extraction flags; the flag's own message carries the detail. */
export const FLAG_LABELS: Record<string, string> = {
  QUANTITY_RANGE: "Amount given as a range",
  QUANTITY_APPROXIMATE: "Approximate amount",
  QUANTITY_AMBIGUOUS_NUMBER: "Number could be read two ways",
  QUANTITY_UNUSUAL_UNIT: "Unit LabJarvis cannot measure",
  QUANTITY_UNSPECIFIED: "Amount not given as a number",
  QUANTITY_WITHOUT_REAGENT: "Amount without a reagent name",
  REQUIREMENT_LIMIT_REACHED: "Too many reagents to track",
  TIMING_RANGE: "Time given as a range",
  TIMING_APPROXIMATE: "Approximate time",
  TIMING_UNCLEAR: "Timing LabJarvis cannot measure",
  TIMING_MULTIPLE: "More than one duration",
  TIMING_OUT_OF_RANGE: "Duration cannot be a timer",
  REPETITION: "Step repeats",
  CONDITIONAL: "Step depends on a condition",
  WARNING_PLACEMENT: "Check which step this warning belongs to",
  PARAGRAPH_ATTACHED: "Separate paragraph attached",
  NUMBERING_RESTARTED: "Source numbering restarted",
  NUMBERING_IRREGULAR: "Source numbering skipped or repeated",
  BULLET_STEPS: "Steps taken from bullet points",
  SUBSECTION_STEPS: "Steps taken from numbered sub-sections",
  TABLE_ROW: "Step taken from a table row",
  OCR_DERIVED: "Text read by OCR",
  STEP_TEXT_OVERFLOW: "Step text too long",
  FLAG_LIMIT_REACHED: "More items than can be listed",
};

export function flagLabel(code: string): string {
  return FLAG_LABELS[code] ?? "Needs review";
}

/** Why a piece of source text is not part of any step. */
export const UNASSIGNED_REASONS: Record<string, string> = {
  NON_PROCEDURE_SECTION: "In a section that is not part of the procedure",
  OUTSIDE_STEPS: "Outside the numbered or bulleted steps",
  PAGE_HEADER_FOOTER: "Repeated page header, footer, or page number",
  WARNING_WITHOUT_STEP: "Warning with no step after it",
  STEP_TEXT_OVERFLOW: "Rest of a step that was too long",
};

export function unassignedReason(reason: string): string {
  return UNASSIGNED_REASONS[reason] ?? "Not placed in any step";
}

export interface ApprovalBlocker {
  stepNumber: number;
  text: string;
}

/** Everything the server would refuse at approval, phrased for the reviewer. */
export function approvalBlockers(steps: ProtocolStep[]): ApprovalBlocker[] {
  const blockers: ApprovalBlocker[] = [];
  steps.forEach((step, index) => {
    const stepNumber = index + 1;
    if (isPlaceholder(step.title, PLACEHOLDER_STEP_TITLE)) {
      blockers.push({ stepNumber, text: "replace the placeholder title" });
    }
    if (isPlaceholder(step.instruction, PLACEHOLDER_STEP_INSTRUCTION)) {
      blockers.push({ stepNumber, text: "write the reviewed instruction" });
    }
    if (step.requirements.some((item) => isPlaceholder(item.name, PLACEHOLDER_REQUIREMENT_NAME))) {
      blockers.push({ stepNumber, text: "name or remove the placeholder requirement" });
    }
    const open = (step.flags ?? []).filter((flag) => !flag.acknowledged).length;
    if (open > 0) {
      blockers.push({
        stepNumber,
        text: open === 1 ? "check 1 import flag" : `check ${open} import flags`,
      });
    }
  });
  return blockers;
}
