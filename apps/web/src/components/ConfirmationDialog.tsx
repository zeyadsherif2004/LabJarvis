import { AlertTriangle, Check, X } from "lucide-react";

import type { ActionResult } from "../actions";
import type { PendingAction } from "../types";

interface ConfirmationDialogProps {
  action: PendingAction;
  busy: boolean;
  onConfirm: (accepted: boolean) => Promise<ActionResult>;
  /**
   * Study condition "readback": a measurement can only be confirmed by saying
   * the value back (push-to-talk); the on-screen confirm is withheld. Reject
   * stays available in every condition.
   */
  readbackOnly?: boolean | undefined;
}

/**
 * State exactly what will be written, in the normalised form it will be
 * written in.
 *
 * The operator is confirming a database record, not a phrase they said. Showing
 * the normalised value is what lets them catch "5 mL" heard as "50 mL" before it
 * becomes part of the experimental record, so the summary must never paraphrase.
 */
function actionSummary(action: PendingAction): string {
  const { payload } = action;
  switch (action.intent) {
    case "RECORD_MEASUREMENT":
      return [payload.kind, payload.value, payload.unit].filter(Boolean).join(" ");
    case "RECORD_OBSERVATION":
      return payload.observation ?? "Observation";
    case "SKIP_STEP_WITH_REASON":
      return `Skip this step: ${payload.reason ?? "reason required"}`;
    case "SET_TIMER":
      return `Start a ${payload.duration_seconds ?? "reviewed"}-second timer`;
    case "PAUSE_TIMER":
      return "Pause the running timer";
    case "RESUME_TIMER":
      return "Resume the paused timer";
    case "CANCEL_TIMER":
      return "Cancel the timer without completing its interval";
    case "PAUSE_SESSION":
      return "Pause this session";
    case "RESUME_SESSION":
      return "Resume this session";
    case "COMPLETE_STEP":
      return "Complete the current step";
    case "END_SESSION":
      return "End this session early as aborted";
    default:
      return action.intent.replaceAll("_", " ").toLowerCase();
  }
}

/** Extra warning text for actions that cannot be undone. */
function warningNote(action: PendingAction): string | null {
  if (action.payload.requirement_warning) {
    return action.payload.requirement_warning;
  }
  if (action.intent === "END_SESSION") {
    return "This is final. Steps after the current one will be recorded as never performed.";
  }
  if (action.intent === "SKIP_STEP_WITH_REASON") {
    return "The step will be permanently marked as skipped, not performed.";
  }
  return null;
}

export function ConfirmationDialog({
  action,
  busy,
  onConfirm,
  readbackOnly = false,
}: ConfirmationDialogProps) {
  const warning = warningNote(action);
  const spokenOnly = readbackOnly && action.intent === "RECORD_MEASUREMENT";
  return (
    <section
      className="confirmation-panel"
      role="dialog"
      aria-labelledby="confirmation-title"
      aria-describedby="confirmation-description"
    >
      <AlertTriangle aria-hidden="true" />
      <div className="confirmation-copy">
        <h2 id="confirmation-title">Confirm before recording</h2>
        <p id="confirmation-description">
          Review the proposed action before LabJarvis applies it.
        </p>
        <strong className="confirmation-value">{actionSummary(action)}</strong>
        {warning !== null ? <p className="confirmation-warning">{warning}</p> : null}
        <small>This confirmation will be recorded in the append-only notebook.</small>
      </div>
      <div className="confirmation-actions">
        {spokenOnly ? (
          <p className="confirmation-readback">
            To confirm, hold the push-to-talk key and say the value back, for example
            &ldquo;confirm {action.payload.value} {action.payload.unit}&rdquo;.
          </p>
        ) : (
          <button
            className="button button--confirm"
            type="button"
            disabled={busy}
            onClick={() => void onConfirm(true)}
          >
            <Check aria-hidden="true" /> Confirm action
          </button>
        )}
        <button
          className="button button--secondary"
          type="button"
          disabled={busy}
          onClick={() => void onConfirm(false)}
        >
          <X aria-hidden="true" /> Reject
        </button>
      </div>
    </section>
  );
}
