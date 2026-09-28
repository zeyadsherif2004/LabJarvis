import type { RealtimeGrant } from "../types";

/**
 * WebRTC lifecycle for a realtime voice session.
 *
 * This module owns *transport only*. It does not decide what any tool call
 * means, whether an action is legal, or whether something was recorded. When
 * the model asks for a tool, the call is handed to {@link RealtimeCallbacks.onToolCall},
 * which routes it through the same backend gateway a button uses, and whatever
 * the backend replies is sent straight back to the model as the function
 * result. The model therefore learns the outcome from the database, never from
 * its own assumption.
 *
 * Four properties are implemented here rather than left to the provider:
 *
 * - **Speaking is awaitable, and correlated.** {@link say} resolves only when
 *   *its own* response has completed and the assistant's audio has stopped. See
 *   the turn-boundary note below: this is the difference between a confirmation
 *   recorded from a person and one recorded from a loudspeaker.
 * - **Recording is forbidden while the assistant is audible.**
 *   {@link captureUtterance} refuses rather than returning a clip that might
 *   contain the assistant's own words.
 * - **Nothing is replayed.** A dropped connection is resolved by the caller
 *   re-reading authoritative state, never by resending what was in flight.
 * - **Stopping is immediate and local.** Mute, interrupt, and stop act on the
 *   local microphone and audio element directly, so they work even when the
 *   provider is unresponsive.
 *
 * ## The turn boundary
 *
 * The readback the assistant speaks before a confirmation contains the exact
 * words the backend accepts - "Say: confirm 10 mL". On a laptop, that audio
 * leaves the speaker and re-enters the microphone. If recording began while the
 * assistant was still talking, the captured clip could contain the assistant
 * saying the value, the unit, and the confirmation word, both transcription
 * readers would agree, and a measurement would be committed that no person ever
 * confirmed.
 *
 * Closing that requires knowing when *this specific* utterance has finished,
 * which needs event correlation rather than a delay:
 *
 * - `response.create` is sent; the next `response.created` binds the waiter to
 *   that `response.id`. Only a `response.done` carrying the same id can satisfy
 *   it, so an unrelated response - a tool-call follow-up, say - cannot release
 *   it early. `response.id` correlation on both events is documented behaviour.
 * - Generation finishing is not the same as playback finishing, so the waiter
 *   additionally requires the output audio to be quiet. That is established
 *   from whichever signals the session actually has: the documented
 *   `response.output_audio.done`, the WebRTC `output_audio_buffer.stopped`
 *   (which is undocumented, has reported delivery delays, and is absent from
 *   recent API versions - so it is treated as corroboration, never as the sole
 *   gate), and a level meter on the remote track where the browser provides
 *   one.
 * - Only then does a short quiet settle window run. A settle window *after*
 *   confirmed completion is a debounce. A settle window *instead of* one would
 *   be a guess, and is exactly what this replaces.
 *
 * Anything other than a clean completion - a provider error, an interruption, a
 * timeout, a closed connection - **rejects**. The caller must not record.
 */

export interface RealtimeToolCall {
  callId: string;
  name: string;
  parameters: Record<string, string>;
}

/**
 * What the session is doing right now, derived from provider events rather than
 * assumed.
 */
export type RealtimeActivity =
  | "listening"
  | "operator_speaking"
  | "processing"
  | "speaking";

export interface RealtimeCallbacks {
  onStatusChange: (status: RealtimeConnectionStatus) => void;
  onActivity: (activity: RealtimeActivity) => void;
  onToolCall: (call: RealtimeToolCall) => Promise<Record<string, unknown>>;
  onTranscript: (text: string, role: "operator" | "assistant") => void;
  onError: (message: string) => void;
}

export type RealtimeConnectionStatus =
  | "connecting"
  | "connected"
  | "interrupted"
  | "closed"
  | "failed";

/**
 * Why a readback did not complete cleanly. Never "it probably finished".
 *
 * The provider-status reasons mirror the documented final states of
 * `response.done`, which is emitted for *every* terminal outcome and so is not
 * by itself evidence of success.
 */
export type ReadbackFailureReason =
  | "interrupted"
  | "provider_error"
  | "timeout"
  | "closed"
  | "cancelled"
  | "failed"
  | "incomplete"
  | "unknown_status"
  | "playback_unverifiable"
  | "busy";

export class ReadbackAbandoned extends Error {
  public constructor(
    public readonly reason: ReadbackFailureReason,
    message: string,
  ) {
    super(message);
    this.name = "ReadbackAbandoned";
  }
}

/** Recording was requested at a moment when it could have captured the assistant. */
export class RecordingRefused extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "RecordingRefused";
  }
}

/**
 * The outgoing microphone track could not be detached or reattached.
 *
 * Detaching is what keeps the provider from hearing - and answering - the
 * operator's confirmation while it is being recorded. If it cannot be done, the
 * confirmation does not happen; the on-screen control is still there.
 */
export class MicrophoneIsolationFailed extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "MicrophoneIsolationFailed";
  }
}

