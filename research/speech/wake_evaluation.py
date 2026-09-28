"""False-wake / missed-wake evaluation of hands-free listening.

Evidence class: SYNTHETIC. 16 utterances addressed to "Jarvis" and 16 ambient
lab-bench remarks (two Windows TTS voices, research/data/media/wake), plus
noise-only segments (white and pink noise at three levels). Pipeline, as in
the application: Silero VAD (the same ``silero_vad_v5.onnx`` the browser loads,
run with onnxruntime) decides whether a segment contains speech; segments with
speech are transcribed by faster-whisper and accepted only if
``addressed_to_assistant`` finds the wake phrase at the start.

* missed wake = an addressed utterance that is not accepted;
* false wake = an ambient utterance or a noise segment that is accepted.

    python -m research.speech.wake_evaluation [--device cuda]
"""

from __future__ import annotations

import argparse
import io
import json
import wave
from pathlib import Path

import numpy as np
import onnxruntime

from labjarvis.domain.wake_phrase import addressed_to_assistant
from labjarvis.infrastructure.real_models import FasterWhisperSpeechToText

ROOT = Path(__file__).resolve().parents[2]
CLIPS = ROOT / "research" / "data" / "media" / "wake"
MODEL = ROOT / "apps" / "web" / "public" / "vad" / "silero_vad_v5.onnx"
OUT = ROOT / "research" / "final_report_evidence" / "hands-free"
RATE = 16_000
THRESHOLD = 0.6  # the browser listener's positiveSpeechThreshold


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as handle:
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
        width = handle.getsampwidth()
        channels = handle.getnchannels()
    samples = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.uint8).astype(np.float32)
    samples = samples / (32768.0 if width == 2 else 128.0)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        positions = np.linspace(0, len(samples) - 1, int(len(samples) * RATE / rate))
        samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
    return samples


def to_wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


class SileroVad:
    def __init__(self, model: Path) -> None:
        self.session = onnxruntime.InferenceSession(str(model), providers=["CPUExecutionProvider"])

    def max_probability(self, samples: np.ndarray) -> float:
        state = np.zeros((2, 1, 128), dtype=np.float32)
        best = 0.0
        for start in range(0, len(samples) - 512, 512):
            chunk = samples[start : start + 512].reshape(1, -1).astype(np.float32)
            output, state = self.session.run(
                None, {"input": chunk, "state": state, "sr": np.array(RATE, dtype=np.int64)}
            )
            best = max(best, float(output[0][0]))
        return best


def noise(kind: str, level: float, seconds: float = 3.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(int(RATE * seconds)).astype(np.float32)
    if kind == "pink":
        spectrum = np.fft.rfft(white)
        spectrum /= np.sqrt(np.arange(1, len(spectrum) + 1))
        white = np.fft.irfft(spectrum, n=len(white)).astype(np.float32)
        white /= np.max(np.abs(white)) + 1e-9
    return white * level


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    vad = SileroVad(MODEL)
    whisper = FasterWhisperSpeechToText(
        model_name="small.en",
        device=args.device,
        compute_type="float16" if args.device == "cuda" else "int8",
        cache_dir=ROOT / ".labjarvis" / "models" / "faster-whisper",
    )
    records = []
    items = [(path.name, "addressed" if path.name.startswith("addressed") else "ambient", load_wav(path))
             for path in sorted(CLIPS.glob("*.wav"))]  # fmt: skip
    for kind in ("white", "pink"):
        for index, level in enumerate((0.01, 0.05, 0.2)):
            items.append((f"noise-{kind}-{level}", "noise", noise(kind, level, seed=index)))
    for name, truth, samples in items:
        probability = vad.max_probability(samples)
        speech = probability >= THRESHOLD
        transcript = ""
        accepted = False
        if speech:
            try:
                transcript = whisper.transcribe(to_wav_bytes(samples), content_type="audio/wav").transcript
            except Exception:  # an empty transcript is simply not addressed
                transcript = ""
            accepted = addressed_to_assistant(transcript, "Jarvis")
        records.append({"clip": name, "truth": truth, "vad_max_probability": round(probability, 3),
                         "vad_speech": speech, "transcript": transcript, "accepted": accepted})  # fmt: skip
    addressed = [r for r in records if r["truth"] == "addressed"]
    ambient = [r for r in records if r["truth"] == "ambient"]
    noises = [r for r in records if r["truth"] == "noise"]
    summary = {
        "evidence_class": "synthetic TTS + generated noise",
        "addressed": len(addressed),
        "missed_wakes": sum(not r["accepted"] for r in addressed),
        "missed_by_vad": sum(not r["vad_speech"] for r in addressed),
        "ambient": len(ambient),
        "false_wakes_ambient": sum(r["accepted"] for r in ambient),
        "noise_segments": len(noises),
        "vad_triggers_on_noise": sum(r["vad_speech"] for r in noises),
        "false_wakes_noise": sum(r["accepted"] for r in noises),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wake-evaluation.json").write_text(json.dumps({"summary": summary, "records": records}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for record in records:
        if (record["truth"] == "addressed") != record["accepted"]:
            print("ERROR CASE", record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
