"""Run post-review command interpreters on the frozen 81-command benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from labjarvis.evaluation.metrics import precision_recall_f1, slot_counts
from labjarvis.evaluation.run import _openai_text_adapter
from labjarvis.infrastructure.fake_models import FakeTextUnderstanding
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.real_models import OllamaTextUnderstanding
from labjarvis.ports.models import CommandContext, TextResult
from labjarvis.settings import Settings
from research.experiments.common import (
    component_outcomes_for_command,
    semantically_equal_parameters,
)

ROOT = Path(__file__).resolve().parents[2]


class Interpreter(Protocol):
    adapter_name: str
    model_name: str

    def interpret(self, transcript: str, context: CommandContext) -> TextResult: ...


def evaluation_settings() -> Settings:
    """Read shared limits and credentials without selecting an app profile."""

    return Settings(model_mode="fake")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_text_samples(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Load and minimally validate the frozen research manifest."""

    samples: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("kind") not in {None, "command"}:
            continue
        required = {
            "id",
            "transcript",
            "expected_intent",
            "expected_parameters",
            "action_valid",
            "session_state",
            "category",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"missing fields at {path}:{line_number}: {sorted(missing)}")
        samples.append({key: raw[key] for key in required})
    if not samples:
        raise ValueError(f"text manifest is empty: {path}")
    return samples, _sha256(path)


def _mean(records: Sequence[Mapping[str, Any]], key: str) -> float:
    return sum(float(record.get(key, 0)) for record in records) / max(1, len(records))


def evaluate_interpreter(
    adapter: Interpreter,
    samples: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    slot_tp = slot_fp = slot_fn = 0
    for sample in samples:
        transcript = str(sample["transcript"])
        input_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        context = CommandContext(
            session_id="post-review-evaluation",
            session_state=str(sample["session_state"]),
            active_step_id="evaluation-step",
            active_instruction="Follow the frozen public or synthetic protocol step.",
        )
        started = perf_counter()
        try:
            result = adapter.interpret(transcript, context)
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            command = result.command
            expected_parameters = dict(sample["expected_parameters"])
            intent_correct = int(command.intent == sample["expected_intent"])
            parameters_correct = semantically_equal_parameters(
                command.intent,
                dict(command.parameters),
                expected_parameters,
            )
            exact_command = int(intent_correct and parameters_correct)
            tp, fp, fn = slot_counts(expected_parameters, command.parameters)
            slot_tp += tp
            slot_fp += fp
            slot_fn += fn
            record = {
                "sample_id": sample["id"],
                "category": sample["category"],
                "input_sha256": input_hash,
                "status": "succeeded",
                "latency_ms": latency_ms,
                "intent_correct": intent_correct,
                "exact_command": exact_command,
                "slot_tp": tp,
                "slot_fp": fp,
                "slot_fn": fn,
                "prediction": {
                    "intent": command.intent,
                    "parameters": dict(command.parameters),
                    "confidence": command.confidence,
                    "ambiguities": list(command.ambiguities),
                },
                "provider_metadata": dict(result.metadata),
                "components": component_outcomes_for_command(
                    command,
                    sample,
                    session_state=str(sample["session_state"]),
                ),
            }
        except ModelAdapterError as exc:
            record = {
                "sample_id": sample["id"],
                "category": sample["category"],
                "input_sha256": input_hash,
                "status": "failed",
                "latency_ms": max(0, round((perf_counter() - started) * 1000)),
                "error_code": exc.code,
                "intent_correct": 0,
                "exact_command": 0,
            }
        records.append(record)

    precision, recall, f1 = precision_recall_f1(slot_tp, slot_fp, slot_fn)
    component_names = (
        "proposal",
        "validation",
        "state",
        "confidence",
        "confirm_attentive",
        "confirm_always_accept",
    )
    component_summary = {
        name: {
            "executed": sum(
                int(record["components"][name]["executed"])
                for record in records
                if "components" in record
            ),
            "unsafe_executed": sum(
                int(record["components"][name]["unsafe_executed"])
                for record in records
                if "components" in record
            ),
            "missed_valid": sum(
                int(record["components"][name]["missed_valid"])
                for record in records
                if "components" in record
            ),
            "confirmations": sum(
                int(record["components"][name]["confirmations"])
                for record in records
                if "components" in record
            ),
        }
        for name in component_names
    }
    return {
        "adapter": adapter.adapter_name,
        "model_name": adapter.model_name,
        "sample_count": len(records),
        "failure_count": sum(record["status"] == "failed" for record in records),
        "metrics": {
            "intent_accuracy": _mean(records, "intent_correct"),
            "exact_command_accuracy": _mean(records, "exact_command"),
            "slot_precision": precision,
            "slot_recall": recall,
            "slot_f1": f1,
            "mean_latency_ms": _mean(records, "latency_ms"),
        },
        "component_summary": component_summary,
        "records": records,
    }


def _adapter(kind: str, model_name: str, settings: Settings) -> Interpreter:
    if kind == "rule":
        return FakeTextUnderstanding(wake_phrase=settings.wake_phrase)
    if kind == "hosted":
        return _openai_text_adapter(model_name=model_name, settings=settings)
    if kind == "ollama":
        return OllamaTextUnderstanding(
            model_name=model_name,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.inference_timeout_seconds,
            context_tokens=settings.text_context_tokens,
            wake_phrase=settings.wake_phrase,
        )
    raise ValueError(f"unknown interpreter kind: {kind}")


def run(
    kind: str,
    model_name: str,
    *,
    output: Path,
    manifest: Path | None = None,
) -> Path:
    manifest = manifest or (
        ROOT / "research" / "data" / "manifests" / "text-commands.jsonl"
    )
    manifest = manifest.resolve()
    samples, dataset_hash = load_text_samples(manifest)
    settings = evaluation_settings()
    adapter = _adapter(kind, model_name, settings)
    try:
        candidate = evaluate_interpreter(adapter, samples)
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    payload = {
        "schema_version": 1,
        "evidence_class": "synthetic_post_review",
        "eligible_claims": [
            "interpreter performance on the frozen synthetic text benchmark",
            "component outcomes on identical authored cases",
        ],
        "prohibited_claims": [
            "real operator performance",
            "real laboratory safety",
            "real speech or image performance",
        ],
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": str(manifest.relative_to(ROOT)).replace("\\", "/"),
        "dataset_sha256": dataset_hash,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "commit": commit,
            "working_tree_dirty": dirty,
        },
        "candidate": candidate,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("rule", "hosted", "ollama"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    print(run(args.kind, args.model, output=args.output, manifest=args.manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
