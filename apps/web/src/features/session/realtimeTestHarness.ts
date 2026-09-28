import {
  MicrophoneIsolationFailed,
  ReadbackAbandoned,
  RecordingRefused,
  type ConfirmationCapture,
  type ReadbackFailureReason,
  type RealtimeCallbacks,
  type RealtimeSessionPort,
} from "../../media/realtimeSession";

/**
 * A realtime transport double whose speaking is genuinely asynchronous, and
 * which models the room *and the provider* rather than just the call sequence.
 *
 * The first version of this harness resolved `say()` synchronously, which is
 * precisely why it could not catch the defect it was supposed to guard: the
 * confirmation cycle started recording while the assistant was still reading
 * the proposal back, and a synchronous double made that look like correct
 * sequencing.
 *
 * The second version fixed that but still modelled recording as instantaneous,
 * so it could not express the *next* defect: microphone forwarding was restored
 * the moment the readback finished, while the local recorder was still open.
 * Default provider VAD can generate a response from that audio, so the
 * assistant could start talking into the operator's own confirmation clip.
 *
 * This version therefore models three things a real session has:
 *
 * - **A recording interval.** `captureUtterance()` opens a recorder that stays
 *   open until the test closes it, and whatever is audible during that interval
 *   ends up in the Blob.
 * - **A provider that answers by itself.** {@link ScriptedSession.providerRespondsSpontaneously}
 *   is what default VAD does when it hears a complete utterance. It succeeds
 *   only when microphone forwarding is attached - which is the whole safety
 *   property being tested.
 * - **Track replacement that can fail or be slow.** Suppression and restoration
 *   are promises, and either can be made to reject.
 */

export type SayPhase =
  | "idle"
  | "requested"
  | "started"
  | "playing"
  | "generation_complete"
  | "audio_stopped"
  | "completed"
  | "failed"
  | "interrupted";

export interface ScriptedSession {
  port: RealtimeSessionPort;
  callbacks: RealtimeCallbacks;
  /** Every sentence `say()` was asked to speak, in order. */
  said: string[];
  /** Sentences spoken best-effort, outside the safety-critical path. */
  announced: string[];
  /** Sentences whose promise has resolved. */
  spokenToCompletion: string[];
  phase: SayPhase;
  /** How many times a recording was actually started. */
  captureCount: number;
  /** How many times recording was refused because the assistant was audible. */
  refusedCaptureCount: number;
  cueCount: number;
  stopCount: number;
  interruptCount: number;
  outgoingSuppressed: boolean;
  suppressionLog: ("suppressed" | "resumed")[];

  // ---- what the room and the recorder did ----
  /** True between `captureUtterance()` starting and its recorder closing. */
  recorderOpen: boolean;
  /** Whether microphone forwarding was attached at any point while recording. */
  forwardingAttachedDuringCapture: boolean;
  /** Whether the assistant made any sound while the recorder was open. */
  assistantSpokeDuringCapture: boolean;
  /** Auto-responses the provider could not make because it heard nothing. */
  spontaneousResponsesBlocked: number;

  // ---- controls a test drives ----
  /** Provider began generating: audio is now leaving the speaker. */
  beginPlayback: () => void;
  /** The correct response completed and its audio stopped. Resolves `say()`. */
  finishReadback: () => void;
  /** A different response completed. Must not resolve the readback. */
  completeUnrelatedResponse: () => void;
  /** The provider failed mid-sentence. */
  failReadback: (reason?: ReadbackFailureReason) => void;
  /**
   * The transport reported an interruption.
   *
   * The production transport does *not* raise this during a confirmation
   * readback, because microphone forwarding is detached for the whole protected
   * window and the provider therefore cannot detect operator speech. It stays
   * here so the controller's handling of the rejection is still covered as
   * defence in depth.
   */
  bargeIn: () => void;
  /** Close the open recorder, resolving `captureUtterance()`. */
  finishCapture: () => void;
  /**
   * Default VAD hears a finished utterance and answers on its own.
   *
   * Succeeds only when microphone forwarding is attached. That is the point:
   * while the confirmation recorder is open the provider must be deaf, so this
   * must be a no-op and must never reach the recording.
   */
  providerRespondsSpontaneously: () => void;
  /** What the microphone would pick up if recording started right now. */
  roomAudio: () => string;
}

