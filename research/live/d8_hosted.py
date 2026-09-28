"""D8 (post-hoc, lower priority): hosted vision and full-chain deployment profiles.

Every hosted request goes through :class:`BudgetedClient`, which authorises the
request's worst case against the global cumulative ledger *before* it is sent
and settles it from the provider-reported usage afterwards - for text, vision
and speech alike, including calls made inside the grammar-first cascade.

Parts (see research/evidence/protocol-deviations.md, D8):

* ``vision`` - PROVISIONAL, SYNTHETIC: the 90 rendered labels, one frame each,
  read by hosted vision models; outcomes classified exactly as the EasyOCR
  single-frame policy in ``multiframe_ablation`` (same comparator, thresholds).
* ``chain`` - the six demo scenarios (four TTS utterances, two rendered labels)
  through the production service under three profiles: fully local, hybrid
  (local speech + vision, hosted DeepSeek text cascade) and fully hosted OpenAI
  (speech, text cascade and vision hosted).

    python -m research.live.d8_hosted vision --allow-paid
    python -m research.live.d8_hosted chain --allow-paid
"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import SecretStr

import labjarvis.infrastructure.model_factory as model_factory
from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.reagent import classify_reagent
from labjarvis.evaluation.spend import (
    BudgetExceededError,
    Price,
    SpendLedger,
    cost_usd,
    load_prices,
    upper_bound_tokens,
)
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    HostedResponse,
    OpenAIClient,
    OpenAICompatibleLabelRecognition,
    OpenAILabelRecognition,
)
from labjarvis.settings import ModelMode, Settings

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "research" / "final_report_evidence" / "live" / "hosted-spend-ledger.json"
PRICES = ROOT / "research" / "live" / "pricing.json"
OUT = ROOT / "research" / "final_report_evidence" / "live"
MANIFEST = ROOT / "research" / "data" / "manifests" / "label-images.jsonl"
IMAGE_TOKEN_BOUND = 6_000  # per image; generous for 1-2 tile label photos
MAX_OUTPUT_BOUND = 512


class BudgetedClient(OpenAIClient):
    """An OpenAI(-compatible) client that cannot spend past the global cap."""

    ledger: SpendLedger
    prices: dict[str, Price]
    purpose: str = "D8"

    def _price(self, model: str) -> Price:
        price = self.prices.get(model)
        if price is None:
            raise BudgetExceededError(f"no recorded price for {model}; refusing an unbounded call")
        return price

    def _settle(self, index: int, response: HostedResponse, price: Price, bound: float) -> None:
        usage = {k: float(v) for k, v in response.usage_metadata().items()}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        observed = (
            cost_usd(price, input_tokens=prompt, output_tokens=completion,
                     cached_input_tokens=usage.get("cached_input_tokens", 0.0))
            if prompt is not None and completion is not None
            else None
        )  # fmt: skip
        if observed is None and "usage_seconds" in usage:
            observed = price.per_audio_minute * usage["usage_seconds"] / 60
        self.ledger.settle(index, usage=usage, observed_usd=observed, status="succeeded")

    def post_json(self, path: str, payload: Mapping[str, Any]) -> HostedResponse:
        model = str(payload.get("model", ""))
        price = self._price(model)
        body = json.dumps(payload)
        images = body.count("data:image/")
        text_chars = len(body) - sum(len(part) for part in body.split('"')[1::2] if part.startswith("data:image/"))
        bound = cost_usd(
            price,
            input_tokens=upper_bound_tokens(text_chars) + IMAGE_TOKEN_BOUND * images,
            output_tokens=MAX_OUTPUT_BOUND,
        )
        index = self.ledger.authorise(
            provider=self.provider_label, model=model, operation=path.strip("/"),
            upper_bound_usd=bound, purpose=self.purpose,
        )  # fmt: skip
        try:
            response = super().post_json(path, payload)
        except Exception as exc:
            self.ledger.settle(index, usage={}, observed_usd=None, status=type(exc).__name__)
            raise
        self._settle(index, response, price, bound)
        return response

    def post_multipart(
        self,
        path: str,
        *,
        files: Mapping[str, tuple[str, bytes, str]],
        data: Mapping[str, str],
    ) -> HostedResponse:
        model = str(data.get("model", ""))
        price = self._price(model)
        audio_bytes = sum(len(item[1]) for item in files.values())
        minutes = max(1, math.ceil(audio_bytes / 32_000 / 60))  # >= 16 kHz 16-bit mono
        bound = price.per_audio_minute * minutes + cost_usd(
            price, input_tokens=2_000 * minutes, output_tokens=MAX_OUTPUT_BOUND
        )
        index = self.ledger.authorise(
            provider=self.provider_label, model=model, operation=path.strip("/"),
            upper_bound_usd=bound, purpose=self.purpose,
        )  # fmt: skip
        try:
            response = super().post_multipart(path, files=files, data=data)
        except Exception as exc:
            self.ledger.settle(index, usage={}, observed_usd=None, status=type(exc).__name__)
            raise
        self._settle(index, response, price, bound)
        return response


def budgeted(ledger: SpendLedger, purpose: str) -> type[BudgetedClient]:
    class Bound(BudgetedClient):
        pass

    Bound.ledger = ledger
    Bound.prices = load_prices(PRICES)
    Bound.purpose = purpose
    return Bound


def _key(name: str) -> SecretStr:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"credential variable {name!r} is not set")
    return SecretStr(value)


def run_vision(ledger: SpendLedger) -> dict[str, Any]:
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    models = [
        ("OpenAI", "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5.4-nano"),
        ("GLM", "openai_compatible", "https://api.z.ai/api/paas/v4", "GLM API Key", "glm-4.6v-flashx"),
    ]
    results: dict[str, Any] = {}
    for label, provider, base_url, env, model in models:
        client_type = budgeted(ledger, f"D8 hosted vision: {model}")
        client = client_type(
            HostedClientConfig(
                api_key=_key(env), base_url=base_url, timeout_seconds=60,
                max_response_bytes=1024 * 1024, max_retries=0, provider=provider,
                provider_label=label,
            )
        )  # fmt: skip
        adapter = (
            OpenAILabelRecognition(client, model_name=model)
            if provider == "openai"
            else OpenAICompatibleLabelRecognition(client, model_name=model)
        )
        records = []
        stopped = False
        for row in rows:
            image = (MANIFEST.parent / row["media_path"]).resolve().read_bytes()
            started = perf_counter()
            try:
                text = adapter.recognise(image, content_type="image/png").visible_text
                error = ""
            except BudgetExceededError:
                stopped = True
                break
            except Exception as exc:  # recorded, never hidden
                text, error = "", type(exc).__name__
            outcome = classify_reagent(
                text, row["expected_reagent"], match_threshold=92.0, uncertain_threshold=72.0
            ).outcome
            records.append({
                "id": row["id"], "truth": row["truth"], "variant": row["variant"],
                "printed": row["printed_text"], "visible_text": text, "error": error,
                "outcome": outcome.value, "ms": round((perf_counter() - started) * 1000),
            })  # fmt: skip
        client.close()
        classes = Counter(_classify(r["truth"], r["outcome"]) for r in records)
        matches = [r for r in records if r["truth"] == "match"]
        results[model] = {
            "provider": label,
            "items": len(records),
            "complete": not stopped and len(records) == len(rows),
            "counts": dict(sorted(classes.items())),
            "false_accepts": classes.get("false_accept", 0),
            "coverage_of_true_matches": round(
                sum(_classify(r["truth"], r["outcome"]) == "correct" for r in matches) / max(1, len(matches)), 3
            ),
            "errors": sum(bool(r["error"]) for r in records),
            "records": records,
        }  # fmt: skip
    return results


def _classify(truth: str, outcome: str) -> str:
    if outcome == VerificationOutcome.UNCERTAIN.value:
        return "uncertain"
    if truth == "match":
        return "correct" if outcome == VerificationOutcome.MATCH.value else "false_reject"
    return "false_accept" if outcome == VerificationOutcome.MATCH.value else "correct"


def run_chain(ledger: SpendLedger) -> dict[str, Any]:
    from research.real_media.run_chain import build_service, demo_rows
    from research.real_media.run_chain import run_labels as chain_labels
    from research.real_media.run_chain import run_speech as chain_speech
    from research.real_media.run_chain import summarise as chain_summary

    common = {
        "speech_device": "cuda", "speech_compute_type": "float16", "vision_gpu": True,
        "model_cache_dir": ROOT / ".labjarvis" / "models", "data_dir": ROOT / ".labjarvis",
    }  # fmt: skip
    profiles = {
        "fully_local": Settings(model_mode=ModelMode.LOCAL, **common),
        "hybrid_deepseek_text": Settings(
            model_mode=ModelMode.LOCAL, text_provider="openai_compatible", allow_hosted_models=True,
            hosted_text_base_url="https://api.deepseek.com", hosted_text_api_key=_key("Deepseek API key"),
            hosted_text_model_name="deepseek-flash", hosted_text_provider_label="DeepSeek",
            hosted_text_extra_body='{"thinking": {"type": "disabled"}}', **common,
        ),
        "fully_hosted_openai": Settings(
            model_mode=ModelMode.HOSTED, allow_hosted_models=True, hosted_api_key=_key("OPENAI_API_KEY"),
            hosted_speech_model_name="gpt-4o-mini-transcribe", hosted_text_model_name="gpt-5.4-nano",
            hosted_vision_model_name="gpt-5.4-nano", hosted_text_reasoning_effort="none",
            hosted_text_max_output_tokens=512, **common,
        ),
    }  # fmt: skip
    speech_rows, label_rows = demo_rows()
    results: dict[str, Any] = {}
    original = model_factory.OpenAIClient
    try:
        for name, settings in profiles.items():
            model_factory.OpenAIClient = budgeted(ledger, f"D8 chain: {name}")  # type: ignore[misc]
            service, adapters = build_service(settings)
            try:
                speech = chain_speech(service, speech_rows, confirm=True)
                labels = chain_labels(service, label_rows, frames=1)
            finally:
                adapters.close()
            results[name] = {
                "profile": settings.execution_profile(),
                "stages": {stage: settings.stage_locality(stage) for stage in ("speech", "text", "vision")},
                "speech": {"summary": chain_summary(speech, "total_ms"), "records": speech},
                "labels": {"summary": chain_summary(labels, "total_ms"), "records": labels},
                "reached": sum(bool(r.get("expected_reached")) for r in speech + labels),
                "scenarios": len(speech) + len(labels),
            }  # fmt: skip
    finally:
        model_factory.OpenAIClient = original  # type: ignore[misc]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", choices=("vision", "chain"))
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    if not args.allow_paid:
        raise SystemExit("refusing: D8 makes paid calls; pass --allow-paid")
    ledger = SpendLedger(LEDGER)
    before = ledger.committed_usd()
    results = run_vision(ledger) if args.part == "vision" else run_chain(ledger)
    after = ledger.committed_usd()
    payload = {
        "deviation": "D8 (post-hoc, recorded before running)",
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": (
            "PROVISIONAL SYNTHETIC: rendered labels, hosted vision"
            if args.part == "vision"
            else "synthetic TTS speech + rendered labels through the production service"
        ),
        "spend_usd": round(after - before, 6),
        "results": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"d8-{args.part}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    brief = {
        name: {k: v for k, v in value.items() if k not in {"records", "speech", "labels"}}
        for name, value in results.items()
    }
    print(json.dumps({"spend_usd": payload["spend_usd"], "results": brief}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