/**
 * What a playback monitor can currently tell us.
 *
 * The distinction that matters is between *silence* and *no opinion*. The
 * previous interface collapsed them: `isAudible()` returned `false` when no
 * analyser existed, so a browser that could not measure anything looked exactly
 * like a browser reporting a quiet speaker. Confirmation would then proceed on
 * the strength of provider events, which describe streaming completion and say
 * nothing about audio still buffered in the local output device.
 */
export type PlaybackMonitorState =
  | "unavailable"
  | "initializing"
  | "running"
  | "suspended"
  | "failed"
  | "closed";

/**
 * Reports whether the assistant's audio is actually audible right now.
 *
 * Injected so the turn boundary can be tested without a real audio pipeline.
 * A monitor that is not `running` has no opinion, and a confirmation that
 * cannot positively observe silence must fail closed rather than guess.
 */
export interface PlaybackMonitor {
  /**
   * Create the audio graph. Called from the operator's own gesture, because
   * that is the only context in which resuming an AudioContext is permitted.
   */
  prepare(): Promise<void>;
  attach(stream: MediaStream): void;
  /** True only when the monitor positively observes sound. */
  isAudible(): boolean;
  readonly state: PlaybackMonitorState;
  close(): void;
}

export interface RealtimeSessionPort {
  connect(grant: RealtimeGrant): Promise<void>;
  setMuted(muted: boolean): void;
  interrupt(): void;
  /**
   * Voice one exact sentence, resolving only once it has finished being spoken.
   *
   * Correlated to its own response through `response.create` metadata, and
   * satisfied only by a `response.done` whose status is `completed` *and* whose
   * audio a working playback monitor has positively observed to be silent.
   *
   * Rejects with {@link ReadbackAbandoned} on any other outcome. A rejection
   * means the caller must not record.
   */
  say(text: string): Promise<void>;
  /**
   * Speak something that is not a turn boundary, best effort.
   *
   * Used for reading an outcome back after the fact. Deliberately a separate
   * method: it opens no waiter and makes no promise about silence, so it can
   * never be mistaken for the safety-critical path.
   */
  announce(text: string): Promise<void>;
  /** True while the assistant may still be audible. */
  readonly assistantOutputActive: boolean;
  /** True while the outgoing track is detached from the provider. */
  readonly outgoingAudioSuppressed: boolean;
  /**
   * Stop forwarding microphone audio to the provider, keeping the track live.
   *
   * Awaitable, and rejects with {@link MicrophoneIsolationFailed} rather than
   * discarding the failure: whether the sender is really detached is a safety
   * question, not a nicety.
   */
  suppressOutgoingAudio(): Promise<void>;
  resumeOutgoingAudio(): Promise<void>;
  /** A short non-speech cue so the operator knows recording has begun. */
  playCaptureCue(): Promise<void>;
  /** Record a bounded clip. Refuses while the assistant may be audible. */
  captureUtterance(maxMilliseconds: number): Promise<Blob>;
  stop(): void;
  readonly isConnected: boolean;
}

/**
 * The lifetime over which the provider must not hear the microphone.
 *
 * Suppression used to be two bare calls in the controller, and the restore call
 * sat immediately after the readback - while the recorder was still open. The
 * transaction exists so the window has one owner and one exit: `release()` is
 * the only place forwarding comes back, and it runs on every path.
 */
export interface ConfirmationCapture {
  /**
   * Reattach the microphone. Never throws.
   *
   * Returns the isolation failure instead, because by the time this runs a
   * valid recording may already exist, and throwing away good evidence is a
   * worse outcome than reporting a degraded connection.
   */
  release(): Promise<MicrophoneIsolationFailed | null>;
}

/**
 * Detach the outgoing track and hand back the transaction that reattaches it.
 *
 * Rejects if the sender cannot be detached, and in that case nothing has been
 * spoken and nothing may be recorded.
 */
export async function openConfirmationCapture(
  port: RealtimeSessionPort,
): Promise<ConfirmationCapture> {
  await port.suppressOutgoingAudio();
  let released = false;
  return {
    release: async (): Promise<MicrophoneIsolationFailed | null> => {
      if (released) return null;
      released = true;
      try {
        await port.resumeOutgoingAudio();
        return null;
      } catch (caught) {
        return caught instanceof MicrophoneIsolationFailed
          ? caught
          : new MicrophoneIsolationFailed(
              "LabJarvis could not resume sending microphone audio to the voice service.",
            );
      }
    },
  };
}

export type RealtimeSessionFactory = (
  callbacks: RealtimeCallbacks,
) => RealtimeSessionPort;

const REALTIME_BASE_URL = "https://api.openai.com/v1/realtime/calls";

/** How long a readback may take before it is treated as stuck. */
const READBACK_TIMEOUT_MS = 15_000;
/** Quiet period after confirmed completion, to let a speaker stop ringing. */
const QUIET_SETTLE_MS = 400;
/** How often the quiet condition is re-evaluated while a waiter is armed. */
const SETTLE_POLL_MS = 50;

