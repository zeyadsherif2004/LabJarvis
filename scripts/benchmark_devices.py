"""Verify and benchmark Whisper and EasyOCR on GPU and CPU on this machine.

Loads each model through the production adapters, confirms which device was
actually used (not just requested), and measures cold (first call, includes
load) and warm (repeated) latency on the synthetic research media. Writes a
JSON record with the environment, device evidence and p50/p95 per device.

    python scripts/benchmark_devices.py --out research/final_report_evidence/devices/device-benchmark.json

Evidence class: synthetic media (TTS clips, rendered labels); the latency is
real, the inputs are not real-world captures.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEECH = sorted((ROOT / "research" / "data" / "media" / "speech").glob("speech-0*.wav"))
LABELS = sorted((ROOT / "research" / "data" / "media" / "vision").glob("label-0*.png"))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def _gpu_memory_mb() -> float | None:
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 1e6, 1)
    except ImportError:
        return None
    return None


def _nvidia_used_mb() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()  # fmt: skip
    except OSError:
        return ""


def bench_whisper(device: str, compute_type: str, samples: int, model: str) -> dict[str, Any]:
    from labjarvis.infrastructure.real_models import FasterWhisperSpeechToText

    adapter = FasterWhisperSpeechToText(
        model_name=model,
        device=device,
        compute_type=compute_type,
        cache_dir=ROOT / ".labjarvis" / "models" / "faster-whisper",
        allow_download=True,
    )
    latencies: list[float] = []
    transcripts: list[str] = []
    for path in SPEECH[: samples + 1]:
        started = perf_counter()
        result = adapter.transcribe(path.read_bytes(), content_type="audio/wav")
        latencies.append((perf_counter() - started) * 1000)
        transcripts.append(result.transcript)
    loaded = adapter._load()  # noqa: SLF001 - evidence of the device actually used
    actual = str(getattr(getattr(loaded, "model", None), "device", device))
    warm = latencies[1:]
    return {
        "model": model,
        "requested_device": device,
        "actual_device": actual,
        "compute_type": compute_type,
        "cold_ms": round(latencies[0], 1),
        "warm_p50_ms": round(statistics.median(warm), 1),
        "warm_p95_ms": round(_percentile(warm, 0.95), 1),
        "warm_n": len(warm),
        "example_transcript": transcripts[1] if len(transcripts) > 1 else "",
    }


def bench_easyocr(gpu: bool, samples: int) -> dict[str, Any]:
    from labjarvis.infrastructure.real_models import EasyOcrLabelRecognition

    adapter = EasyOcrLabelRecognition(
        languages=("en",),
        gpu=gpu,
        cache_dir=ROOT / ".labjarvis" / "models" / "easyocr",
        allow_download=True,
    )
    latencies: list[float] = []
    for path in LABELS[: samples + 1]:
        started = perf_counter()
        adapter.recognise(path.read_bytes(), content_type="image/png")
        latencies.append((perf_counter() - started) * 1000)
    reader = adapter._load()  # noqa: SLF001
    actual = str(getattr(reader, "device", "unknown"))
    warm = latencies[1:]
    return {
        "requested_gpu": gpu,
        "actual_device": actual,
        "cold_ms": round(latencies[0], 1),
        "warm_p50_ms": round(statistics.median(warm), 1),
        "warm_p95_ms": round(_percentile(warm, 0.95), 1),
        "warm_n": len(warm),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--whisper-model", default="small.en")
    args = parser.parse_args()
    import torch

    record: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "evidence_class": "synthetic media; real device latency",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "nvidia_smi": _nvidia_used_mb(),
        "results": {},
    }
    record["results"]["whisper_gpu"] = bench_whisper("cuda", "float16", args.samples, args.whisper_model)
    record["results"]["whisper_cpu"] = bench_whisper("cpu", "int8", args.samples, args.whisper_model)
    record["results"]["easyocr_gpu"] = bench_easyocr(True, args.samples)
    record["results"]["easyocr_gpu"]["torch_max_allocated_mb"] = _gpu_memory_mb()
    record["results"]["easyocr_cpu"] = bench_easyocr(False, args.samples)
    record["nvidia_smi_after"] = _nvidia_used_mb()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
