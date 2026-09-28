/**
 * Everything LabJarvis says or sounds goes through here (eyes-free output).
 *
 * - `speak` uses Kokoro, the local neural voice rendered by the LabJarvis
 *   server on this computer (ADR 0020), when one is set with `setNeuralVoice`;
 *   otherwise, and whenever Kokoro fails, the browser's own speech
 *   synthesiser. Kokoro speaks sentence by sentence, fetching the next
 *   sentence while the current one plays, so the first words start quickly.
 *   It is silent while muted but always remembers the text, so "say that
 *   again" works after an unmute.
 * - `stop` is barge-in: it cancels speech immediately.
 * - `playTone` is the timer alarm: a distinct two-note tone that still plays
 *   when speech is muted, because an elapsed timer must not be missed.
 * - `isSpeaking` covers speech *and* tones, so the listener's echo guard never
 *   sends LabJarvis' own sound as a command.
 * - `unlock` is called from the single user gesture (or at start-up in kiosk
 *   mode) so later speech and tones are allowed by the browser's autoplay rules;
 *   it reports whether audio can play without a gesture.
 *
 * Spoken text never contains the wake phrase: the server builds every prompt
 * that way, and this module does not add any words of its own beyond them.
 */

export type SpokenDetail = "brief" | "full";

/** Renders one sentence of speech as audio (a WAV from the local server). */
export interface NeuralVoice {
  synthesize(text: string, signal: AbortSignal): Promise<ArrayBuffer>;
}

/** After a Kokoro failure the browser voice is used for this long before retrying. */
export const NEURAL_RETRY_MS = 30_000;