/**
 * The metadata key carrying our own correlation identifier.
 *
 * `response.create` accepts a `metadata` object, and the provider echoes it on
 * `response.created` and `response.done`. That is the documented way to tell
 * "the response I asked for" from "some response" - previously the waiter was
 * bound to whatever `response.created` arrived next, which could just as easily
 * have been an automatic VAD turn or a tool-call follow-up.
 */
const READBACK_METADATA_KEY = "labjarvis_readback_id";

interface ReadbackWaiter {
  /** Our identifier, echoed back by the provider. Keys the waiter map. */
  correlationId: string;
  /** Bound from the matching `response.created`; only this id may satisfy it. */
  responseId: string | null;
  /** Set by `response.done` when its status is exactly `completed`. */
  generationComplete: boolean;
  settleAt: number | null;
  resolve: () => void;
  reject: (error: ReadbackAbandoned) => void;
}

/** Final response states, from the documented `RealtimeResponse.status` union. */
const TERMINAL_FAILURE_REASONS: Record<string, ReadbackFailureReason> = {
  cancelled: "cancelled",
  failed: "failed",
  incomplete: "incomplete",
};

const FAILURE_MESSAGES: Record<ReadbackFailureReason, string> = {
  interrupted: "the readback was interrupted",
  provider_error: "the realtime provider reported an error",
  timeout: "the readback did not finish in time",
  closed: "the realtime connection closed",
  cancelled: "the readback was cancelled before it finished",
  failed: "the voice service could not produce the readback",
  incomplete: "the readback was cut short before it finished",
  unknown_status: "the readback ended in a state LabJarvis does not recognise",
  playback_unverifiable: "LabJarvis could not confirm that the speaker had gone quiet",
  busy: "another readback was already in progress",
};

let correlationCounter = 0;

function nextCorrelationId(): string {
  correlationCounter += 1;
  const random =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `readback-${correlationCounter}-${random}`;
}

/**
 * Level meter over the remote audio track, when the browser provides Web Audio.
 *
 * Two things changed here after the audit. The context is built in
 * {@link prepare}, called from `connect()` and therefore from the operator's
 * own click, because that is the only context in which `resume()` is allowed to
 * succeed - building it inside `ontrack` meant a suspended context that could
 * never be started. And the monitor now reports *why* it has no reading, so a
 * failure to measure is distinguishable from a measurement of silence.
 */
export class WebAudioPlaybackMonitor implements PlaybackMonitor {
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private samples: Float32Array<ArrayBuffer> | null = null;
  private status: PlaybackMonitorState = "unavailable";

  public get state(): PlaybackMonitorState {
    if (this.status !== "running") return this.status;
    // A context can be suspended by the browser after it was started - a tab
    // moving to the background does it - so the live state is what counts, not
    // the state at construction.
    const live = this.context?.state;
    if (live === "suspended") return "suspended";
    if (live === "closed") return "closed";
    return this.analyser === null ? "failed" : "running";
  }

  public async prepare(): Promise<void> {
    const Constructor =
      (globalThis as { AudioContext?: typeof AudioContext }).AudioContext ??
      (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Constructor === undefined) {
      // No Web Audio at all. Spoken confirmation will refuse; typed and
      // on-screen confirmation are unaffected.
      this.status = "unavailable";
      return;
    }
    this.status = "initializing";
    try {
      this.context = new Constructor();
    } catch {
      this.status = "failed";
      return;
    }
    if (this.context.state === "suspended") {
      try {
        // Only ever attempted here, inside the operator's gesture.
        await this.context.resume();
      } catch {
        this.status = "suspended";
        return;
      }
    }
    this.status = this.context.state === "running" ? "initializing" : "suspended";
  }

  public attach(stream: MediaStream): void {
    const context = this.context;
    if (context === null) return;
    try {
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      this.analyser = analyser;
      this.samples = new Float32Array(new ArrayBuffer(analyser.fftSize * 4));
      this.status = "running";
    } catch {
      // Constructing the graph is not proof it works, but failing to construct
      // it is proof it does not. Say so rather than silently having no opinion.
      this.analyser = null;
      this.samples = null;
      this.status = "failed";
    }
  }

  public isAudible(): boolean {
    const analyser = this.analyser;
    const samples = this.samples;
    if (analyser === null || samples === null) {
      // Never "no sound"; callers must consult `state` first. Erring towards
      // audible keeps a misuse from silently opening the recorder.
      return true;
    }
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    // A low threshold on purpose: the question is "has it definitely stopped",
    // and answering "yes" too eagerly is the failure this exists to prevent.
    return Math.sqrt(sum / samples.length) > 0.005;
  }

  public close(): void {
    this.analyser = null;
    this.samples = null;
    void this.context?.close?.();
    this.context = null;
    this.status = "closed";
  }
}

export class WebRtcRealtimeSession implements RealtimeSessionPort {
  private peer: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;
  private stream: MediaStream | null = null;
  private sender: RTCRtpSender | null = null;
  private audio: HTMLAudioElement | null = null;
  private stopped = false;

