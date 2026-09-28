"""Fail if development/few-shot text leaks into the benchmark or held-out set."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from labjarvis.infrastructure.interpreter_spec import FEW_SHOT_EXAMPLES

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "research" / "data" / "manifests" / "text-dev.jsonl"
BENCHMARK = ROOT / "research" / "data" / "manifests" / "text-commands.jsonl"
HELDOUT = ROOT / "research" / "data" / "heldout" / "human-paraphrases.jsonl"
JACCARD_LIMIT = 0.8


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9.]+", text.casefold()))


def _load(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        json.loads(line)["transcript"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def leaks() -> list[str]:
    development = _load(DEV) + [example for example, _ in FEW_SHOT_EXAMPLES]
    problems: list[str] = []
    for name, evaluated in (("benchmark", _load(BENCHMARK)), ("heldout", _load(HELDOUT))):
        for text in evaluated:
            tokens = _tokens(text)
            if not tokens:
                continue
            for dev in development:
                dev_tokens = _tokens(dev)
                overlap = len(tokens & dev_tokens) / len(tokens | dev_tokens)
                if overlap >= JACCARD_LIMIT:
                    problems.append(f"{name}: {text!r} ~ development {dev!r} ({overlap:.2f})")
    return problems


def main() -> int:
    problems = leaks()
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"split check: {len(problems)} leak(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