/** Sentences, kept whole so each one is natural on its own. */
export function speechChunks(text: string): string[] {
  return text
    .split(/(?<=[.?!])\s+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

export interface SpeechOutput {
  speak(text: string): void;
  stop(): void;
  repeat(): void;
  playTone(): void;
  isSpeaking(): boolean;
  lastSpoken(): string | null;
  setMuted(muted: boolean): void;
  setDetail(detail: SpokenDetail): void;
  unlock(): Promise<boolean>;
  /** Kokoro when available; ``null`` speaks with the browser voice only. */
  setNeuralVoice?(voice: NeuralVoice | null): void;
}

/** The outcome, plus any sentence telling the operator what to say. */
export function briefText(text: string): string {
  const sentences = text.split(/(?<=[.?!])\s+/).filter((part) => part.trim() !== "");
  if (sentences.length <= 1) return text;
  const kept = [sentences[0]!];
  for (const sentence of sentences.slice(1)) {
    if (/\bsay\b|\bwhich\b|\?$/i.test(sentence)) kept.push(sentence);
  }
  return kept.join(" ");
}

export function createSpeechOutput(): SpeechOutput {
  let muted = false;
  let detail: SpokenDetail = "full";
  let last: string | null = null;
  let toneUntil = 0;
  let context: AudioContext | null = null;
  let neural: NeuralVoice | null = null;
  let neuralRetryAt = 0;
  // Every speak/stop starts a new generation; late audio from an older one is dropped.
  let generation = 0;
  let controller: AbortController | null = null;
  const sources = new Set<AudioBufferSourceNode>();
  let neuralBusy = false;
  let neuralUntil = 0;

  function synthesiser(): SpeechSynthesis | null {
    if (!("speechSynthesis" in globalThis) || !("SpeechSynthesisUtterance" in globalThis)) {
      return null;
    }
    return globalThis.speechSynthesis;
  }

  function audioContext(): AudioContext | null {
    if (context !== null) return context;
    const Constructor = (globalThis as { AudioContext?: typeof AudioContext }).AudioContext;
    if (Constructor === undefined) return null;
    try {
      context = new Constructor();
    } catch {
      context = null;
    }
    return context;
  }

  function speakWithBrowser(text: string): void {
    const synth = synthesiser();
    if (synth === null || text === "") return;
    synth.cancel();
    synth.speak(new SpeechSynthesisUtterance(text));
  }

  function cancelAll(): void {
    generation += 1;
    controller?.abort();
    controller = null;
    for (const source of sources) {
      try {
        source.stop();
      } catch {
        // Already finished.
      }
    }
    sources.clear();
    neuralBusy = false;
    neuralUntil = 0;
    synthesiser()?.cancel();
  }

  async function runningAudio(): Promise<AudioContext | null> {
    const audio = audioContext();
    if (audio === null) return null;
    if (audio.state === "suspended") {
      try {
        await Promise.race([audio.resume(), new Promise((resolve) => setTimeout(resolve, 300))]);
      } catch {
        return null;
      }
    }
    return audio.state === "running" ? audio : null;
  }

  /** Speak with Kokoro; anything not spoken when it fails goes to the browser voice. */
  async function speakWithNeural(voice: NeuralVoice, text: string, token: number): Promise<void> {
    const chunks = speechChunks(text);
    const audio = await runningAudio();
    if (token !== generation) return;
    if (audio === null || chunks.length === 0) {
      speakWithBrowser(text);
      return;
    }
    const abort = new AbortController();
    controller = abort;
    neuralBusy = true;
    const request = (chunk: string): Promise<ArrayBuffer> => {
      const pending = voice.synthesize(chunk, abort.signal);
      pending.catch(() => undefined); // awaited below; never an unhandled rejection
      return pending;
    };
    let next = request(chunks[0]!);
    let playhead = audio.currentTime;
    for (let index = 0; index < chunks.length; index += 1) {
      let decoded: AudioBuffer;
      try {
        const wav = await next;
        if (index + 1 < chunks.length) next = request(chunks[index + 1]!);
        decoded = await audio.decodeAudioData(wav);
      } catch {
        if (token !== generation) return;
        neuralRetryAt = Date.now() + NEURAL_RETRY_MS;
        neuralBusy = false;
        speakWithBrowser(chunks.slice(index).join(" "));
        return;
      }
      if (token !== generation) return;
      const source = audio.createBufferSource();
      source.buffer = decoded;
      source.connect(audio.destination);
      const start = Math.max(playhead, audio.currentTime);
      source.start(start);
      playhead = start + decoded.duration;
      neuralUntil = Date.now() + (playhead - audio.currentTime) * 1000;
      sources.add(source);
      source.onended = () => sources.delete(source);
    }
    if (token === generation) neuralBusy = false;
  }

  function say(text: string): void {
    cancelAll();
    const token = generation;
    if (neural !== null && Date.now() >= neuralRetryAt) {
      void speakWithNeural(neural, text, token);
    } else {
      speakWithBrowser(text);
    }
  }

  return {
    speak(text: string) {
      const spoken = detail === "brief" ? briefText(text) : text;
      last = spoken;
      if (muted) return;
      say(spoken);
    },
    stop() {
      cancelAll();
    },
    repeat() {
      if (last === null || muted) return;
      say(last);
    },
    setNeuralVoice(voice: NeuralVoice | null) {
      neural = voice;
      neuralRetryAt = 0;
    },
    playTone() {
      const audio = audioContext();
      if (audio === null) return;
      try {
        const start = audio.currentTime;
        for (const [offset, frequency] of [
          [0, 880],
          [0.25, 660],
          [0.5, 880],
        ] as const) {
          const oscillator = audio.createOscillator();
          const gain = audio.createGain();
          oscillator.frequency.value = frequency;
          gain.gain.value = 0.15;
          oscillator.connect(gain).connect(audio.destination);
          oscillator.start(start + offset);
          oscillator.stop(start + offset + 0.2);
        }
        toneUntil = Date.now() + 800;
      } catch {
        // A blocked or closed audio context: the spoken and on-screen alarm remain.
      }
    },
    isSpeaking() {
      return (
        neuralBusy ||
        Date.now() < neuralUntil ||
        synthesiser()?.speaking === true ||
        Date.now() < toneUntil
      );
    },
    lastSpoken() {
      return last;
    },
    setMuted(next: boolean) {
      muted = next;
      if (next) cancelAll();
    },
    setDetail(next: SpokenDetail) {
      detail = next;
    },
    async unlock() {
      const audio = audioContext();
      if (audio === null) return false;
      try {
        // Without a gesture (and without the kiosk flag) resume() can stay
        // pending indefinitely, so it is raced against a short timeout.
        if (audio.state === "suspended") {
          await Promise.race([
            audio.resume(),
            new Promise((resolve) => setTimeout(resolve, 500)),
          ]);
        }
      } catch {
        return false;
      }
      return audio.state === "running";
    },
  };
}