  /**
   * Which responses currently have audio in flight, by `response_id`.
   *
   * A set rather than a boolean because a single flag let *any* audio-done
   * event mark *every* response quiet: an unrelated response finishing could
   * clear the flag while the readback was still playing, and the recorder would
   * open over the top of it.
   */
  private readonly activeAudioResponses = new Set<string>();
  /** Audio known to be playing whose response could not be identified. */
  private unattributedOutput = false;
  private readonly waiters = new Map<string, ReadbackWaiter>();
  /** `response.id` -> correlation id, for events that carry only the former. */
  private readonly responseCorrelation = new Map<string, string>();
  private settlePoll: ReturnType<typeof globalThis.setInterval> | null = null;
  private recording = false;
  private abortRecording: ((error: RecordingRefused) => void) | null = null;
  private outgoingSuppressed = false;

  public constructor(
    private readonly callbacks: RealtimeCallbacks,
    private readonly monitor: PlaybackMonitor = new WebAudioPlaybackMonitor(),
    private readonly timings: { timeoutMs?: number; settleMs?: number } = {},
  ) {}

  public get isConnected(): boolean {
    return this.peer !== null && this.peer.connectionState === "connected";
  }

  public get assistantOutputActive(): boolean {
    // A pending readback counts as active: the sentence has been requested and
    // may begin at any moment, so recording is not safe yet either.
    if (this.waiters.size > 0) return true;
    if (this.activeAudioResponses.size > 0 || this.unattributedOutput) return true;
    // A monitor that cannot measure has no opinion here. It is not treated as
    // silence - `say()` refuses outright in that case - but neither is it
    // reported as the assistant being audible.
    return this.monitor.state === "running" && this.monitor.isAudible();
  }

  public get outgoingAudioSuppressed(): boolean {
    return this.outgoingSuppressed;
  }

  public async connect(grant: RealtimeGrant): Promise<void> {
    this.stopped = false;
    this.callbacks.onStatusChange("connecting");
    // Built here, inside the operator's own gesture, so a suspended
    // AudioContext can legitimately be resumed. Constructing it later, in
    // `ontrack`, produced a context that could never start.
    await this.monitor.prepare();

    // Permission is requested here, after the operator has already consented in
    // the UI. A denial is a normal outcome, not an error state: typed control
    // remains fully available.
    //
    // The audio constraints are defence in depth against the assistant's own
    // output re-entering the microphone. They are not the control that makes
    // confirmation safe - the turn boundary in `say()` is - because echo
    // cancellation is best-effort and varies by device.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const peer = new RTCPeerConnection();
    this.peer = peer;

    this.audio = document.createElement("audio");
    this.audio.autoplay = true;
    peer.ontrack = (event) => {
      const remote = event.streams[0] ?? null;
      if (this.audio !== null) this.audio.srcObject = remote;
      if (remote !== null) this.monitor.attach(remote);
    };

    for (const track of this.stream.getAudioTracks()) {
      this.sender = peer.addTrack(track, this.stream);
    }

    const channel = peer.createDataChannel("oai-events");
    this.channel = channel;
    channel.addEventListener("message", (event: MessageEvent<string>) => {
      void this.handleEvent(event.data);
    });

    peer.addEventListener("connectionstatechange", () => {
      if (this.stopped) return;
      if (peer.connectionState === "connected") {
        this.callbacks.onStatusChange("connected");
        this.callbacks.onActivity("listening");
      } else if (
        peer.connectionState === "disconnected" ||
        peer.connectionState === "failed"
      ) {
        // Any readback in flight cannot be known to have finished, so it fails
        // rather than resolving.
        this.abandonWaiters("closed", "the realtime connection dropped mid-sentence");
        this.callbacks.onStatusChange("interrupted");
      }
    });

    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);

