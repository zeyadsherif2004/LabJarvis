"""E3: real EasyOCR label reading + reagent decision policies A/B/C."""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import RESEARCH_ROOT, environment_record, load_manifest, reagent_decisions, write_raw

from labjarvis.evaluation.metrics import character_error_rate
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.real_models import EasyOcrLabelRecognition
from labjarvis.settings import Settings


def main() -> None:
    settings = Settings()
    adapter = EasyOcrLabelRecognition(
        languages=settings.vision_languages,
        gpu=False,
        cache_dir=settings.model_cache_dir / "easyocr",
        model_name=settings.vision_model_name,
    )
    labels = load_manifest("label-images.jsonl")
    records = []
    for label in labels:
        media_path = (RESEARCH_ROOT / "data" / "manifests" / label["media_path"]).resolve()
        image = media_path.read_bytes()
        record: dict[str, object] = {
            "label_id": label["id"],
            "expected_reagent": label["expected_reagent"],
            "printed_text": label["printed_text"],
            "variant": label["variant"],
            "truth": label["truth"],
        }
        try:
            started = perf_counter()
            result = adapter.recognise(image, content_type=label["content_type"])
            latency_ms = round((perf_counter() - started) * 1000)
            record.update(
                status="succeeded",
                ocr_text=result.visible_text,
                ocr_confidence=result.confidence,
                latency_ms=latency_ms,
                ocr_cer=character_error_rate(label["printed_text"], result.visible_text)
                if label["printed_text"]
                else None,
                decisions=reagent_decisions(result.visible_text, label["expected_reagent"]),
            )
        except ModelAdapterError as exc:
            record.update(
                status="failed",
                error_code=exc.code,
                decisions=reagent_decisions("", label["expected_reagent"]),
            )
        records.append(record)

    payload = {
        "experiment_id": "E3-vision",
        "vision_model": {
            "adapter": adapter.adapter_name,
            "model_name": settings.vision_model_name,
            "device": "cpu",
            "languages": list(settings.vision_languages),
        },
        "environment": environment_record(),
        "sample_count": len(records),
        "failure_count": sum(record.get("status") != "succeeded" for record in records),
        "records": records,
    }
    path = write_raw("e3-vision-results.json", payload)
    print(path)


if __name__ == "__main__":
    main()
