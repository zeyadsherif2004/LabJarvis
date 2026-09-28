"""The global, cumulative, fail-closed spend ledger for paid calls."""

import json
from pathlib import Path

import pytest

from labjarvis.evaluation.spend import (
    GLOBAL_HARD_CAP_USD,
    BudgetExceededError,
    Price,
    SpendLedger,
    cost_usd,
    load_prices,
    upper_bound_tokens,
)


def test_the_global_cap_is_two_dollars_and_cannot_be_raised(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json", cap_usd=50.0)
    assert GLOBAL_HARD_CAP_USD == 2.0
    assert ledger.cap_usd == 2.0


def test_a_lower_cap_is_honoured(tmp_path: Path) -> None:
    assert SpendLedger(tmp_path / "ledger.json", cap_usd=0.5).cap_usd == 0.5


def test_the_cap_is_cumulative_across_ledger_instances(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    first = SpendLedger(path)
    index = first.authorise(
        provider="P", model="m", operation="text", upper_bound_usd=1.5, purpose="t"
    )
    first.settle(index, usage={}, observed_usd=1.5, status="succeeded")

    second = SpendLedger(path)  # e.g. a different experiment in a new process
    with pytest.raises(BudgetExceededError, match="cap"):
        second.authorise(
            provider="Q", model="n", operation="text", upper_bound_usd=0.6, purpose="t"
        )
    second.authorise(provider="Q", model="n", operation="text", upper_bound_usd=0.5, purpose="t")
    assert second.remaining_usd() == pytest.approx(0.0)


def test_an_unsettled_call_counts_at_its_upper_bound(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json")
    ledger.authorise(provider="P", model="m", operation="text", upper_bound_usd=0.3, purpose="t")
    assert ledger.committed_usd() == pytest.approx(0.3)


def test_observed_usage_replaces_the_bound(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json")
    index = ledger.authorise(
        provider="P", model="m", operation="text", upper_bound_usd=0.3, purpose="t"
    )
    ledger.settle(index, usage={"input_tokens": 10}, observed_usd=0.01, status="succeeded")
    assert ledger.committed_usd() == pytest.approx(0.01)


def test_an_older_ledger_is_upgraded_to_the_global_cap_without_losing_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "cap_usd": 1.0,
                "entries": [
                    {"provider": "P", "model": "m", "upper_bound_usd": 0.5, "observed_usd": 0.48}
                ],
            }
        ),
        encoding="utf-8",
    )
    ledger = SpendLedger(path, cap_usd=1.0)
    assert json.loads(path.read_text(encoding="utf-8"))["cap_usd"] == 2.0
    assert ledger.committed_usd() == pytest.approx(0.48)


def test_summary_reports_by_provider_and_model(tmp_path: Path) -> None:
    ledger = SpendLedger(tmp_path / "ledger.json")
    a = ledger.authorise(
        provider="OpenAI", model="x", operation="t", upper_bound_usd=0.1, purpose=""
    )
    ledger.settle(a, usage={}, observed_usd=0.02, status="succeeded")
    ledger.authorise(provider="OpenAI", model="y", operation="t", upper_bound_usd=0.1, purpose="")
    summary = ledger.summary()
    assert summary["by_model"]["OpenAI/x"]["observed_usd"] == pytest.approx(0.02)
    assert summary["by_model"]["OpenAI/y"]["unsettled_calls"] == 1
    assert summary["by_provider"]["OpenAI"]["counted_usd"] == pytest.approx(0.12)


def test_cached_prompt_tokens_use_the_cached_price_only_when_known() -> None:
    priced = Price(input_per_million=2.0, output_per_million=12.0, cached_input_per_million=0.2)
    unpriced = Price(input_per_million=2.0, output_per_million=12.0)
    tokens = {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 500_000}
    assert cost_usd(priced, **tokens) == pytest.approx(1.1)
    assert cost_usd(unpriced, **tokens) == pytest.approx(2.0)
    # Nonsense counts can never lower the cost below zero input.
    assert cost_usd(priced, input_tokens=10, output_tokens=0, cached_input_tokens=99) == (
        pytest.approx(10 * 0.2 / 1_000_000)
    )


def test_the_upper_bound_overestimates_prompt_tokens() -> None:
    assert upper_bound_tokens(8000) >= 4000
    assert upper_bound_tokens(0, images=1) >= 1600


def test_prices_load_with_optional_cached_price(tmp_path: Path) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "retrieved": "2026-09-27",
                "models": {
                    "a": {"input_per_million": 1, "output_per_million": 2},
                    "b": {"input_per_million": 1, "output_per_million": 2,
                          "cached_input_per_million": 0.1},
                },
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    prices = load_prices(path)
    assert prices["a"].cached_input_per_million is None
    assert prices["b"].cached_input_per_million == 0.1
