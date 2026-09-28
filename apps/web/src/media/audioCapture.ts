export interface AudioCapturePort {
  start(): Promise<void>;
  stop(): Promise<Blob>;
  /** Stop without producing a recording; the audio is discarded. */
  cancel?(): Promise<void>;
}

export class BrowserAudioCapture implements AudioCapturePort {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];

  public async start(): Promise<void> {
    if (this.recorder !== null) throw new Error("audio capture is already active");
    if (navigator.mediaDevices?.getUserMedia === undefined) {
      throw new DOMException("Microphone capture is unavailable", "NotSupportedError");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: false,
    });
    this.chunks = [];
    const preferred = "audio/webm;codecs=opus";
    this.recorder = MediaRecorder.isTypeSupported(preferred)
      ? new MediaRecorder(this.stream, { mimeType: preferred })
      : new MediaRecorder(this.stream);
    this.recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    });
    this.recorder.start();
  }

  public stop(): Promise<Blob> {
    const recorder = this.recorder;
    if (recorder === null) return Promise.resolve(new Blob([], { type: "audio/webm" }));
    return new Promise((resolve, reject) => {
      recorder.addEventListener(
        "stop",
        () => {
          const blob = new Blob(this.chunks, { type: recorder.mimeType || "audio/webm" });
          this.dispose();
          resolve(blob);
        },
        { once: true },
      );
      recorder.addEventListener(
        "error",
        () => {
          this.dispose();
          reject(new Error("audio recording failed"));
        },
        { once: true },
      );
      recorder.stop();
    });
  }

  public async cancel(): Promise<void> {
    const recorder = this.recorder;
    if (recorder === null) return;
    await new Promise<void>((resolve) => {
      recorder.addEventListener("stop", () => resolve(), { once: true });
      recorder.stop();
    });
    this.dispose();
  }

  private dispose(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
  }
}
