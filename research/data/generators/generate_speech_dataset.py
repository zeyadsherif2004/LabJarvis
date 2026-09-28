"""Render the clearly-synthetic speech dataset with Windows SAPI voices.

The clips are machine-synthesised speech (Microsoft David/Zira desktop voices)
and are labelled and analysed as synthetic speech only; no human speakers were
recorded. A noise-augmented variant adds seeded Gaussian noise at a fixed
signal-to-noise ratio to probe transcription robustness.
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1]
MANIFEST = DATA_DIR / "manifests" / "speech-clips.jsonl"
MEDIA_DIR = DATA_DIR / "media" / "speech"
NOISE_SNR_DB = 5.0
SEED = 20260717

# (text_sample_id, spoken text). Texts mirror the text-command manifest so
# speech results chain into the interpretation pipeline.
UTTERANCES: list[tuple[str, str]] = [
    ("text-001", "Record the temperature as 37 degrees Celsius"),
    ("text-003", "Record the temperature as 21.5 degrees Celsius"),
    ("text-004", "Record the pH as 7.4 pH"),
    ("text-005", "Record the volume as 250 milliliters"),
    ("text-007", "Record the volume as 500 microliters"),
    ("text-008", "Record the mass as 5 grams"),
    ("text-009", "Record the mass as 120 milligrams"),
    ("text-010", "Record the speed as 1500 rpm"),
    ("text-011", "Record the concentration as 70 percent"),
    ("text-012", "Record the incubation time as 30 minutes"),
    ("text-016", "Record an observation as the solution is clear"),
    ("text-017", "Record an observation as precipitate formed at the bottom"),
    ("text-019", "Record an observation as no bubbles are visible"),
    ("text-022", "Set a timer for 5 minutes"),
    ("text-023", "Set timer for 90 seconds"),
    ("text-025", "Set a timer for 45 seconds"),
    ("text-027", "What is the current step"),
    ("text-029", "What is the next step"),
    ("text-031", "Repeat the instruction"),
    ("text-033", "Pause"),
    ("text-035", "Resume"),
    ("text-036", "Complete the step"),
    ("text-038", "Verify reagent"),
    ("text-040", "Correct the previous entry"),
    ("text-041", "End session"),
    ("text-043", "Jarvis, pause"),
    ("text-056", "Record the temperature as -300 degrees Celsius"),
    ("text-057", "Record the volume as 50 gallons"),
    ("text-064", "Skip all remaining steps"),
    ("text-074", "Record the temperature as 37 degrees Celsius and complete the step"),
]

VOICES = ["Microsoft David Desktop", "Microsoft Zira Desktop"]

RENDER_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$spec = Get-Content -Raw -Encoding UTF8 $args[0] | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($item in $spec) {
    $synth.SelectVoice($item.voice)
    $synth.Rate = $item.rate
    $synth.SetOutputToWaveFile($item.path)
    $synth.Speak($item.text)
    $synth.SetOutputToNull()
}
$synth.Dispose()
"""


def render_clips(spec: list[dict[str, object]]) -> None:
    spec_path = MEDIA_DIR / "_render-spec.json"
    script_path = MEDIA_DIR / "_render.ps1"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    script_path.write_text(RENDER_SCRIPT, encoding="utf-8")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            str(spec_path),
        ],
        check=True,
    )
    spec_path.unlink()
    script_path.unlink()


def add_noise(source: Path, target: Path, *, snr_db: float, seed: int) -> None:
    import numpy as np

    with wave.open(str(source), "rb") as reader:
        parameters = reader.getparams()
        frames = reader.readframes(reader.getnframes())
    signal = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    signal_power = float(np.mean(signal**2)) or 1.0
    noise_power = signal_power / (10 ** (snr_db / 10))
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, noise_power**0.5, size=signal.shape)
    noisy = np.clip(signal + noise, -32768, 32767).astype(np.int16)
    with wave.open(str(target), "wb") as writer:
        writer.setparams(parameters)
        writer.writeframes(noisy.tobytes())


def clip_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


def main() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    spec: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    for utterance_index, (text_id, text) in enumerate(UTTERANCES, start=1):
        for voice_index, voice in enumerate(VOICES):
            voice_tag = "david" if "David" in voice else "zira"
            clip_id = f"speech-{utterance_index:03d}-{voice_tag}"
            path = MEDIA_DIR / f"{clip_id}.wav"
            spec.append({"voice": voice, "rate": 0, "text": text, "path": str(path)})
            records.append(
                {
                    "id": clip_id,
                    "modality": "speech",
                    "synthetic": True,
                    "synthetic_speech": True,
                    "text_sample_id": text_id,
                    "expected_text": text,
                    "voice": voice,
                    "augmentation": "clean",
                    "media_path": f"../media/speech/{clip_id}.wav",
                    "content_type": "audio/wav",
                }
            )
            del voice_index

    render_clips(spec)

    # Noise-augment the David renderings of the first 15 utterances.
    noise_records = []
    for record in records:
        if record["augmentation"] != "clean" or "david" not in str(record["id"]):
            continue
        utterance_number = int(str(record["id"]).split("-")[1])
        if utterance_number > 15:
            continue
        source = MEDIA_DIR / f"{record['id']}.wav"
        clip_id = f"{record['id']}-noise"
        target = MEDIA_DIR / f"{clip_id}.wav"
        add_noise(source, target, snr_db=NOISE_SNR_DB, seed=SEED + utterance_number)
        noise_records.append(
            {
                **record,
                "id": clip_id,
                "augmentation": f"gaussian-noise-{NOISE_SNR_DB:g}dB-snr",
                "media_path": f"../media/speech/{clip_id}.wav",
            }
        )
    records.extend(noise_records)

    for record in records:
        record["duration_seconds"] = round(
            clip_duration(DATA_DIR / "manifests" / str(record["media_path"])), 3
        )

    with MANIFEST.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"wrote {len(records)} clips to {MANIFEST}")


if __name__ == "__main__":
    sys.exit(main())
