import { useEffect, useRef, useState } from "react";
import { Ear, EarOff } from "lucide-react";

import {
  createHandsFreeListener,
  type HandsFreeListener,
  type HandsFreeOptions,
} from "../../media/handsFreeListener";

export type ListenMode = "command" | "standby" | "barge_in";

interface HandsFreeListeningPanelProps {
  wakePhrase: string;
  /** True while another request is in flight or a camera capture is running. */
  busy: boolean;
  onSegment: (audio: Blob, options?: { listenMode?: ListenMode }) => void;
  /** Injected in tests; the real listener loads the ONNX VAD lazily. */
  createListener?: (options: HandsFreeOptions) => Promise<HandsFreeListener>;
  /** Idle switch-off outside a session. `null` disables it (a session is open). */
  idleTimeoutMs?: number | null;
  /** Injected in tests; defaults to the browser's speech-synthesis state. */
  isSpeaking?: () => boolean;
  /**
   * Echo guard: a segment that *started* while LabJarvis was speaking, or within
   * this many milliseconds after it stopped, is never sent as a command or an
   * answer. The read-back contains the exact value to confirm, so it must never
   * be sent.
   */
  echoGuardMs?: number;
  /**
   * Barge-in (optional): instead of dropping a segment that overlapped
   * LabJarvis speaking, send it in `barge_in` mode, where the server honours
   * only "stop"/"quiet", "mute" and "stop the alarm", and discards everything else untraced. It
   * can therefore interrupt speech, and never propose, confirm or record.
   */
  onBargeIn?: ((audio: Blob) => void) | undefined;
  /** Start as soon as mounted (hands-free autostart, after the one gesture). */
  autoStart?: boolean;
  /** After "stop listening": only "start listening" is accepted. */
  standby?: boolean;
  /** Spoken announcements (microphone failure, recovery). */
  announce?: ((text: string) => void) | undefined;
  /** Retry delays after a microphone or detector failure, before asking for a tap. */
  retryDelaysMs?: number[];
  onStateChange?: ((state: ListeningState) => void) | undefined;
}

export type ListeningState = "off" | "starting" | "listening" | "paused" | "error";

function browserSpeaking(): boolean {
  return "speechSynthesis" in globalThis && globalThis.speechSynthesis.speaking === true;
}

/**
 * Wake-phrase listening. Every detected speech segment is sent with "wake
 * phrase required": anything not starting with the wake phrase is discarded by
 * the server without interpretation or retention. While a session is open the
 * listener stays on with no idle timeout; it pauses only while a request is in
 * flight, and recovers from a microphone or detector failure by itself (three
 * attempts with backoff) before asking for a tap.
 */
