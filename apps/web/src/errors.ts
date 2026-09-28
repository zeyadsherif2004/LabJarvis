import { ApiProblem, ApiUnreachable } from "./api/client";

/**
 * Turn a failure into something an operator can act on.
 *
 * Internal codes are useful for a bug report and useless at a bench, so the
 * code is demoted to a reference the user can quote while the sentence they
 * read is written for the situation they are actually in.
 */
export interface DisplayableError {
  /** The sentence shown to the operator. Never a raw code. */
  message: string;
  /** What to do next, when there is a concrete next step. */
  action: string | null;
  /** Correlation ID or error code, shown small, for support and logs. */
  reference: string | null;
  /** True when the API never answered, as opposed to answering with a problem. */
  offline: boolean;
}

const GUIDANCE: Record<string, { message: string; action: string }> = {
  SESSION_VERSION_CONFLICT: {
    message: "This session changed somewhere else while you were working.",
    action: "Reload the session to pick up the latest state, then try again.",
  },
  PENDING_ACTION_RESOLVED: {
    message: "That proposal was already confirmed or rejected.",
    action: "Check the notebook to see what was recorded.",
  },
  IDEMPOTENCY_CONFLICT: {
    message: "That request was already used for a different action.",
    action: "Start the action again so it gets a fresh identifier.",
  },
  TOOL_NOT_LEGAL_IN_STATE: {
    message: "That action is not available in the session's current state.",
    action: "Resume or start the session first, then retry.",
  },
  TOOL_NOT_ALLOWLISTED: {
    message: "That action is not one LabJarvis supports.",
    action: "Use one of the on-screen controls instead.",
  },
  MEASUREMENT_INVALID: {
    message: "That measurement could not be read as a number with a unit.",
    action: "Say or type the value and unit again, for example “10 millilitres”.",
  },
  REAGENT_REQUIREMENT_AMBIGUOUS: {
    message: "This step lists more than one reagent.",
    action: "Choose which reagent you are checking, then capture the label again.",
  },
  REAGENT_REQUIREMENT_NOT_FOUND: {
    message: "The active step does not list that reagent.",
    action: "Pick a reagent from the list shown for this step.",
  },
  REALTIME_DISABLED: {
    message: "Hands-free voice is not enabled on this installation.",
    action: "Use push-to-talk or typed commands, which work the same way.",
  },
  REALTIME_UNAVAILABLE: {
    message: "Hands-free voice is enabled but no provider is configured.",
    action: "Check the realtime settings, or continue with typed control.",
  },
  MODEL_UNAVAILABLE: {
    message: "The model service did not respond.",
    action: "Check the readiness page, then retry. Typed control still works.",
  },
  INFERENCE_TIMEOUT: {
    message: "The model took too long to respond.",
    action: "Try again, or continue with typed control.",
  },
  IMAGE_TOO_LARGE: {
    message: "That image is larger than LabJarvis accepts.",
    action: "Capture a new photo with the in-app camera instead.",
  },
  AUDIO_TOO_LARGE: {
    message: "That recording is longer than LabJarvis accepts.",
    action: "Record a shorter command and try again.",
  },
  PROTOCOL_STEPS_REQUIRED: {
    message: "No protocol steps could be found in that document.",
    action:
      "LabJarvis looks for numbered or bulleted steps, ideally under a Procedure or Method " +
      "heading. Check the document, then import it again.",
  },
  PROTOCOL_FLAGS_UNRESOLVED: {
    message: "Some steps still have import flags nobody has checked.",
    action: "Tick each flag once you have compared it with the source, then approve again.",
  },
  PROTOCOL_PLACEHOLDER_UNRESOLVED: {
    message: "A step or reagent still contains placeholder text.",
    action: "Replace the placeholder, or remove the step or reagent, then approve again.",
  },
  UNSUPPORTED_DOCUMENT: {
    message: "That file type, or its contents, is not a supported protocol document.",
    action: "Upload a PDF, Word (.docx), plain-text (.txt) or Markdown (.md) file.",
  },
  DOCUMENT_TEXT_ENCODING: {
    message: "That text file is not saved as UTF-8.",
    action: "Save it as UTF-8 (or upload the PDF or Word version) and import it again.",
  },
  PROTOCOL_VERSION_CONFLICT: {
    message: "This protocol draft was edited somewhere else.",
    action: "Reload the draft before approving it.",
  },
};

export function describeError(error: unknown): DisplayableError {
  if (error instanceof ApiUnreachable) {
    return {
      message: "LabJarvis could not reach its local API.",
      action: "Start the API, then choose Retry. Nothing you recorded has been lost.",
      reference: null,
      offline: true,
    };
  }
  if (error instanceof ApiProblem) {
    const guidance = GUIDANCE[error.problem.code];
    return {
      message: guidance?.message ?? error.problem.detail,
      action: guidance?.action ?? null,
      reference: error.problem.correlation_id || error.problem.code,
      offline: false,
    };
  }
  if (error instanceof Error && error.message) {
    return { message: error.message, action: null, reference: null, offline: false };
  }
  return {
    message: "LabJarvis could not complete that request.",
    action: "Try again. If it keeps happening, check the readiness page.",
    reference: null,
    offline: false,
  };
}

/** One-line form for compact surfaces such as toasts. */
export function errorSentence(error: unknown): string {
  const described = describeError(error);
  return described.action === null
    ? described.message
    : `${described.message} ${described.action}`;
}
