"""Explicitly download or inspect LabJarvis local model dependencies."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from importlib import import_module
from importlib.util import find_spec

from labjarvis.infrastructure.model_factory import build_model_adapters
from labjarvis.infrastructure.speech_output import KokoroSpeechSynthesis, download_kokoro
from labjarvis.settings import ModelMode, Settings


def local_settings(*, allow_download: bool) -> Settings:
    return Settings(model_mode=ModelMode.LOCAL, allow_model_download=allow_download)


def check_models() -> int:
    settings = local_settings(allow_download=False)
    adapters = build_model_adapters(settings)
    try:
        statuses = {
            "speech": asdict(adapters.speech.readiness()),
            "text": asdict(adapters.text.readiness()),
            "vision": asdict(adapters.labels.readiness()),
            "voice_output": asdict(
                KokoroSpeechSynthesis(cache_dir=settings.model_cache_dir / "kokoro").readiness()
            ),
        }
    finally:
        adapters.close()
    print(json.dumps(statuses, indent=2, sort_keys=True))
    return 0 if all(status["ready"] for status in statuses.values()) else 1


def download_models() -> int:
    settings = local_settings(allow_download=True)
    settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        whisper_utils = import_module("faster_whisper.utils")
        download_model = whisper_utils.download_model
        speech_path = download_model(
            settings.speech_model_name,
            cache_dir=str(settings.model_cache_dir / "faster-whisper"),
        )
        print(f"Speech model ready at {speech_path}")

        easyocr = import_module("easyocr")
        easyocr.Reader(
            list(settings.vision_languages),
            gpu=settings.vision_gpu,
            model_storage_directory=str(settings.model_cache_dir / "easyocr"),
            download_enabled=True,
            recog_network=settings.vision_model_name,
            verbose=False,
        )
        print(f"Vision model ready in {settings.model_cache_dir / 'easyocr'}")

        download_kokoro(settings.model_cache_dir / "kokoro")
        print(f"Voice (Kokoro) ready in {settings.model_cache_dir / 'kokoro'}")
    except (ImportError, AttributeError) as exc:
        print(
            "Model Python dependencies are missing. Run: "
            ".venv/Scripts/python -m pip install -r apps/api/requirements-models.lock",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 2

    ollama = shutil.which("ollama")
    if ollama is None:
        print(
            "Ollama is missing. Install it from https://ollama.com/download, then rerun this command.",
            file=sys.stderr,
        )
        return 2
    pulled = subprocess.run([ollama, "pull", settings.text_model_name], check=False)
    if pulled.returncode != 0:
        print("Ollama could not pull the configured text model.", file=sys.stderr)
        return pulled.returncode
    return check_models()


def download_voice() -> int:
    """Only the Kokoro voice (about 350 MB, SHA-256 verified)."""

    if find_spec("kokoro_onnx") is None:
        print(
            "Kokoro is not installed. Run: "
            ".venv/Scripts/python -m pip install -r apps/api/requirements-models.lock",
            file=sys.stderr,
        )
        return 2
    download_kokoro(local_settings(allow_download=True).model_cache_dir / "kokoro")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "download", "kokoro"))
    arguments = parser.parse_args()
    if arguments.action == "kokoro":
        return download_voice()
    return check_models() if arguments.action == "check" else download_models()


if __name__ == "__main__":
    raise SystemExit(main())
