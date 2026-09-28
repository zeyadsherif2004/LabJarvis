import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NEURAL_RETRY_MS, createSpeechOutput, speechChunks, type NeuralVoice } from "./speechOutput";

class FakeSource {
  public buffer: unknown = null;
  public onended: (() => void) | null = null;
  public started: number | null = null;
  public stopped = false;
  connect() {
    return this;
  }
  start(at: number) {
    this.started = at;
  }
  stop() {
    this.stopped = true;
  }
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  public state = "running";
  public currentTime = 0;
  public destination = {};
  public sources: FakeSource[] = [];
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  decodeAudioData(wav: ArrayBuffer) {
    return Promise.resolve({ duration: wav.byteLength });
  }
  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
  createOscillator() {
    return { frequency: { value: 0 }, connect: (x: unknown) => x, start() {}, stop() {} };
  }
  createGain() {
    return { gain: { value: 0 }, connect: (x: unknown) => x };
  }
}

const browserSpeak = vi.fn();
const browserCancel = vi.fn();

beforeEach(() => {
  FakeAudioContext.instances = [];
  browserSpeak.mockReset();
  browserCancel.mockReset();
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("speechSynthesis", { speak: browserSpeak, cancel: browserCancel, speaking: false });
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      constructor(public text: string) {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function voice(synthesize: NeuralVoice["synthesize"]): NeuralVoice {
  return { synthesize };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("speech output with Kokoro", () => {
  it("splits responses into sentences", () => {
    expect(speechChunks("Measured 10 mL. Say confirm, or reject.")).toEqual([
      "Measured 10 mL.",
      "Say confirm, or reject.",
    ]);
  });

  it("speaks each sentence with Kokoro, back to back, and never uses the browser voice", async () => {
    const synthesize = vi.fn((text: string) => Promise.resolve(new ArrayBuffer(text.length)));
    const output = createSpeechOutput();
    output.setNeuralVoice?.(voice(synthesize));

    output.speak("Step 3 is complete. Say next.");
    await vi.waitFor(() => expect(FakeAudioContext.instances[0]?.sources).toHaveLength(2));

    expect(synthesize.mock.calls.map(([text]) => text)).toEqual([
      "Step 3 is complete.",
      "Say next.",
    ]);
    const [first, second] = FakeAudioContext.instances[0]!.sources;
    expect(first!.started).toBe(0);
    expect(second!.started).toBe("Step 3 is complete.".length); // queued after the first
    expect(output.isSpeaking()).toBe(true);
    expect(browserSpeak).not.toHaveBeenCalled();
  });

  it("falls back to the browser voice for what Kokoro could not say, then retries later", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const synthesize = vi.fn(() => Promise.reject(new Error("503")));
    const output = createSpeechOutput();
    output.setNeuralVoice?.(voice(synthesize));

    output.speak("Timer for 5 minutes started.");
    await vi.waitFor(() => expect(browserSpeak).toHaveBeenCalledTimes(1));
    expect((browserSpeak.mock.calls[0]![0] as { text: string }).text).toBe("Timer for 5 minutes started.");

    output.speak("Step 4.");
    await settle();
    expect(synthesize).toHaveBeenCalledTimes(1); // backing off: browser voice only
    expect(browserSpeak).toHaveBeenCalledTimes(2);

    vi.setSystemTime(Date.now() + NEURAL_RETRY_MS + 1);
    output.speak("Step 5.");
    await vi.waitFor(() => expect(synthesize).toHaveBeenCalledTimes(2));
  });

  it("stops at once: the request is aborted and playing audio is cut", async () => {
    let signal: AbortSignal | null = null;
    const synthesize = vi.fn((text: string, abort: AbortSignal) => {
      signal = abort;
      return text.startsWith("First")
        ? Promise.resolve(new ArrayBuffer(4))
        : new Promise<ArrayBuffer>(() => undefined);
    });
    const output = createSpeechOutput();
    output.setNeuralVoice?.(voice(synthesize));

    output.speak("First sentence. Second sentence.");
    await vi.waitFor(() => expect(FakeAudioContext.instances[0]?.sources).toHaveLength(1));
    output.stop();

    expect(signal!.aborted).toBe(true);
    expect(FakeAudioContext.instances[0]!.sources[0]!.stopped).toBe(true);
    expect(output.isSpeaking()).toBe(false);
    expect(browserSpeak).not.toHaveBeenCalled();
  });

  it("uses the browser voice when no Kokoro voice is set, and says nothing while muted", async () => {
    const output = createSpeechOutput();
    output.speak("Hello.");
    expect(browserSpeak).toHaveBeenCalledTimes(1);

    const synthesize = vi.fn(() => Promise.resolve(new ArrayBuffer(1)));
    output.setNeuralVoice?.(voice(synthesize));
    output.setMuted(true);
    output.speak("Quiet.");
    await settle();
    expect(synthesize).not.toHaveBeenCalled();
    expect(output.lastSpoken()).toBe("Quiet.");
  });
});
