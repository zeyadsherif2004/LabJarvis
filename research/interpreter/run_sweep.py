"""Run the predeclared text-interpreter model-selection sweep.

Each configuration is evaluated on the frozen 81-command benchmark and on the
held-out human-written paraphrase set, separately, through the production
adapters (v2 contract, grounding, cascade). Paid hosted calls go through the
cumulative spend ledger and stop before the global $2 cap could be exceeded.

    python -m research.interpreter.run_sweep --config research/interpreter/configs/final-sweep.yaml
    python -m research.interpreter.run_sweep --config ... --only qwen2.5-1.5b-cascade
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import httpx
import psutil
import yaml
from pydantic import SecretStr

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.models import ParsedCommand
from labjarvis.evaluation.spend import (
    Price,
    SpendLedger,
    cost_usd,
    load_prices,
    upper_bound_tokens,
)
from labjarvis.infrastructure.cascade import CascadeTextUnderstanding
from labjarvis.infrastructure.command_contract import CONTRACT_VERSION, schema_sha256
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAICompatibleTextUnderstanding,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.interpreter_spec import PROMPT_SHA256, PROMPT_VERSION
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.real_models import OllamaTextUnderstanding
from labjarvis.infrastructure.rule_grammar import RuleGrammarTextUnderstanding
from labjarvis.infrastructure.text_model_profiles import resolve_text_profile
from labjarvis.ports.models import (
    AdapterReadiness,
    CommandContext,
    TextResult,
    TextUnderstandingPort,
)
from research.experiments.common import (
    component_outcomes_for_command,
    semantically_equal_parameters,
)

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "research" / "data" / "manifests" / "text-commands.jsonl"
HELDOUT = ROOT / "research" / "data" / "heldout" / "human-paraphrases.jsonl"
HELDOUT_B = ROOT / "research" / "data" / "heldout" / "human-paraphrases-B.jsonl"
DEV = ROOT / "research" / "data" / "manifests" / "text-dev.jsonl"
RESULTS = ROOT / "research" / "interpreter" / "results"
LEDGER = ROOT / "research" / "final_report_evidence" / "live" / "hosted-spend-ledger.json"
PRICES = ROOT / "research" / "live" / "pricing.json"
GATES = (
    "proposal",
    "validation",
    "state",
    "confidence",
    "confirm_attentive",
    "confirm_always_accept",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        raw.setdefault("acceptable_intents", [raw["expected_intent"]])
        # The development split (used only for preflight checks) omits this.
        raw.setdefault("action_valid", raw["expected_intent"] != "UNSUPPORTED")
        rows.append(raw)
    return rows


class BudgetedModel:
    """Authorise each hosted call against the ledger before it is sent."""

    def __init__(
        self,
        inner: OpenAITextUnderstanding | OpenAICompatibleTextUnderstanding,
        *,
        ledger: SpendLedger,
        price: Price,
        provider: str,
        max_output_tokens: int,
        purpose: str,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._price = price
        self._provider = provider
        self._max_output = max_output_tokens
        self._purpose = purpose
        self.adapter_name = inner.adapter_name
        self.model_name = inner.model_name

    def interpret(self, transcript: str, context: CommandContext) -> TextResult:
        body = json.dumps(self._inner.request_payload(transcript, context))
        bound = cost_usd(
            self._price,
            input_tokens=upper_bound_tokens(len(body)),
            output_tokens=self._max_output,
        )
        index = self._ledger.authorise(
            provider=self._provider,
            model=self.model_name,
            operation="text.interpret",
            upper_bound_usd=bound,
            purpose=self._purpose,
        )
        try:
            result = self._inner.interpret(transcript, context)
        except ModelAdapterError as exc:
            self._ledger.settle(index, usage={}, observed_usd=None, status=exc.code)
            raise
        usage = {
            key: float(result.metadata[key])
            for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")
            if key in result.metadata
        }
        for key in ("cached_input_tokens", "reasoning_tokens"):
            if key in result.metadata:
                usage[key] = float(result.metadata[key])
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        observed = (
            cost_usd(
                self._price,
                input_tokens=prompt,
                output_tokens=completion,
                cached_input_tokens=usage.get("cached_input_tokens", 0.0),
            )
            if prompt is not None and completion is not None
            else None
        )
        self._ledger.settle(index, usage=usage, observed_usd=observed, status="succeeded")
        return result

    def readiness(self) -> AdapterReadiness:
        return self._inner.readiness()

    def close(self) -> None:
        self._inner.close()


def _secret(env_name: str) -> SecretStr:
    value = os.environ.get(env_name, "")
    if not value.strip():
        raise SystemExit(f"credential environment variable {env_name!r} is not set")
    return SecretStr(value.strip())


def build(config: Mapping[str, Any], ledger: SpendLedger | None) -> TextUnderstandingPort:
    kind = config["kind"]
    if kind == "rules":
        return RuleGrammarTextUnderstanding()
    provider = config["provider"]
    few_shot = bool(config.get("few_shot", True))
    model: TextUnderstandingPort
    if provider == "ollama":
        profile = resolve_text_profile(
            config["model"],
            think=config.get("think", "auto"),
            timeout_seconds=float(config.get("timeout_seconds", 20)),
            context_tokens=int(config.get("context_tokens", 4096)),
            max_output_tokens=int(config.get("max_output_tokens", 128)),
            few_shot=few_shot,
            structured_output=config.get("structured_output", "json_schema"),
        )
        model = OllamaTextUnderstanding(model_name=config["model"], profile=profile)
    else:
        if ledger is None:
            raise SystemExit("hosted configurations require the spend ledger")
        prices = load_prices(PRICES)
        price_key = config.get("price_key", config["model"])
        if price_key not in prices:
            raise SystemExit(f"no recorded price for {price_key}; refusing an unbounded call")
        client = OpenAIClient(
            HostedClientConfig(
                api_key=_secret(config["api_key_env"]),
                base_url=config["base_url"],
                timeout_seconds=float(config.get("timeout_seconds", 30)),
                max_response_bytes=1024 * 1024,
                max_retries=0,
                provider="openai" if provider == "openai" else "openai_compatible",
                provider_label=config.get("label", provider),
            )
        )
        max_output = int(config.get("max_output_tokens", 160))
        inner: OpenAITextUnderstanding | OpenAICompatibleTextUnderstanding
        if provider == "openai":
            inner = OpenAITextUnderstanding(
                client, model_name=config["model"], few_shot=few_shot,
                max_output_tokens=max_output,
                reasoning_effort=config.get("reasoning_effort"),
            )  # fmt: skip
        else:
            inner = OpenAICompatibleTextUnderstanding(
                client,
                model_name=config["model"],
                structured_output=config.get("structured_output", "json_object"),
                max_output_tokens=max_output,
                extra_body=config.get("extra_body") or {},
                few_shot=few_shot,
                reasoning_effort=config.get("reasoning_effort"),
            )
        model = BudgetedModel(
            inner,
            ledger=ledger,
            price=prices[price_key],
            provider=config.get("label", provider),
            max_output_tokens=max_output,
            purpose=f"model-selection sweep: {config['name']}",
        )
    if kind == "llm":
        return model
    verifier: TextUnderstandingPort | None = None
    if config.get("verifier_model"):
        verifier = OllamaTextUnderstanding(
            model_name=config["verifier_model"],
            profile=resolve_text_profile(
                config["verifier_model"],
                think=config.get("verifier_think", "auto"),
                timeout_seconds=float(config.get("timeout_seconds", 20)),
            ),
        )
    return CascadeTextUnderstanding(
        model=model,
        mode="agreement" if kind == "agreement" else "rules_first",
        verifier=verifier,
    )


def _route(result: TextResult | None, kind: str) -> str:
    if kind == "rules":
        return "rule" if result is not None and result.metadata.get("cascade_route") == "rule" else "rule_no_match"
    if kind == "llm":
        return "llm"
    return result.metadata.get("cascade_route", "unknown") if result is not None else "error"


def _llm_call_status(result: TextResult | None, error: str, kind: str) -> tuple[bool, bool]:
    """(llm_called, schema_valid) for this item.

    A call counts as schema-valid only if it returned output that parsed against
    the v2 union; timeouts and unavailability count as invalid (nothing usable).
    """

    if kind == "rules":
        return False, True
    if kind == "llm":
        return True, error == ""
    if result is None:
        return False, True
    called = result.metadata.get("llm_called") == "true"
    return called, called and not result.metadata.get("llm_failure")


def exact_match(command: ParsedCommand, row: Mapping[str, Any]) -> tuple[bool, bool]:
    """(intent_correct, exact_correct) using the row's acceptable intents."""

    canonical = validate_and_normalise_command(command)
    if canonical.intent not in set(row["acceptable_intents"]):
        return False, False
    if canonical.intent != row["expected_intent"]:
        # Alternative acceptable intents are parameterless.
        return True, not dict(canonical.parameters)
    expected = dict(row["expected_parameters"])
    if row["expected_intent"] in {"RECORD_MEASUREMENT", "SET_TIMER", "RECORD_OBSERVATION"} and not expected:
        # Benchmark paraphrases without labelled slots are scored on intent only.
        return True, False
    return True, semantically_equal_parameters(
        canonical.intent, dict(canonical.parameters), expected
    )


