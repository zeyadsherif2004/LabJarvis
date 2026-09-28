"""A cumulative, fail-closed ledger for paid hosted-model calls.

Every live call made by the evaluation tooling is authorised here first. The
caller supplies a conservative *upper bound* on the call's cost (prompt size
over-estimated at two characters per token, the full output-token budget, and
the published per-token price); the call is refused unless every previously
committed amount plus that bound stays within the cap. After the call the
provider-reported usage is recorded and costed.

The ledger never stores a credential, prompt or response - only provider,
model, operation, token counts and amounts. It is a JSON file so that the cap
holds across processes and across separate runs of different tools.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The single, global ceiling for every paid call made by any tool, across all
#: runs, providers and experiments (raised from $1.00 to $2.00 on 2026-09-27 by
#: the project owner). A caller may ask for a *lower* cap, never a higher one.
GLOBAL_HARD_CAP_USD = 2.0
DEFAULT_CAP_USD = GLOBAL_HARD_CAP_USD


class BudgetExceededError(RuntimeError):
    """A live call was refused because its upper bound could exceed the cap."""


class UnpricedModelError(RuntimeError):
    """No trustworthy price is recorded for a model, so its cost cannot be bounded."""


@dataclass(frozen=True, slots=True)
class Price:
    """USD per one million tokens (or per minute for audio)."""

    input_per_million: float
    output_per_million: float
    per_audio_minute: float = 0.0
    #: Price of prompt tokens the provider reports as served from its cache;
    #: ``None`` bills them at the full input price (conservative).
    cached_input_per_million: float | None = None
    source: str = ""
    retrieved: str = ""


def load_prices(path: Path) -> dict[str, Price]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    prices: dict[str, Price] = {}
    for key, value in raw["models"].items():
        prices[key] = Price(
            input_per_million=float(value["input_per_million"]),
            output_per_million=float(value["output_per_million"]),
            per_audio_minute=float(value.get("per_audio_minute", 0.0)),
            cached_input_per_million=(
                float(value["cached_input_per_million"])
                if value.get("cached_input_per_million") is not None
                else None
            ),
            source=str(value.get("source", "")),
            retrieved=str(value.get("retrieved", raw.get("retrieved", ""))),
        )
    return prices


def upper_bound_tokens(prompt_characters: int, images: int = 0) -> int:
    """Over-estimate prompt tokens: 2 characters per token, plus 1,600 per image."""

    return math.ceil(prompt_characters / 2) + 1600 * images + 64


class SpendLedger:
    """Authorise and record paid calls against one cumulative cap."""

    def __init__(self, path: Path, *, cap_usd: float = DEFAULT_CAP_USD) -> None:
        self._path = path
        # The cap is global: no configuration file or caller can lift it above
        # GLOBAL_HARD_CAP_USD, and every tool shares the same cumulative file.
        self._cap = min(float(cap_usd), GLOBAL_HARD_CAP_USD)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({"cap_usd": GLOBAL_HARD_CAP_USD, "entries": []})
        else:
            data = self._read()
            if data.get("cap_usd") != GLOBAL_HARD_CAP_USD:
                data["cap_usd"] = GLOBAL_HARD_CAP_USD
                self._write(data)

    def _read(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        return data

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._path)

    @property
    def cap_usd(self) -> float:
        return self._cap

    def committed_usd(self) -> float:
        """Observed cost where known, otherwise the upper bound, for every call."""

        total = 0.0
        for entry in self._read()["entries"]:
            observed = entry.get("observed_usd")
            total += float(observed if observed is not None else entry["upper_bound_usd"])
        return total

    def remaining_usd(self) -> float:
        return max(0.0, self._cap - self.committed_usd())

    def authorise(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        upper_bound_usd: float,
        purpose: str,
    ) -> int:
        """Reserve ``upper_bound_usd`` or raise; returns the entry index."""

        committed = self.committed_usd()
        if committed + upper_bound_usd > self._cap + 1e-12:
            raise BudgetExceededError(
                f"refused: committed ${committed:.4f} + bound ${upper_bound_usd:.4f} "
                f"would exceed the ${self._cap:.2f} cap"
            )
        data = self._read()
        data["entries"].append(
            {
                "at": datetime.now(UTC).isoformat(),
                "provider": provider,
                "model": model,
                "operation": operation,
                "purpose": purpose,
                "upper_bound_usd": round(upper_bound_usd, 8),
                "observed_usd": None,
                "usage": {},
                "status": "authorised",
            }
        )
        self._write(data)
        return len(data["entries"]) - 1

    def settle(
        self,
        index: int,
        *,
        usage: dict[str, float],
        observed_usd: float | None,
        status: str,
    ) -> None:
        """Record what the provider reported. ``observed_usd`` None keeps the bound."""

        data = self._read()
        entry = data["entries"][index]
        entry["usage"] = usage
        entry["status"] = status
        entry["observed_usd"] = None if observed_usd is None else round(observed_usd, 8)
        self._write(data)

    def summary(self) -> dict[str, Any]:
        entries = self._read()["entries"]
        by_provider: dict[str, dict[str, float]] = {}
        by_model: dict[str, dict[str, float]] = {}
        for entry in entries:
            observed = entry.get("observed_usd")
            counted = float(observed if observed is not None else entry["upper_bound_usd"])
            model_key = str(entry["provider"]) + "/" + str(entry["model"])
            for bucket in (
                by_provider.setdefault(entry["provider"], _empty_bucket()),
                by_model.setdefault(model_key, _empty_bucket()),
            ):
                bucket["calls"] += 1
                bucket["upper_bound_usd"] += float(entry["upper_bound_usd"])
                bucket["observed_usd"] += float(observed or 0.0)
                bucket["counted_usd"] += counted
                bucket["unsettled_calls"] += 1 if observed is None else 0
        return {
            "cap_usd": self._cap,
            "calls": len(entries),
            "committed_usd": round(self.committed_usd(), 6),
            "remaining_usd": round(self.remaining_usd(), 6),
            "by_provider": by_provider,
            "by_model": by_model,
        }


def _empty_bucket() -> dict[str, float]:
    return {
        "calls": 0,
        "upper_bound_usd": 0.0,
        "observed_usd": 0.0,
        "counted_usd": 0.0,
        "unsettled_calls": 0,
    }


def cost_usd(
    price: Price,
    *,
    input_tokens: float,
    output_tokens: float,
    cached_input_tokens: float = 0.0,
) -> float:
    """Cost from token counts. Output tokens include any reasoning tokens.

    Cached prompt tokens are billed at the cached price only when one is
    recorded and the provider reported them; otherwise at the full price.
    """

    cached = min(max(cached_input_tokens, 0.0), input_tokens)
    cached_price = (
        price.cached_input_per_million
        if price.cached_input_per_million is not None
        else price.input_per_million
    )
    return (
        (input_tokens - cached) * price.input_per_million
        + cached * cached_price
        + output_tokens * price.output_per_million
    ) / 1_000_000