    const response = await fetch(
      `${REALTIME_BASE_URL}?model=${encodeURIComponent(grant.model_name)}`,
      {
        method: "POST",
        body: offer.sdp ?? "",
        headers: {
          Authorization: `Bearer ${grant.client_secret}`,
          "Content-Type": "application/sdp",
        },
      },
    );
    if (!response.ok) {
      this.callbacks.onStatusChange("failed");
      throw new Error("The realtime provider refused the connection.");
    }
    await peer.setRemoteDescription({ type: "answer", sdp: await response.text() });
  }

  // ------------------------------------------------------------- event intake

  private async handleEvent(raw: string): Promise<void> {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = typeof event.type === "string" ? event.type : "";

    if (type === "input_audio_buffer.speech_started") {
      // Ordinary conversational barge-in only.
      //
      // During a confirmation the outgoing track is detached, so the provider
      // receives nothing and cannot legitimately report operator speech. An
      // event arriving in that window is stale or spurious, and acting on it
      // would mean claiming a barge-in detection the design cannot make. The
      // readback is non-interruptible while input is suppressed; speech over it
      // is simply never recorded, because recording has not started.
      if (this.outgoingSuppressed) return;
      this.callbacks.onActivity("operator_speaking");
      this.abandonWaiters(
        "interrupted",
        "you spoke while LabJarvis was still reading the proposal back",
      );
      if (this.assistantOutputActive) this.interrupt();
      return;
    }
    if (type === "input_audio_buffer.speech_stopped") {
      if (this.outgoingSuppressed) return;
      this.callbacks.onActivity("processing");
      return;
    }
    if (type === "response.created") {
      this.bindResponse(event);
      this.callbacks.onActivity("processing");
      return;
    }
    if (
      type === "output_audio_buffer.started" ||
      type === "response.output_audio.delta" ||
      type === "response.audio.delta"
    ) {
      this.markAudioStarted(this.responseIdOf(event));
      return;
    }
    if (type === "response.output_audio.done" || type === "response.audio.done") {
      // Documented, and carries `response_id`. Note it is also emitted when a
      // response is interrupted, incomplete, or cancelled, so it says only that
      // this response's audio stopped - never that it succeeded.
      this.markAudioStopped(this.responseIdOf(event), { documented: true });
      return;
    }
    if (type === "output_audio_buffer.stopped" || type === "output_audio_buffer.cleared") {
      // WebRTC-only, undocumented, with reported delivery delays and absent
      // from recent API versions. Corroboration, never the sole gate: it may
      // clear audio it can identify, and nothing else.
      this.markAudioStopped(this.responseIdOf(event), { documented: false });
      return;
    }
    if (type === "response.done") {
      this.completeResponse(event);
      this.callbacks.onActivity("listening");
      return;
    }

    if (type.endsWith("transcription.completed") || type.endsWith("transcript.done")) {
      const transcript = typeof event.transcript === "string" ? event.transcript : "";
      if (transcript) {
        this.callbacks.onTranscript(
          transcript,
          type.includes("input_audio") ? "operator" : "assistant",
        );
      }
      return;
    }

    if (type === "response.function_call_arguments.done") {
      await this.handleToolCall(event);
      return;
    }

    if (type === "error") {
      this.abandonWaiters("provider_error", "the realtime provider reported an error");
      this.callbacks.onError("The realtime provider reported an error.");
    }
  }

  /**
   * Read the response identifier from either shape the protocol uses.
   *
   * `response.created` and `response.done` nest it under `response.id`; the
   * WebRTC output-buffer events carry a flat `response_id`.
   */
  private responseIdOf(event: Record<string, unknown>): string | null {
    const nested = event.response;
    if (nested !== null && typeof nested === "object") {
      const id = (nested as { id?: unknown }).id;
      if (typeof id === "string" && id) return id;
    }
    const flat = event.response_id;
    return typeof flat === "string" && flat ? flat : null;
  }

  private async handleToolCall(event: Record<string, unknown>): Promise<void> {
    const callId = typeof event.call_id === "string" ? event.call_id : "";
    const name = typeof event.name === "string" ? event.name : "";
    if (!callId || !name) return;
    let parameters: Record<string, string> = {};
    if (typeof event.arguments === "string" && event.arguments.trim()) {
      try {
        const parsed: unknown = JSON.parse(event.arguments);
        if (parsed !== null && typeof parsed === "object") {
          parameters = Object.fromEntries(
            Object.entries(parsed as Record<string, unknown>).map(([key, value]) => [
              key,
              String(value),
            ]),
          );
        }
      } catch {
        // Malformed arguments are handled by the backend schema check, which
        // will reject them with a message the model can read aloud.
        parameters = {};
      }
    }
    const result = await this.callbacks.onToolCall({ callId, name, parameters });
    this.send({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        // Whatever the backend said, verbatim. The model does not get to
        // decide what happened.
        output: JSON.stringify(result),
      },
    });
    this.send({ type: "response.create" });
  }

  // ------------------------------------------------------- readback waiters

  /**
   * Bind a waiter to the response the provider just opened - but only if the
   * provider echoed *our* correlation identifier back.
   *
   * The previous implementation bound the oldest unbound waiter to whatever
   * `response.created` arrived next. An automatic VAD turn, or the follow-up
   * response after a tool call, would be adopted as the readback, and its
   * completion would then be taken as proof that the readback had finished.
   */
  private bindResponse(event: Record<string, unknown>): void {
    const correlationId = this.correlationIdOf(event);
    const responseId = this.responseIdOf(event);
    if (correlationId === null || responseId === null) return;
    const waiter = this.waiters.get(correlationId);
    // A response carrying an identifier we are not waiting on is not ours -
    // a late duplicate, or an echo after a timeout already cleaned up.
    if (waiter === undefined) return;
    if (waiter.responseId !== null) {
      if (waiter.responseId === responseId) return; // Duplicate delivery.
      // Two different responses claiming the same readback. Neither can be
      // trusted to be the one that will be spoken.
      this.failWaiter(
        waiter,
        "unknown_status",
        "the voice service opened two responses for one readback",
      );
      return;
    }
    waiter.responseId = responseId;
    this.responseCorrelation.set(responseId, correlationId);
  }

  /**
   * Apply a terminal `response.done` to the waiter that owns it.
   *
   * `response.done` is emitted for every final state, so its arrival is not
   * success. Only `completed` may satisfy a readback; `cancelled`, `failed`,
   * and `incomplete` reject, and anything else - a missing status, or a value
   * this build does not know - rejects too rather than being assumed benign.
   */
  private completeResponse(event: Record<string, unknown>): void {
    const waiter = this.waiterFor(event);
    // A `response.done` for anything else - a tool-call follow-up, or a
    // response this session did not request - is deliberately ignored.
    if (waiter === undefined) return;

    const status = this.statusOf(event);
    if (status === "completed") {
      waiter.generationComplete = true;
      this.evaluateWaiters();
      return;
    }
    const reason = status === null ? undefined : TERMINAL_FAILURE_REASONS[status];
    this.failWaiter(waiter, reason ?? "unknown_status");
  }

  /** Find the waiter an event belongs to, by metadata first and id second. */
  private waiterFor(event: Record<string, unknown>): ReadbackWaiter | undefined {
    const correlationId = this.correlationIdOf(event);
    if (correlationId !== null) return this.waiters.get(correlationId);
    const responseId = this.responseIdOf(event);
    if (responseId === null) return undefined;
    const mapped = this.responseCorrelation.get(responseId);
    return mapped === undefined ? undefined : this.waiters.get(mapped);
  }

  private correlationIdOf(event: Record<string, unknown>): string | null {
    const response = event.response;
    if (response === null || typeof response !== "object") return null;
    const metadata = (response as { metadata?: unknown }).metadata;
    if (metadata === null || typeof metadata !== "object") return null;
    const value = (metadata as Record<string, unknown>)[READBACK_METADATA_KEY];
    return typeof value === "string" && value ? value : null;
  }

  private statusOf(event: Record<string, unknown>): string | null {
    const response = event.response;
    if (response === null || typeof response !== "object") return null;
    const status = (response as { status?: unknown }).status;
    return typeof status === "string" && status ? status : null;
  }

  // ------------------------------------------------------- audio lifecycle

  private markAudioStarted(responseId: string | null): void {
    const wasQuiet = !this.assistantOutputActive;
    if (responseId === null) this.unattributedOutput = true;
    else this.activeAudioResponses.add(responseId);
    if (wasQuiet) this.callbacks.onActivity("speaking");
    // A recording open while the assistant starts speaking is contaminated by
    // definition. Stop it and discard the clip rather than upload a mixture.
    this.discardRecordingInProgress();
  }

  private markAudioStopped(
    responseId: string | null,
    { documented }: { documented: boolean },
  ): void {
    if (responseId !== null) {
      this.activeAudioResponses.delete(responseId);
      // Only a response-scoped event may clear the unattributed flag, and only
      // once nothing else is in flight.
      if (this.activeAudioResponses.size === 0) this.unattributedOutput = false;
    } else if (documented && this.activeAudioResponses.size === 0) {
      // A documented done event with no id can only speak for output we could
      // not attribute in the first place.
      this.unattributedOutput = false;
    }
    this.evaluateWaiters();
  }

  /**
   * Resolve any waiter whose response is complete and whose audio has been
   * quiet for the settle window.
   */
  private evaluateWaiters(): void {
    if (this.waiters.size === 0) {
      this.stopSettlePolling();
      return;
    }
    const now = Date.now();
    const settleMs = this.timings.settleMs ?? QUIET_SETTLE_MS;

    for (const waiter of [...this.waiters.values()]) {
      if (!waiter.generationComplete) continue;

      // Fail closed the moment the monitor stops being able to answer. A
      // suspended, failed, or closed context is not silence; it is the absence
      // of a reading, and the whole point of the settle gate is that provider
      // events do not describe locally buffered playback.
      if (this.monitor.state !== "running") {
        this.failWaiter(waiter, "playback_unverifiable");
        continue;
      }
      const responseAudioDone =
        waiter.responseId === null || !this.activeAudioResponses.has(waiter.responseId);
      const quiet = responseAudioDone && !this.unattributedOutput && !this.monitor.isAudible();
      if (!quiet) {
        // Audio came back before the window elapsed; start again.
        waiter.settleAt = null;
        continue;
      }
      if (waiter.settleAt === null) waiter.settleAt = now + settleMs;
      if (now >= waiter.settleAt) {
        this.removeWaiter(waiter);
        waiter.resolve();
      }
    }
    if (this.waiters.size > 0) this.startSettlePolling();
    else this.stopSettlePolling();
  }

  /** Reject one waiter and remove every mapping that referenced it. */
  private failWaiter(
    waiter: ReadbackWaiter,
    reason: ReadbackFailureReason,
    detail?: string,
  ): void {
    this.removeWaiter(waiter);
    waiter.reject(new ReadbackAbandoned(reason, detail ?? FAILURE_MESSAGES[reason]));
  }

  private startSettlePolling(): void {
    if (this.settlePoll !== null) return;
    this.settlePoll = globalThis.setInterval(() => this.evaluateWaiters(), SETTLE_POLL_MS);
  }

  private stopSettlePolling(): void {
    if (this.settlePoll === null) return;
    globalThis.clearInterval(this.settlePoll);
    this.settlePoll = null;
  }

  /** Drop a waiter and every mapping that pointed at it. */
  private removeWaiter(waiter: ReadbackWaiter): void {
    this.waiters.delete(waiter.correlationId);
    if (waiter.responseId !== null) this.responseCorrelation.delete(waiter.responseId);
    if (this.waiters.size === 0) this.stopSettlePolling();
  }

  /** Fail every readback in flight. Never used to resolve one. */
  private abandonWaiters(reason: ReadbackFailureReason, detail: string): void {
    const abandoned = [...this.waiters.values()];
    this.waiters.clear();
    this.responseCorrelation.clear();
    this.stopSettlePolling();
    for (const waiter of abandoned) {
      waiter.reject(new ReadbackAbandoned(reason, detail));
    }
  }

  public say(text: string): Promise<void> {
    if (this.stopped || this.channel === null || this.channel.readyState !== "open") {
      return Promise.reject(
        new ReadbackAbandoned("closed", "the realtime connection is not open"),
      );
    }
    // Fail closed before speaking a word. Without a working level meter there
    // is no way to establish that the speaker has actually gone quiet -
    // provider events describe streaming, not the local output buffer - so the
    // recorder could open over audio that is still playing.
    if (this.monitor.state !== "running") {
      return Promise.reject(
        new ReadbackAbandoned(
          "playback_unverifiable",
          FAILURE_MESSAGES.playback_unverifiable,
        ),
      );
    }
    // Explicit readbacks are serialised. Concurrency here buys nothing a bench
    // operator needs and makes the quiet gate ambiguous, since two responses
    // could be audible at once.
    if (this.waiters.size > 0) {
      return Promise.reject(new ReadbackAbandoned("busy", FAILURE_MESSAGES.busy));
    }

    const correlationId = nextCorrelationId();
    return new Promise<void>((resolve, reject) => {
      const waiter: ReadbackWaiter = {
        correlationId,
        responseId: null,
        generationComplete: false,
        settleAt: null,
        resolve,
        reject,
      };
      this.waiters.set(correlationId, waiter);

      const timeout = globalThis.setTimeout(() => {
        const current = this.waiters.get(correlationId);
        if (current !== waiter) return;
        // A stuck readback is a failure, not permission to start recording.
        this.failWaiter(waiter, "timeout");
      }, this.timings.timeoutMs ?? READBACK_TIMEOUT_MS);

      const settle = () => globalThis.clearTimeout(timeout);
      waiter.resolve = () => {
        settle();
        resolve();
      };
      waiter.reject = (error) => {
        settle();
        reject(error);
      };

      this.send({
        type: "response.create",
        response: {
          // Echoed back on response.created and response.done, which is how
          // this readback is told apart from any other response the session
          // happens to produce.
          metadata: { [READBACK_METADATA_KEY]: correlationId },
          instructions:
            "Read the following to the operator exactly, word for word. " +
            `Add nothing and omit nothing: ${text}`,
        },
      });
      this.startSettlePolling();
    });
  }

  /**
   * Speak something that is not a turn boundary.
   *
   * Reading an outcome back is courtesy, not evidence: the authoritative
   * wording is already on screen. It opens no waiter, so it cannot be mistaken
   * for the safety-critical path and cannot be blocked by a browser with no
   * working level meter.
   */
  public announce(text: string): Promise<void> {
    if (this.stopped || this.channel === null || this.channel.readyState !== "open") {
      return Promise.resolve();
    }
    this.send({
      type: "response.create",
      response: {
        instructions:
          "Read the following to the operator exactly, word for word. " +
          `Add nothing and omit nothing: ${text}`,
      },
    });
    return Promise.resolve();
  }

  // ------------------------------------------------------------- microphone

  private send(payload: Record<string, unknown>): void {
    if (this.channel === null || this.channel.readyState !== "open") return;
    this.channel.send(JSON.stringify(payload));
  }

  /** Mute locally by disabling the track, so it works even if the peer is stuck. */
  public setMuted(muted: boolean): void {
    for (const track of this.stream?.getAudioTracks() ?? []) {
      track.enabled = !muted;
    }
  }

  /**
   * Stop sending microphone audio to the provider without closing the track.
   *
   * `replaceTrack(null)` is used rather than `track.enabled = false` because the
   * local track has to stay live: it is what the confirmation recording is
   * taken from moments later. Disabling it would hand the recorder silence.
   *
   * The point is that while the assistant is speaking, the provider receives no
   * input at all, so the assistant's own voice cannot be re-ingested as an
   * operator turn.
   */
  public async suppressOutgoingAudio(): Promise<void> {
    const sender = this.sender;
    if (sender?.replaceTrack === undefined) {
      // Previously the promise was discarded with `void`, so a missing sender
      // or a rejected replaceTrack looked exactly like success and the
      // confirmation proceeded with the provider still listening.
      throw new MicrophoneIsolationFailed(
        "LabJarvis could not stop sending microphone audio to the voice service.",
      );
    }
    try {
      await sender.replaceTrack(null);
    } catch (caught) {
      throw new MicrophoneIsolationFailed(
        `LabJarvis could not stop sending microphone audio to the voice service: ${
          caught instanceof Error ? caught.message : "the browser refused"
        }`,
      );
    }
    this.outgoingSuppressed = true;
  }

  public async resumeOutgoingAudio(): Promise<void> {
    const sender = this.sender;
    const track = this.stream?.getAudioTracks()[0] ?? null;
    if (sender?.replaceTrack === undefined || track === null) {
      // Stopping or replacing the connection during a confirmation lands here.
      // There is nothing left to restore, and nothing to report.
      this.outgoingSuppressed = false;
      return;
    }
    try {
      await sender.replaceTrack(track);
    } catch (caught) {
      throw new MicrophoneIsolationFailed(
        `LabJarvis could not resume sending microphone audio to the voice service: ${
          caught instanceof Error ? caught.message : "the browser refused"
        }`,
      );
    }
    this.outgoingSuppressed = false;
  }

  /** Barge-in: cut off the assistant's current utterance immediately. */
  public interrupt(): void {
    this.send({ type: "response.cancel" });
    this.send({ type: "output_audio_buffer.clear" });
    this.activeAudioResponses.clear();
    this.unattributedOutput = false;
    if (this.audio !== null) {
      // Detaching the element stops audio already buffered locally, which
      // cancelling the response on the provider side does not.
      this.audio.pause();
      this.audio.srcObject = null;
    }
    this.callbacks.onActivity("listening");
  }

  /**
   * A short tone marking the start of recording.
   *
   * Deliberately not speech: a spoken cue could itself be transcribed, and the
   * whole problem being solved is the assistant's words reaching the microphone.
   */
  public playCaptureCue(): Promise<void> {
    const Constructor =
      (globalThis as { AudioContext?: typeof AudioContext }).AudioContext ??
      (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Constructor === undefined) return Promise.resolve();
    try {
      const context = new Constructor();
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.frequency.value = 880;
      gain.gain.value = 0.04;
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start();
      return new Promise<void>((resolve) => {
        globalThis.setTimeout(() => {
          oscillator.stop();
          void context.close?.();
          resolve();
        }, 120);
      });
    } catch {
      return Promise.resolve();
    }
  }

  /**
   * Record a bounded clip from the live microphone.
   *
   * Refuses while the assistant may still be audible. That refusal is the
   * invariant the confirmation design rests on: a clip captured during playback
   * could contain the assistant reading the value and the confirmation word
   * aloud, which both transcription readers would then agree on.
   */
  public captureUtterance(maxMilliseconds: number): Promise<Blob> {
    const stream = this.stream;
    if (stream === null) {
      return Promise.reject(new RecordingRefused("The microphone is not open."));
    }
    if (this.assistantOutputActive) {
      return Promise.reject(
        new RecordingRefused(
          "LabJarvis was still speaking, so nothing was recorded.",
        ),
      );
    }
    if (this.recording) {
      return Promise.reject(
        new RecordingRefused("A recording is already in progress."),
      );
    }

    this.recording = true;
    const recorder = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    });
    const finished = new Promise<Blob>((resolve, reject) => {
      recorder.addEventListener("stop", () => {
        if (this.abortRecording === null) return; // Already rejected.
        resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      });
      // Armed for the whole interval. If the assistant starts speaking while
      // this recorder is open - which suppression should prevent, but which
      // must not be survivable if it happens - the clip is a mixture of two
      // voices and is discarded rather than uploaded.
      this.abortRecording = (error: RecordingRefused) => {
        this.abortRecording = null;
        chunks.length = 0;
        if (recorder.state !== "inactive") recorder.stop();
        reject(error);
      };
    });
    recorder.start();
    const timer = globalThis.setTimeout(() => {
      if (recorder.state !== "inactive") recorder.stop();
    }, maxMilliseconds);
    return finished.finally(() => {
      globalThis.clearTimeout(timer);
      this.recording = false;
      this.abortRecording = null;
    });
  }

  /** Stop and throw away a recording that the assistant has just talked over. */
  private discardRecordingInProgress(): void {
    if (!this.recording) return;
    this.abortRecording?.(
      new RecordingRefused(
        "LabJarvis started speaking while you were being recorded, so that recording was discarded.",
      ),
    );
  }

  public stop(): void {
    this.stopped = true;
    this.activeAudioResponses.clear();
    this.unattributedOutput = false;
    this.abandonWaiters("closed", "hands-free voice was stopped");
    // A recording still open when the session ends is evidence of nothing.
    this.abortRecording?.(new RecordingRefused("hands-free voice was stopped."));
    this.stopSettlePolling();
    this.monitor.close();
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    this.sender = null;
    this.channel?.close();
    this.channel = null;
    this.peer?.close();
    this.peer = null;
    if (this.audio !== null) {
      this.audio.srcObject = null;
      this.audio = null;
    }
    this.callbacks.onStatusChange("closed");
  }
}
