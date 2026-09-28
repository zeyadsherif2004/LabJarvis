import type { PendingAction, ReagentVerification, SessionSnapshot } from "../../types";

/**
 * The words to say after the wake phrase to confirm a proposal voice-only.
 *
 * Display only: the backend (`domain/voice_confirmation.py`) is the sole
 * authority on whether a reply counts. The phrase never includes the wake
 * phrase itself, so reading it aloud can never be mistaken for an answer.
 * `null` means this proposal cannot be confirmed voice-only.
 */
const UNIT_WORDS: Record<string, string> = {
  uL: "microlitres",
  mL: "millilitres",
  L: "litres",
  mg: "milligrams",
  g: "grams",
  kg: "kilograms",
  degC: "degrees Celsius",
  K: "kelvin",
  s: "seconds",
  min: "minutes",
  h: "hours",
  rpm: "rpm",
  pH: "pH",
  "%": "percent",
};

function plural(value: number, word: string): string {
  return `${value} ${word}${value === 1 ? "" : "s"}`;
}

export function spokenDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(plural(hours, "hour"));
  if (minutes > 0) parts.push(plural(minutes, "minute"));
  if (seconds > 0 || parts.length === 0) parts.push(plural(seconds, "second"));
  return parts.join(" ");
}

/** "10 minute", "1 hour 30 minute": the form used before the word timer. */
export function adjectivalDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours} hour`);
  if (minutes > 0) parts.push(`${minutes} minute`);
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds} second`);
  return parts.join(" ");
}

export function voiceConfirmationPhrase(
  action: PendingAction,
  snapshot: Pick<SessionSnapshot, "session" | "version">,
  options: { voiceObservations?: boolean } = {},
): string | null {
  const payload = action.payload;
  const activeStep = snapshot.version.steps.find(
    (candidate) => candidate.id === snapshot.session.active_step_id,
  );
  switch (action.intent) {
    case "RECORD_MEASUREMENT":
    case "CORRECT_PREVIOUS_ENTRY": {
      const unit = payload.unit ?? "";
      return `confirm ${payload.value ?? ""} ${UNIT_WORDS[unit] ?? unit}`.trim();
    }
    case "SET_TIMER": {
      const seconds = Number(payload.duration_seconds);
      return Number.isFinite(seconds) ? `confirm ${spokenDuration(seconds)}` : null;
    }
    case "COMPLETE_STEP":
      return activeStep === undefined ? null : `confirm step ${activeStep.number}`;
    case "SKIP_STEP_WITH_REASON":
      return activeStep === undefined ? null : `confirm skip step ${activeStep.number}`;
    case "PAUSE_SESSION":
      return "confirm pause";
    case "RESUME_SESSION":
      return "confirm resume session";
    case "START_SESSION":
      return "confirm start session";
    case "PAUSE_TIMER":
      return "confirm pause timer";
    case "RESUME_TIMER":
      return "confirm resume timer";
    case "CANCEL_TIMER": {
      const seconds = Number(payload.timer_duration_seconds);
      return Number.isFinite(seconds) && payload.timer_duration_seconds !== undefined
        ? `confirm cancel ${adjectivalDuration(seconds)} timer`
        : null;
    }
    case "RECORD_OBSERVATION":
      // Opt-in and weaker: only a dictation both transcriptions agreed on.
      return options.voiceObservations === true && payload.heard_agreement === "dual_agreed"
        ? "confirm observation"
        : null;
    case "END_SESSION":
      return "confirm end session";
    default:
      return null;
  }
}

/** Only a MATCH can be confirmed by voice; anything else can only be rejected. */
export function reagentVoicePhrase(verification: ReagentVerification): string | null {
  return verification.outcome === "MATCH" ? `confirm ${verification.expected_label}` : null;
}

export type WakeSegmentRoute = "confirm_proposal" | "decide_reading" | "command" | "ignore";

/**
 * Where a wake-phrase segment goes. With voice-only confirmation off, a waiting
 * proposal or reading is answered only by push-to-talk or the screen, so the
 * segment is ignored rather than risk being read as a new command.
 */
export function routeWakeSegment(state: {
  proposalPending: boolean;
  readingPending: boolean;
  voiceOnly: boolean;
}): WakeSegmentRoute {
  if (state.proposalPending) return state.voiceOnly ? "confirm_proposal" : "ignore";
  if (state.readingPending) return state.voiceOnly ? "decide_reading" : "command";
  return "command";
}
