/**
 * Optional always-listening mode: a browser voice-activity detector (Silero VAD,
 * ONNX, run locally in the page) cuts speech segments, and each segment is sent
 * to the server with `require_wake_phrase`, which discards anything that does
 * not begin with the wake phrase before interpretation or retention.
 *
 * Safety and privacy behaviour:
 * - raw audio never leaves the browser except as a finished segment sent to the
 *   local API; nothing is stored unless raw-media retention is configured;
 * - segments that start while (or just after) LabJarvis speaks are never sent
 *   as commands or answers (echo guard); with barge-in they may only carry
 *   "stop", "mute" or "stop the alarm" (see HandsFreeListeningPanel);
 * - outside a session it switches itself off after `idleTimeoutMs`; while a
 *   session is open it stays on (ADR 0018);
 * - push-to-talk keeps working throughout.
 */

export interface HandsFreeListener {
  start(): Promise<void>;
  pause(): Promise<void>;
  destroy(): Promise<void>;
}

export interface HandsFreeOptions {
  onSegment: (audio: Blob) => void;
  onSpeechStart?: () => void;
  onError?: (message: string) => void;
}

/** Encode 16 kHz mono float samples as a 16-bit PCM WAV blob. */
export function encodeWav(samples: Float32Array, sampleRate = 16_000): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index] ?? 0));
    view.setInt16(44 + index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/**
 * Development-only seam for the zero-touch browser test: a page script may
 * install a factory that emits scripted speech segments instead of running the
 * ONNX detector on a real microphone. Ignored in production builds, where
 * `import.meta.env.DEV` is false and the branch is removed.
 */
type TestListenerFactory = (options: HandsFreeOptions) => Promise<HandsFreeListener>;

function testListenerFactory(): TestListenerFactory | null {
  // The type assertion is stripped before Vite rewrites import.meta.env.
  if ((import.meta as { env?: { DEV?: boolean } }).env?.DEV !== true) return null;
  const candidate = (globalThis as { __labjarvisTestListener?: unknown }).__labjarvisTestListener;
  return typeof candidate === "function" ? (candidate as TestListenerFactory) : null;
}

/** Load the detector lazily: the ONNX runtime is only fetched when enabled. */
export async function createHandsFreeListener(
  options: HandsFreeOptions,
): Promise<HandsFreeListener> {
  const scripted = testListenerFactory();
  if (scripted !== null) return scripted(options);
  const { MicVAD } = await import("@ricky0123/vad-web");
  const vad = await MicVAD.new({
    model: "v5",
    baseAssetPath: "/vad/",
    onnxWASMBasePath: "/vad/",
    startOnLoad: false,
    // A microphone that is unplugged or revoked ends its track; report it so
    // the panel can announce the problem and retry with backoff.
    getStream: async () => {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, autoGainControl: true, noiseSuppression: true },
      });
      for (const track of stream.getAudioTracks()) {
        track.addEventListener("ended", () => options.onError?.("the microphone stopped"));
      }
      return stream;
    },
    // Conservative segmentation: short bursts (a cough, a clink) are misfires.
    positiveSpeechThreshold: 0.6,
    negativeSpeechThreshold: 0.35,
    redemptionFrames: 12,
    minSpeechFrames: 6,
    onSpeechStart: () => options.onSpeechStart?.(),
    onSpeechEnd: (audio: Float32Array) => options.onSegment(encodeWav(audio)),
  } as never);
  return {
    start: () => vad.start(),
    pause: () => vad.pause(),
    destroy: () => vad.destroy(),
  };
}