def evaluate(adapter: TextUnderstandingPort, rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    for index, row in enumerate(rows):
        context = CommandContext(
            session_id="model-selection",
            session_state=row.get("session_state", "ACTIVE"),
            active_step_id="evaluation-step",
            active_instruction="Follow the reviewed protocol step.",
        )
        started = perf_counter()
        result: TextResult | None = None
        error = ""
        try:
            result = adapter.interpret(row["transcript"], context)
            command = result.command
        except ModelAdapterError as exc:
            error = exc.code
            command = ParsedCommand("UNSUPPORTED", {}, 0.0, (f"adapter failure {exc.code}",))
        except Exception as exc:  # budget refusal and similar stop the configuration
            if type(exc).__name__ == "BudgetExceededError":
                records.append({"id": row["id"], "status": "not_run_budget"})
                break
            raise
        latency = (perf_counter() - started) * 1000
        peak_rss = max(peak_rss, process.memory_info().rss)
        intent_ok, exact_ok = exact_match(command, row)
        scoring_row = dict(row)
        canonical = validate_and_normalise_command(command)
        if canonical.intent in row["acceptable_intents"] and canonical.intent != row["expected_intent"]:
            scoring_row["expected_intent"] = canonical.intent
            scoring_row["expected_parameters"] = {}
        components = component_outcomes_for_command(
            command, scoring_row, session_state=row.get("session_state", "ACTIVE")
        )
        llm_called, schema_valid = _llm_call_status(result, error, kind)
        records.append(
            {
                "id": row["id"],
                "category": row.get("category", ""),
                "source": row.get("source", "benchmark"),
                "valid": bool(row["action_valid"]),
                "status": "failed" if error else "succeeded",
                "error": error,
                "latency_ms": round(latency, 1),
                "route": _route(result, kind),
                "llm_called": llm_called,
                "schema_valid": schema_valid,
                "intent": canonical.intent,
                "parameters": dict(canonical.parameters),
                "confidence": command.confidence,
                "intent_correct": intent_ok,
                "exact_correct": exact_ok,
                "abstained": bool(row["action_valid"]) and canonical.intent == "UNSUPPORTED",
                "components": {
                    gate: {
                        "executed": bool(components[gate]["executed"]),
                        "unsafe_executed": bool(components[gate]["unsafe_executed"]),
                    }
                    for gate in GATES
                },
                "metadata": {
                    key: value
                    for key, value in (result.metadata.items() if result else [])
                    if key
                    in {
                        "cascade_route",
                        "confidence_basis",
                        "llm_failure",
                        "abstention_reason",
                        "llm_confidence_basis",
                        "prompt_tokens",
                        "completion_tokens",
                        "llm_prompt_tokens",
                        "llm_completion_tokens",
                    }
                },
            }
        )
        del index
    return {"records": records, "peak_rss_mb": round(peak_rss / 1e6, 1)}


VERIFIED_ROUTES = {"llm_verified", "abstain_model_disagreement", "abstain_unverified"}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1) + 0.4999)))
    return ordered[position]


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in records if r.get("status") != "not_run_budget"]
    valid = [r for r in done if r["valid"]]
    llm_calls = [r for r in done if r["llm_called"]]
    warm = [r["latency_ms"] for r in done[1:]]
    llm_routed_valid = [
        r for r in valid if r["route"] in {"llm", "llm_unsupported", "llm_failed", *VERIFIED_ROUTES}
    ]
    summary: dict[str, Any] = {
        "items": len(done),
        "not_run_budget": len(records) - len(done),
        "valid_items": len(valid),
        "intent_accuracy_all": _ratio(sum(r["intent_correct"] for r in done), len(done)),
        "exact_accuracy_valid": _ratio(sum(r["exact_correct"] for r in valid), len(valid)),
        "exact_correct_valid": sum(r["exact_correct"] for r in valid),
        "abstentions_valid": sum(r["abstained"] for r in valid),
        "llm_calls": len(llm_calls),
        "schema_valid_llm": _ratio(sum(r["schema_valid"] for r in llm_calls), len(llm_calls)),
        "failures": sum(r["status"] == "failed" for r in done),
        "latency_cold_ms": done[0]["latency_ms"] if done else 0.0,
        "latency_p50_ms": round(median(warm), 1) if warm else 0.0,
        "latency_p95_ms": round(_percentile(warm, 0.95), 1),
        "routes": _count(r["route"] for r in done),
        "routes_valid": _count(r["route"] for r in valid),
        "llm_routed_valid": len(llm_routed_valid),
        "llm_routed_share_valid": _ratio(len(llm_routed_valid), len(valid)),
        "llm_routed_exact": sum(r["exact_correct"] for r in llm_routed_valid),
        "llm_routed_exact_accuracy": _ratio(
            sum(r["exact_correct"] for r in llm_routed_valid), len(llm_routed_valid)
        ),
        "llm_routed_abstentions": sum(r["abstained"] for r in llm_routed_valid),
        "gates": {
            gate: {
                "executed": sum(r["components"][gate]["executed"] for r in done),
                "unsafe_executed": sum(r["components"][gate]["unsafe_executed"] for r in done),
            }
            for gate in GATES
        },
    }
    summary["unsafe_proposals"] = summary["gates"]["proposal"]["unsafe_executed"]
    summary["unsafe_after_gates"] = summary["gates"]["confidence"]["unsafe_executed"]
    summary["llm_routed_unsafe"] = sum(
        r["components"]["confidence"]["unsafe_executed"] for r in llm_routed_valid
    )
    # Coverage: valid items that end in the right outcome - a correct pending
    # action the operator confirms, or a correct read-only answer.
    summary["coverage_valid"] = _ratio(
        sum(
            (r["components"]["confirm_attentive"]["executed"] if r["intent"] not in NON_MUTATING
             else r["exact_correct"])
            for r in valid
        ),
        len(valid),
    )  # fmt: skip
    return summary


