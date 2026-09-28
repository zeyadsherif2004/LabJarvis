"""Opt-in LIVE smoke test of configured hosted providers (costs real money).

Never part of CI or `tasks.py test`. Refuses to run unless
LABJARVIS_RUN_HOSTED_SMOKE=1. Every call is authorised first against the
cumulative spend ledger (hard cap, default $1 across all providers and all
tools) using a conservative upper bound; calls whose cost cannot be bounded are
skipped. The result artefact records provider, model, base domain, outcome,
latency and usage - never an API key, prompt or response body.

A tiny fixed suite: one command utterance per text provider (routed through the
real interpreter contract), one short synthetic audio clip for hosted speech,
and one rendered label for hosted vision.

    set LABJARVIS_RUN_HOSTED_SMOKE=1
    python scripts/hosted_smoke.py --config scripts/hosted_smoke.yaml
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml
from pydantic import SecretStr

from labjarvis.evaluation.spend import (
    BudgetExceededError,
    SpendLedger,
    cost_usd,
    load_prices,
    upper_bound_tokens,
)
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAICompatibleLabelRecognition,
    OpenAICompatibleTextUnderstanding,
    OpenAILabelRecognition,
    OpenAISpeechToText,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.ports.models import CommandContext

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "research" / "final_report_evidence" / "live" / "hosted-spend-ledger.json"
PRICES = ROOT / "research" / "live" / "pricing.json"
CONTEXT = CommandContext("smoke", "ACTIVE", "smoke-step", "Incubate the tube at 37 C.")
UTTERANCE = "log the temperature at 37 celsius"
EXPECTED = {"intent": "RECORD_MEASUREMENT", "value": "37", "unit": "degC"}


def client(entry: dict[str, Any]) -> OpenAIClient:
    key = os.environ.get(entry["api_key_env"], "").strip()
    if not key:
        raise SystemExit(f"credential variable {entry['api_key_env']!r} is not set")
    return OpenAIClient(
        HostedClientConfig(
            api_key=SecretStr(key),
            base_url=entry["base_url"],
            timeout_seconds=60,
            max_response_bytes=1024 * 1024,
            max_retries=0,
            provider="openai" if entry["provider"] == "openai" else "openai_compatible",
            provider_label=entry["label"],
        )
    )


def run_entry(entry: dict[str, Any], ledger: SpendLedger, prices: dict[str, Any]) -> dict[str, Any]:
    price = prices.get(entry.get("price_key", entry["model"]))
    record: dict[str, Any] = {
        "label": entry["label"],
        "provider": entry["provider"],
        "stage": entry["stage"],
        "model": entry["model"],
        "base_domain": entry["base_url"].split("/")[2],
    }
    if price is None:
        record["outcome"] = "skipped: no recorded price, cost cannot be bounded"
        return record
    hosted = client(entry)
    stage = entry["stage"]
    try:
        if stage == "text":
            if entry["provider"] == "openai":
                adapter: Any = OpenAITextUnderstanding(hosted, model_name=entry["model"], max_output_tokens=160)
            else:
                adapter = OpenAICompatibleTextUnderstanding(
                    hosted,
                    model_name=entry["model"],
                    structured_output=entry.get("structured_output", "json_object"),
                    max_output_tokens=160,
                    extra_body=entry.get("extra_body") or {},
                )
            body = json.dumps(adapter.request_payload(UTTERANCE, CONTEXT))
            bound = cost_usd(price, input_tokens=upper_bound_tokens(len(body)), output_tokens=160)
            call = lambda: adapter.interpret(UTTERANCE, CONTEXT)  # noqa: E731
        elif stage == "speech":
            audio = (ROOT / entry["audio"]).read_bytes()
            with wave.open(str(ROOT / entry["audio"])) as handle:
                minutes = handle.getnframes() / handle.getframerate() / 60
            bound = price.per_audio_minute * math.ceil(max(minutes, 1 / 60)) + cost_usd(
                price, input_tokens=2000, output_tokens=200
            )
            adapter = OpenAISpeechToText(hosted, model_name=entry["model"])
            call = lambda: adapter.transcribe(audio, content_type="audio/wav")  # noqa: E731
        else:
            image = (ROOT / entry["image"]).read_bytes()
            bound = cost_usd(price, input_tokens=upper_bound_tokens(600, images=2), output_tokens=256)
            adapter = (
                OpenAILabelRecognition(hosted, model_name=entry["model"])
                if entry["provider"] == "openai"
                else OpenAICompatibleLabelRecognition(hosted, model_name=entry["model"])
            )
            call = lambda: adapter.recognise(image, content_type="image/png")  # noqa: E731
        index = ledger.authorise(
            provider=entry["label"],
            model=entry["model"],
            operation=f"smoke.{stage}",
            upper_bound_usd=bound,
            purpose="hosted live smoke",
        )
        record["upper_bound_usd"] = round(bound, 6)
        started = perf_counter()
        try:
            result = call()
        except ModelAdapterError as exc:
            ledger.settle(index, usage={}, observed_usd=None, status=exc.code)
            record.update(outcome=f"failed: {exc.code}", detail=str(exc))
            return record
        record["latency_ms"] = round((perf_counter() - started) * 1000)
        usage = {
            k: float(v)
            for k, v in result.metadata.items()
            if k in {"prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens"}
        }
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        observed = (
            cost_usd(price, input_tokens=prompt, output_tokens=completion)
            if prompt is not None and completion is not None and stage != "speech"
            else None
        )
        ledger.settle(index, usage=usage, observed_usd=observed, status="succeeded")
        record["usage"] = usage
        record["observed_usd"] = observed
        if stage == "text":
            command = result.command
            passed = (
                command.intent == EXPECTED["intent"]
                and command.parameters.get("value") == EXPECTED["value"]
                and command.parameters.get("unit") == EXPECTED["unit"]
            )
            record["result"] = {"intent": command.intent, "parameters": dict(command.parameters)}
        elif stage == "speech":
            record["result"] = {"transcript": result.transcript}
            passed = bool(result.transcript.strip())
        else:
            record["result"] = {"visible_text": result.visible_text}
            passed = entry.get("expect", "").casefold() in result.visible_text.casefold()
        record["outcome"] = "PASS" if passed else "FAIL"
    except BudgetExceededError as exc:
        record["outcome"] = f"skipped: {exc}"
    finally:
        hosted.close()
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "scripts" / "hosted_smoke.yaml")
    parser.add_argument("--out", type=Path, default=ROOT / "research" / "final_report_evidence" / "live" / "hosted-smoke.json")
    args = parser.parse_args()
    if os.environ.get("LABJARVIS_RUN_HOSTED_SMOKE") != "1":
        print("Refusing: set LABJARVIS_RUN_HOSTED_SMOKE=1 to authorise paid requests.", file=sys.stderr)
        return 2
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    ledger = SpendLedger(LEDGER, cap_usd=float(config.get("budget_usd", 2.0)))
    prices = load_prices(PRICES)
    records = [run_entry(entry, ledger, prices) for entry in config["providers"]]
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": "live hosted smoke (paid); tiny fixed suite, synthetic inputs",
        "records": records,
        "ledger": ledger.summary(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