export function HandsFreeListeningPanel({
  wakePhrase,
  busy,
  onSegment,
  createListener = createHandsFreeListener,
  idleTimeoutMs = 10 * 60_000,
  isSpeaking = browserSpeaking,
  echoGuardMs = 1_000,
  onBargeIn,
  autoStart = false,
  standby = false,
  announce,
  retryDelaysMs = [1_000, 2_000, 4_000],
  onStateChange,
}: HandsFreeListeningPanelProps) {
  const [state, setStateValue] = useState<ListeningState>("off");
  const [detail, setDetail] = useState<string | null>(null);
  const listener = useRef<HandsFreeListener | null>(null);
  const lastActivity = useRef(Date.now());
  const segmentHandler = useRef(onSegment);
  segmentHandler.current = onSegment;
  const bargeInHandler = useRef(onBargeIn);
  bargeInHandler.current = onBargeIn;
  const announceRef = useRef(announce);
  announceRef.current = announce;
  const standbyRef = useRef(standby);
  standbyRef.current = standby;
  const speakingNow = useRef(isSpeaking);
  speakingNow.current = isSpeaking;
  const lastSpokeAt = useRef(0);
  const segmentTainted = useRef(false);
  const failures = useRef(0);
  const retryHandle = useRef<number | null>(null);
  const mounted = useRef(true);
  const [discarded, setDiscarded] = useState(0);
  const stateChange = useRef(onStateChange);
  stateChange.current = onStateChange;

  function setState(next: ListeningState): void {
    setStateValue(next);
    stateChange.current?.(next);
  }

  function inEchoWindow(): boolean {
    if (speakingNow.current()) {
      lastSpokeAt.current = Date.now();
      return true;
    }
    return Date.now() - lastSpokeAt.current < echoGuardMs;
  }

  function recover(): void {
    const current = listener.current;
    listener.current = null;
    void current?.destroy().catch(() => undefined);
    const attempt = failures.current;
    failures.current += 1;
    if (attempt < retryDelaysMs.length) {
      setState("starting");
      setDetail(`Microphone problem; retrying (attempt ${attempt + 1} of ${retryDelaysMs.length}).`);
      if (attempt === 0) announceRef.current?.("Listening stopped: microphone problem. Retrying.");
      retryHandle.current = window.setTimeout(() => {
        retryHandle.current = null;
        if (mounted.current) void turnOn(true);
      }, retryDelaysMs[attempt]);
      return;
    }
    setState("error");
    setDetail(
      "Hands-free listening could not restart; push-to-talk remains available. Tap Start hands-free listening to try again.",
    );
    announceRef.current?.(
      "Hands-free listening could not restart. Tap the screen once to start it again.",
    );
  }

  async function turnOn(isRetry = false): Promise<void> {
    if (!isRetry) failures.current = 0;
    setState("starting");
    if (!isRetry) setDetail(null);
    try {
      listener.current = await createListener({
        onSegment: (audio) => {
          lastActivity.current = Date.now();
          // Echo guard: a segment that began during (or just after) LabJarvis'
          // own speech is never a command or an answer. With barge-in enabled it
          // may still interrupt speech, through the barge-in-only channel.
          if (segmentTainted.current || inEchoWindow()) {
            segmentTainted.current = false;
            if (bargeInHandler.current !== undefined && !standbyRef.current) {
              bargeInHandler.current(audio);
            } else {
              setDiscarded((count) => count + 1);
            }
            return;
          }
          segmentHandler.current(audio, {
            listenMode: standbyRef.current ? "standby" : "command",
          });
        },
        onSpeechStart: () => {
          lastActivity.current = Date.now();
          segmentTainted.current = inEchoWindow();
        },
        onError: () => recover(),
      });
      await listener.current.start();
      lastActivity.current = Date.now();
      failures.current = 0;
      setDetail(null);
      setState("listening");
    } catch {
      if (isRetry || announceRef.current !== undefined) {
        recover();
      } else {
        setState("error");
        setDetail("Hands-free listening could not start; push-to-talk remains available.");
      }
    }
  }

  async function turnOff(reason: string | null = null): Promise<void> {
    if (retryHandle.current !== null) window.clearTimeout(retryHandle.current);
    retryHandle.current = null;
    const current = listener.current;
    listener.current = null;
    setState("off");
    setDetail(reason);
    await current?.destroy().catch(() => undefined);
  }

  // Autostart once, after the single gesture the browser requires.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!autoStart || autoStarted.current) return;
    autoStarted.current = true;
    void turnOn();
  }, [autoStart]);

  // Pause while a request is in flight; resume afterwards; idle timeout only
  // outside a session. Without barge-in the detector also pauses while
  // LabJarvis speaks, as before.
  useEffect(() => {
    if (state !== "listening" && state !== "paused") return undefined;
    const handle = window.setInterval(() => {
      const current = listener.current;
      if (current === null) return;
      if (idleTimeoutMs !== null && Date.now() - lastActivity.current > idleTimeoutMs) {
        void turnOff("Hands-free listening switched off after a period without speech.");
        return;
      }
      const talking = speakingNow.current();
      if (talking) lastSpokeAt.current = Date.now();
      const shouldPause = busy || (talking && bargeInHandler.current === undefined);
      if (shouldPause && state === "listening") {
        void current.pause();
        setState("paused");
      } else if (!shouldPause && state === "paused") {
        void current.start();
        setState("listening");
      }
    }, 250);
    return () => window.clearInterval(handle);
  }, [busy, idleTimeoutMs, state]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (retryHandle.current !== null) window.clearTimeout(retryHandle.current);
      void listener.current?.destroy();
    };
  }, []);

  const labels: Record<ListeningState, string> = {
    off: "Hands-free listening is off",
    starting: "Starting hands-free listening...",
    listening: standby
      ? `Listening paused by voice - only "${wakePhrase}, start listening" is recognised`
      : `Listening for "${wakePhrase} ..." - speech without the wake phrase is ignored`,
    paused: "Hands-free listening paused while LabJarvis speaks or works",
    error: "Hands-free listening unavailable",
  };
  const on = state === "listening" || state === "paused" || state === "starting";
  return (
    <section className="hands-free-panel" aria-labelledby="hands-free-title">
      <h2 id="hands-free-title">Hands-free listening</h2>
      <p className={`hands-free-status hands-free-status--${state}`} role="status" aria-live="polite">
        {on ? <Ear aria-hidden="true" /> : <EarOff aria-hidden="true" />} {labels[state]}
      </p>
      <button
        className="button button--secondary"
        type="button"
        aria-pressed={on}
        onClick={() => void (on ? turnOff() : turnOn())}
      >
        {on ? "Stop hands-free listening" : "Start hands-free listening"}
      </button>
      {detail !== null ? <p className="hands-free-detail">{detail}</p> : null}
      {discarded > 0 ? (
        <p className="hands-free-detail">
          Ignored {discarded} segment{discarded === 1 ? "" : "s"} that overlapped LabJarvis
          speaking.
        </p>
      ) : null}
      <small>
        Voice detection runs in this browser. Only speech segments are sent to the local
        LabJarvis service, which discards anything not addressed to {wakePhrase}.
        {idleTimeoutMs === null ? " Listening stays on while this session is open." : ""}
      </small>
    </section>
  );
}