export interface HarnessOptions {
  /** Text the microphone hears when the assistant is audible (self-echo). */
  echoText?: string;
  /** Text the operator says once it is their turn. */
  operatorText?: string;
  /** Refuse to capture during playback, as the real transport does. */
  enforceCaptureGuard?: boolean;
  /** Leave the recorder open until `finishCapture()` is called. */
  manualCapture?: boolean;
  /** Make `suppressOutgoingAudio()` reject, as a failed replaceTrack would. */
  failSuppression?: boolean;
  /** Make `resumeOutgoingAudio()` reject after a safe recording. */
  failRestoration?: boolean;
  /** Resolve suppression only when the test releases it. */
  manualSuppression?: boolean;
}

export function scriptedRealtime(options: HarnessOptions = {}) {
  const {
    // Deliberately the exact phrase the backend accepts. If sequencing is
    // wrong, this is what ends up in the confirmation clip.
    echoText = "Say: confirm 10 mL",
    operatorText = "confirm 10 mL",
    enforceCaptureGuard = true,
    manualCapture = false,
    failSuppression = false,
    failRestoration = false,
    manualSuppression = false,
  } = options;

  const created: ScriptedSession[] = [];
  const releaseSuppression: (() => void)[] = [];

  const factory = (callbacks: RealtimeCallbacks): RealtimeSessionPort => {
    let resolveSay: (() => void) | null = null;
    let rejectSay: ((error: ReadbackAbandoned) => void) | null = null;
    let pendingText: string | null = null;
    let closeRecorder: (() => void) | null = null;
    let abortRecorder: ((error: RecordingRefused) => void) | null = null;
    let recorded: string[] = [];

    const record: ScriptedSession = {
      callbacks,
      said: [],
      announced: [],
      spokenToCompletion: [],
      phase: "idle",
      captureCount: 0,
      refusedCaptureCount: 0,
      cueCount: 0,
      stopCount: 0,
      interruptCount: 0,
      outgoingSuppressed: false,
      suppressionLog: [],
      recorderOpen: false,
      forwardingAttachedDuringCapture: false,
      assistantSpokeDuringCapture: false,
      spontaneousResponsesBlocked: 0,

      beginPlayback: () => {
        record.phase = "playing";
        if (record.recorderOpen) {
          record.assistantSpokeDuringCapture = true;
          recorded.push(echoText);
        }
        callbacks.onActivity("speaking");
      },
      finishReadback: () => {
        record.phase = "completed";
        callbacks.onActivity("listening");
        const text = pendingText;
        pendingText = null;
        const resolve = resolveSay;
        resolveSay = null;
        rejectSay = null;
        if (text !== null) record.spokenToCompletion.push(text);
        resolve?.();
      },
      completeUnrelatedResponse: () => {
        // A tool-call follow-up finishing. The readback is untouched.
        callbacks.onActivity("listening");
      },
      failReadback: (reason = "provider_error") => {
        record.phase = "failed";
        pendingText = null;
        const reject = rejectSay;
        resolveSay = null;
        rejectSay = null;
        reject?.(new ReadbackAbandoned(reason, `readback ${reason}`));
      },
      bargeIn: () => {
        record.phase = "interrupted";
        pendingText = null;
        const reject = rejectSay;
        resolveSay = null;
        rejectSay = null;
        reject?.(
          new ReadbackAbandoned("interrupted", "the operator spoke over the readback"),
        );
      },
      finishCapture: () => {
        closeRecorder?.();
      },
      providerRespondsSpontaneously: () => {
        if (record.outgoingSuppressed) {
          // The provider received no audio at all, so there is nothing for it
          // to answer. This is the isolation property under test.
          record.spontaneousResponsesBlocked += 1;
          return;
        }
        record.phase = "playing";
        callbacks.onActivity("speaking");
        if (record.recorderOpen) {
          record.assistantSpokeDuringCapture = true;
          if (enforceCaptureGuard) {
            // The real transport aborts the recording and discards the clip.
            const abort = abortRecorder;
            closeRecorder = null;
            abortRecorder = null;
            record.recorderOpen = false;
            abort?.(
              new RecordingRefused(
                "LabJarvis started speaking while you were being recorded, so that recording was discarded.",
              ),
            );
          } else {
            recorded.push(echoText);
          }
        }
      },
      roomAudio: () =>
        record.phase === "playing" || record.phase === "requested"
          ? echoText
          : operatorText,

      port: {
        connect: () => Promise.resolve(),
        setMuted: () => undefined,
        interrupt: () => {
          record.interruptCount += 1;
        },
        get assistantOutputActive() {
          return record.phase === "requested" || record.phase === "playing";
        },
        get outgoingAudioSuppressed() {
          return record.outgoingSuppressed;
        },
        suppressOutgoingAudio: () => {
          if (failSuppression) {
            return Promise.reject(
              new MicrophoneIsolationFailed(
                "LabJarvis could not stop sending microphone audio to the voice service.",
              ),
            );
          }
          const apply = () => {
            record.outgoingSuppressed = true;
            record.suppressionLog.push("suppressed");
          };
          if (!manualSuppression) {
            apply();
            return Promise.resolve();
          }
          return new Promise<void>((resolve) => {
            releaseSuppression.push(() => {
              apply();
              resolve();
            });
          });
        },
        resumeOutgoingAudio: () => {
          if (failRestoration) {
            return Promise.reject(
              new MicrophoneIsolationFailed(
                "LabJarvis could not resume sending microphone audio to the voice service.",
              ),
            );
          }
          record.outgoingSuppressed = false;
          record.suppressionLog.push("resumed");
          return Promise.resolve();
        },
        playCaptureCue: () => {
          record.cueCount += 1;
          return Promise.resolve();
        },
        say: (text: string) => {
          record.said.push(text);
          record.phase = "requested";
          pendingText = text;
          return new Promise<void>((resolve, reject) => {
            resolveSay = resolve;
            rejectSay = reject;
          });
        },
        announce: (text: string) => {
          record.announced.push(text);
          return Promise.resolve();
        },
        captureUtterance: () => {
          if (enforceCaptureGuard && record.port.assistantOutputActive) {
            record.refusedCaptureCount += 1;
            return Promise.reject(
              new RecordingRefused("LabJarvis was still speaking, so nothing was recorded."),
            );
          }
          record.captureCount += 1;
          record.recorderOpen = true;
          recorded = [record.roomAudio()];
          if (!record.outgoingSuppressed) record.forwardingAttachedDuringCapture = true;
          return new Promise<Blob>((resolve, reject) => {
            const close = () => {
              record.recorderOpen = false;
              closeRecorder = null;
              abortRecorder = null;
              resolve(new Blob([recorded.join(" ")]));
            };
            closeRecorder = close;
            abortRecorder = reject;
            if (!manualCapture) close();
          });
        },
        stop: () => {
          record.stopCount += 1;
          const reject = rejectSay;
          resolveSay = null;
          rejectSay = null;
          reject?.(new ReadbackAbandoned("closed", "session stopped"));
          const abort = abortRecorder;
          closeRecorder = null;
          abortRecorder = null;
          record.recorderOpen = false;
          abort?.(new RecordingRefused("hands-free voice was stopped"));
        },
        get isConnected() {
          return true;
        },
      },
    };
    created.push(record);
    return record.port;
  };

  /** Let a deliberately slow `replaceTrack(null)` finish. */
  const completeSuppression = (): void => {
    const pending = [...releaseSuppression];
    releaseSuppression.length = 0;
    for (const release of pending) release();
  };

  return { factory, created, completeSuppression };
}

/**
 * Drive a confirmation capture directly against a port.
 *
 * Used by tests that assert on {@link ConfirmationCapture} without going
 * through the React controller.
 */
export type { ConfirmationCapture };

/** Read a Blob back as text, so a test can assert what the microphone captured. */
export async function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") return blob.text();
  // jsdom without Blob.text: fall back to the reader, never to stringification.
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () =>
      resolve(typeof reader.result === "string" ? reader.result : ""),
    );
    reader.addEventListener("error", () => reject(new Error("blob unreadable")));
    reader.readAsText(blob);
  });
}