NON_MUTATING = {
    "GET_CURRENT_STEP", "GET_NEXT_STEP", "REPEAT_INSTRUCTION", "HELP", "VERIFY_REAGENT",
    "CORRECT_PREVIOUS_ENTRY",
}  # fmt: skip


def _ratio(numerator: float, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _ollama_ps() -> str:
    try:
        return subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _model_digest(model: str) -> str:
    try:
        response = httpx.post("http://127.0.0.1:11434/api/show", json={"model": model}, timeout=10)
        details = response.json().get("details", {})
        return json.dumps(
            {k: details.get(k) for k in ("family", "parameter_size", "quantization_level")}
        )
    except (httpx.HTTPError, ValueError):
        return ""


def _hardware() -> dict[str, str]:
    gpu = ""
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()  # fmt: skip
    except OSError:
        pass
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "ram_gb": f"{psutil.virtual_memory().total / 1e9:.1f}",
        "gpu": gpu,
    }


def run_config(config: Mapping[str, Any], ledger: SpendLedger | None) -> Path:
    adapter = build(config, ledger)
    available = {"benchmark": BENCHMARK, "heldout": HELDOUT, "heldout_b": HELDOUT_B, "dev": DEV}
    wanted = config.get("datasets")
    if wanted is None:
        wanted = ["benchmark", "heldout"]
        if HELDOUT_B.exists() and config.get("heldout_b", True):
            wanted.append("heldout_b")
    datasets = {name: load_rows(available[name]) for name in wanted}
    # ``limit`` keeps a file-order prefix of every dataset (a predeclared,
    # budget-driven subset; the result records it).
    limit = config.get("limit")
    if limit is not None:
        datasets = {name: rows[: int(limit)] for name, rows in datasets.items()}
    for name, rows in datasets.items():
        for row in rows:
            row.setdefault("source", name)
    payload: dict[str, Any] = {
        "schema_version": 2,
        "created_at": datetime.now(UTC).isoformat(),
        "config": {k: v for k, v in config.items() if k not in {"api_key_env"}},
        "credential_env_used": config.get("api_key_env", ""),
        "contract_version": CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION if config["kind"] != "rules" else "none",
        "prompt_sha256": PROMPT_SHA256 if config["kind"] != "rules" else "",
        "schema_sha256": schema_sha256(),
        "datasets": {
            "benchmark": {"path": "research/data/manifests/text-commands.jsonl", "sha256": _sha_file(BENCHMARK)},
            "heldout": {"path": "research/data/heldout/human-paraphrases.jsonl", "sha256": _sha_file(HELDOUT)},
            **({"heldout_b": {"path": "research/data/heldout/human-paraphrases-B.jsonl", "sha256": _sha_file(HELDOUT_B)}} if HELDOUT_B.exists() else {}),
            "dev": {"path": "research/data/manifests/text-dev.jsonl", "sha256": _sha_file(DEV)},
        },  # fmt: skip
        "subset": {"datasets": list(datasets), "limit": limit},
        "hardware": _hardware(),
        "evidence_class": {
            "benchmark": "synthetic authored regression benchmark",
            "heldout": "public human-written text (held out); not speech",
        },
        "results": {},
    }
    if config.get("provider") == "ollama":
        payload["model_details"] = _model_digest(config["model"])
    try:
        for name, rows in datasets.items():
            evaluated = evaluate(adapter, rows, config["kind"])
            payload["results"][name] = {
                "summary": summarise(evaluated["records"]),
                "peak_rss_mb": evaluated["peak_rss_mb"],
                "records": evaluated["records"],
            }
        payload["ollama_ps"] = _ollama_ps() if config.get("provider") == "ollama" else ""
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
    RESULTS.mkdir(parents=True, exist_ok=True)
    output = RESULTS / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', config['name'])}.json"
    output.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--allow-paid", action="store_true", help="permit hosted configurations")
    args = parser.parse_args()
    sweep = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    ledger = SpendLedger(LEDGER, cap_usd=float(sweep.get("budget_usd", 2.0)))
    for config in sweep["configurations"]:
        if args.only and config["name"] not in args.only:
            continue
        if config.get("provider") not in (None, "ollama") and not args.allow_paid:
            print(f"skip {config['name']}: hosted (use --allow-paid)")
            continue
        print(f"running {config['name']} ...", flush=True)
        output = run_config(config, ledger if config.get("provider") not in (None, "ollama") else None)
        result = json.loads(output.read_text(encoding="utf-8"))
        for name in result["results"]:
            s = result["results"][name]["summary"]
            print(
                f"  {name}: exact {s['exact_accuracy_valid']:.3f} "
                f"schema {s['schema_valid_llm']:.3f} unsafe {s['unsafe_after_gates']} "
                f"p95 {s['latency_p95_ms']}ms llm-routed {s['llm_routed_valid']}/{s['valid_items']}",
                flush=True,
            )
    print(json.dumps(ledger.summary(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
