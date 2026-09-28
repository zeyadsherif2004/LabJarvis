"""Derive all processed tables and statistics from raw experiment output.

Implements the predeclared analysis plan (research/protocol/analysis-plan.md).
Raw results are never modified.
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results" / "raw"
OUT = ROOT / "results" / "processed"
SEED = 20260717
CONDITIONS = ("A", "B", "C_attentive", "C_always_accept")


def wilson(successes: int, total: int) -> tuple[float, float, float]:
    if total == 0:
        return (0.0, 0.0, 0.0)
    z = 1.959963984540054
    phat = successes / total
    denominator = 1 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2)) / denominator
    )
    return (phat, max(0.0, centre - margin), min(1.0, centre + margin))


def bootstrap_ci(values: list[float], *, seed_offset: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(SEED + seed_offset)
    data = np.asarray(values, dtype=float)
    means = np.array(
        [data[rng.integers(0, len(data), len(data))].mean() for _ in range(10_000)]
    )
    return (float(data.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)))


def bootstrap_diff_ci(
    pairs: list[tuple[int, int]], *, seed_offset: int = 0
) -> tuple[float, float, float]:
    """Paired risk difference (first - second) with percentile bootstrap CI."""

    rng = np.random.default_rng(SEED + seed_offset)
    data = np.asarray(pairs, dtype=float)
    observed = float(data[:, 0].mean() - data[:, 1].mean())
    diffs = []
    for _ in range(10_000):
        resample = data[rng.integers(0, len(data), len(data))]
        diffs.append(resample[:, 0].mean() - resample[:, 1].mean())
    return (observed, float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)))


def cluster_bootstrap_diff_ci(
    pairs: list[tuple[int, int]], clusters: list[str], *, seed_offset: int = 0
) -> tuple[float, float, float, int]:
    """Cluster (block) bootstrap of a paired risk difference.

    Resamples whole clusters with replacement rather than individual samples,
    so that within-cluster dependence (e.g. degradations of a shared base
    label, or the two voices of one utterance) does not inflate precision.
    Returns the observed difference, its 2.5/97.5 percentile interval, and the
    number of distinct clusters.
    """

    rng = np.random.default_rng(SEED + seed_offset)
    grouped: dict[str, list[tuple[int, int]]] = {}
    for pair, cluster in zip(pairs, clusters):
        grouped.setdefault(cluster, []).append(pair)
    keys = list(grouped.keys())
    data = np.asarray(pairs, dtype=float)
    observed = float(data[:, 0].mean() - data[:, 1].mean())
    diffs = []
    for _ in range(10_000):
        chosen = rng.integers(0, len(keys), len(keys))
        resample = np.asarray(
            [pair for index in chosen for pair in grouped[keys[index]]], dtype=float
        )
        diffs.append(resample[:, 0].mean() - resample[:, 1].mean())
    return (observed, float(np.percentile(diffs, 2.5)),
            float(np.percentile(diffs, 97.5)), len(keys))


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """Two-sided exact McNemar p-value on discordant pair counts."""

    n = only_first + only_second
    if n == 0:
        return 1.0
    k = min(only_first, only_second)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (name, p) in enumerate(ordered):
        value = min(1.0, (len(ordered) - rank) * p)
        running = max(running, value)
        adjusted[name] = running
    return adjusted


def normalised_wer(reference: str, hypothesis: str) -> float:
    def words(value: str) -> list[str]:
        cleaned = re.sub(r"[^\w%\s]", " ", value.casefold())
        cleaned = cleaned.replace("%", " percent ")
        return cleaned.split()

    ref, hyp = words(reference), words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j - 1] + (r != h), current[j - 1] + 1, previous[j] + 1))
        previous = current
    return previous[-1] / max(1, len(ref))


def write_csv(name: str, header: list[str], rows: list[list[object]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def load(name: str) -> dict:
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def analyse_e2(stats: dict) -> None:
    data = load("e2-text-results.json")
    records = data["records"]
    n = len(records)
    rows = []
    unsafe_flags = {
        c: [int(r["conditions"][c]["unsafe_executed"]) for r in records] for c in CONDITIONS
    }
    for condition in CONDITIONS:
        unsafe = sum(unsafe_flags[condition])
        missed = sum(r["conditions"][condition]["missed_valid"] for r in records)
        confirmations = sum(r["conditions"][condition]["confirmations"] for r in records)
        rate, low, high = wilson(unsafe, n)
        rows.append([condition, n, unsafe, f"{rate:.4f}", f"{low:.4f}", f"{high:.4f}",
                     missed, confirmations])
    write_csv(
        "e2-condition-summary.csv",
        ["condition", "n", "unsafe_executed", "unsafe_rate", "wilson_low", "wilson_high",
         "missed_valid", "confirmations"],
        rows,
    )
    valid = [r for r in records if r["action_valid"]]
    stats["e2"] = {
        "n": n,
        "intent_accuracy": sum(r["intent_correct"] for r in records) / n,
        "valid_n": len(valid),
        "command_confidence_gate": {
            "threshold": 0.75,
            "value_counts": {
                str(value): sum(
                    1 for r in records if float(r["proposal"]["confidence"]) == value
                )
                for value in (0.0, 0.99)
            },
            "below_threshold": sum(
                1 for r in records if float(r["proposal"]["confidence"]) < 0.75
            ),
            "below_threshold_after_validation_and_state": sum(
                1
                for r in records
                if float(r["proposal"]["confidence"]) < 0.75
                and r["conditions"]["B"]["executed"]
            ),
            "newly_rejected_by_gate": sum(
                1
                for r in records
                if r["conditions"]["B"]["executed"]
                and not r["conditions"]["C_always_accept"]["executed"]
            ),
            "B_vs_C_always_accept_execution_disagreements": sum(
                1
                for r in records
                if r["conditions"]["B"]["executed"]
                != r["conditions"]["C_always_accept"]["executed"]
            ),
        },
        "contrasts": {},
    }
    for index, (first, second) in enumerate(
        [("A", "C_attentive"), ("B", "C_attentive"), ("A", "B")]
    ):
        pairs = list(zip(unsafe_flags[first], unsafe_flags[second]))
        only_first = sum(1 for a, b in pairs if a and not b)
        only_second = sum(1 for a, b in pairs if b and not a)
        diff, low, high = bootstrap_diff_ci(pairs, seed_offset=index)
        stats["e2"]["contrasts"][f"{first}_vs_{second}"] = {
            "outcome": "unsafe_executed",
            "discordant": [only_first, only_second],
            "p_mcnemar": mcnemar_exact(only_first, only_second),
            "risk_difference": diff,
            "rd_ci95": [low, high],
        }
    stats["e2"]["holm_adjusted"] = holm(
        {name: c["p_mcnemar"] for name, c in stats["e2"]["contrasts"].items()}
    )


def analyse_e3(stats: dict) -> None:
    data = load("e3-vision-results.json")
    records = data["records"]
    nonmatch = [r for r in records if r["truth"] != "match"]
    match = [r for r in records if r["truth"] == "match"]
    posthoc_threshold = 93.5

    def accepts(record: dict, policy: str) -> bool:
        decisions = record["decisions"]
        if policy == "A":
            return bool(decisions["A_accepts"])
        if policy == "B":
            return bool(decisions["B_accepts"])
        if policy == "T93.5":
            return float(decisions["score"]) >= posthoc_threshold
        return decisions["C_outcome"] == "MATCH"

    rows = []
    fa_flags: dict[str, list[int]] = {}
    for policy in ("A", "B", "C", "T93.5"):
        false_accepts = sum(1 for r in nonmatch if accepts(r, policy))
        true_accepts = sum(1 for r in match if accepts(r, policy))
        abstain = (
            sum(1 for r in records if r["decisions"]["C_outcome"] == "UNCERTAIN")
            if policy == "C"
            else 0
        )
        fa_rate, fa_low, fa_high = wilson(false_accepts, len(nonmatch))
        ta_rate, ta_low, ta_high = wilson(true_accepts, len(match))
        rows.append([policy, len(nonmatch), false_accepts, f"{fa_rate:.4f}",
                     f"{fa_low:.4f}", f"{fa_high:.4f}", len(match), true_accepts,
                     f"{ta_rate:.4f}", f"{ta_low:.4f}", f"{ta_high:.4f}", abstain])
        fa_flags[policy] = [int(accepts(r, policy)) for r in nonmatch]
    write_csv(
        "e3-condition-summary.csv",
        ["policy", "nonmatch_n", "false_accepts", "false_accept_rate", "fa_wilson_low",
         "fa_wilson_high", "match_n", "true_accepts", "true_accept_rate", "ta_wilson_low",
         "ta_wilson_high", "abstentions"],
        rows,
    )

    cers = [r["ocr_cer"] for r in records if r.get("ocr_cer") is not None]
    latencies = [r["latency_ms"] for r in records if "latency_ms" in r]
    stats["e3"] = {
        "n": len(records),
        "nonmatch_n": len(nonmatch),
        "mean_ocr_cer": sum(cers) / len(cers),
        "exact_reads": sum(1 for c in cers if c == 0),
        "latency_median_ms": statistics.median(latencies),
        "latency_p95_ms": sorted(latencies)[int(0.95 * len(latencies))],
        "contrasts": {},
    }
    nonmatch_clusters = [r["expected_reagent"] for r in nonmatch]
    for index, (first, second) in enumerate([("A", "C"), ("B", "C"), ("A", "B")], start=10):
        pairs = list(zip(fa_flags[first], fa_flags[second]))
        only_first = sum(1 for a, b in pairs if a and not b)
        only_second = sum(1 for a, b in pairs if b and not a)
        diff, low, high = bootstrap_diff_ci(pairs, seed_offset=index)
        c_diff, c_low, c_high, n_clusters = cluster_bootstrap_diff_ci(
            pairs, nonmatch_clusters, seed_offset=index
        )
        stats["e3"]["contrasts"][f"{first}_vs_{second}"] = {
            "outcome": "false_accept",
            "discordant": [only_first, only_second],
            "p_mcnemar": mcnemar_exact(only_first, only_second),
            "risk_difference": diff,
            "rd_ci95": [low, high],
            "rd_cluster_ci95": [c_low, c_high],
            "n_clusters": n_clusters,
        }
    stats["e3"]["holm_adjusted"] = holm(
        {name: c["p_mcnemar"] for name, c in stats["e3"]["contrasts"].items()}
    )
    _e3_selective_prediction(
        records, match, nonmatch, stats, posthoc_threshold=posthoc_threshold
    )

    sweep_rows = []
    for threshold in [t / 2 for t in range(120, 202)]:
        fa = sum(1 for r in nonmatch if r["decisions"]["score"] >= threshold)
        ta = sum(1 for r in match if r["decisions"]["score"] >= threshold)
        sweep_rows.append([threshold, fa / len(nonmatch), ta / len(match)])
    write_csv("e3-threshold-sweep.csv",
              ["threshold", "false_accept_rate", "true_accept_rate"], sweep_rows)
    c_accepts = [accepts(record, "C") for record in records]
    posthoc_accepts = [accepts(record, "T93.5") for record in records]
    stats["e3"]["posthoc_threshold_comparator"] = {
        "threshold": posthoc_threshold,
        "selected_after_inspecting_the_sweep": True,
        "same_acceptance_set_as_C": c_accepts == posthoc_accepts,
        "acceptance_set_disagreements": sum(
            left != right for left, right in zip(c_accepts, posthoc_accepts)
        ),
        "interpretation": (
            "Descriptive comparator only. It was not a predeclared condition "
            "and must not be presented as confirmatory evidence."
        ),
    }


def _e3_selective_prediction(
    records: list[dict],
    match: list[dict],
    nonmatch: list[dict],
    stats: dict,
    *,
    posthoc_threshold: float,
) -> None:
    """Complete outcome accounting and selective-prediction metrics for E3.

    Each of the 90 labels contributes exactly one outcome per condition:
    accept, reject, or (condition C only) abstain. Standard
    selective-classification quantities are computed with abstention treated as
    coverage loss rather than as an error.
    """

    def decision(record: dict, policy: str) -> str:
        d = record["decisions"]
        if policy == "A":
            return "accept" if d["A_accepts"] else "reject"
        if policy == "B":
            return "accept" if d["B_accepts"] else "reject"
        if policy == "T93.5":
            return "accept" if float(d["score"]) >= posthoc_threshold else "reject"
        outcome = d["C_outcome"]
        if outcome == "MATCH":
            return "accept"
        if outcome == "UNCERTAIN":
            return "abstain"
        return "reject"

    n = len(records)
    total_match, total_nonmatch = len(match), len(nonmatch)
    rows = []
    selective = {}
    for policy in ("A", "B", "C", "T93.5"):
        cells = {t: {"accept": 0, "reject": 0, "abstain": 0}
                 for t in ("match", "mismatch", "unreadable")}
        for record in records:
            cells[record["truth"]][decision(record, policy)] += 1
        false_accept = cells["mismatch"]["accept"] + cells["unreadable"]["accept"]
        true_accept = cells["match"]["accept"]
        false_reject = cells["match"]["reject"]
        true_reject = cells["mismatch"]["reject"] + cells["unreadable"]["reject"]
        abstain = sum(cells[t]["abstain"] for t in cells)
        covered = n - abstain
        covered_errors = false_accept + false_reject
        far, far_lo, far_hi = wilson(false_accept, total_nonmatch)
        tar, tar_lo, tar_hi = wilson(true_accept, total_match)
        rows.append([
            policy,
            true_accept, false_reject,  # match labels: accepted / rejected
            false_accept, true_reject,  # nonmatch labels: accepted / rejected
            abstain,
            f"{far:.4f}", f"{far_lo:.4f}", f"{far_hi:.4f}",       # false-accept rate (of nonmatch)
            f"{tar:.4f}", f"{tar_lo:.4f}", f"{tar_hi:.4f}",       # true-accept (sensitivity, of match)
            f"{false_reject / total_match:.4f}",                  # false-reject rate (of match)
            f"{true_reject / total_nonmatch:.4f}",                # true-reject rate (of nonmatch)
            f"{covered / n:.4f}",                                 # decision coverage
            f"{(covered_errors / covered) if covered else 0:.4f}",  # selective risk
        ])
        selective[policy] = {
            "coverage": covered / n,
            "abstention_rate": abstain / n,
            "selective_risk": (covered_errors / covered) if covered else 0.0,
            "false_accept_rate_nonmatch": far,
            "true_accept_rate_match": tar,
            "false_reject_rate_match": false_reject / total_match,
            "confusion": cells,
        }
    write_csv(
        "e3-selective-accounting.csv",
        ["policy", "match_accept", "match_reject", "nonmatch_accept", "nonmatch_reject",
         "abstain", "false_accept_rate", "far_lo", "far_hi", "true_accept_rate",
         "tar_lo", "tar_hi", "false_reject_rate", "true_reject_rate", "coverage",
         "selective_risk"],
        rows,
    )
    stats["e3"]["selective_prediction"] = selective
    stats["e3"]["accounting_note"] = (
        f"{n} labels = {total_match} match + {total_nonmatch} non-match "
        f"({sum(1 for r in records if r['truth'] == 'mismatch')} mismatch + "
        f"{sum(1 for r in records if r['truth'] == 'unreadable')} unreadable/blank). "
        f"OCR CER is reported over the {sum(1 for r in records if r.get('ocr_cer') is not None)} "
        "labels bearing printed text; the 5 blank labels have no reference string."
    )


def analyse_e1(stats: dict) -> None:
    data = load("e1-speech-results.json")
    records = [r for r in data["records"] if r.get("status") == "succeeded"]
    rows = []
    groups = {
        "all": records,
        "clean": [r for r in records if r["augmentation"] == "clean"],
        "noise-5dB": [r for r in records if r["augmentation"] != "clean"],
        "voice-david": [r for r in records if "David" in r["voice"]],
        "voice-zira": [r for r in records if "Zira" in r["voice"]],
    }
    for name, group in groups.items():
        wers = [r["wer"] for r in group]
        nwers = [normalised_wer(r["expected_text"], r["transcript"]) for r in group]
        latencies = [r["latency_ms"] for r in group]
        rtfs = [r["real_time_factor"] for r in group]
        mean_wer, low, high = bootstrap_ci(wers, seed_offset=len(name))
        rows.append([
            name, len(group), f"{mean_wer:.4f}", f"{low:.4f}", f"{high:.4f}",
            f"{sum(nwers)/len(nwers):.4f}", f"{statistics.median(latencies):.0f}",
            f"{sum(rtfs)/len(rtfs):.3f}",
        ])
    write_csv(
        "e1-speech-summary.csv",
        ["group", "n", "mean_wer", "wer_ci_low", "wer_ci_high", "mean_normalised_wer",
         "median_latency_ms", "mean_rtf"],
        rows,
    )
    # Cluster (utterance) bootstrap of the mean WER: clips share 30 utterance
    # texts across two voices and a noise variant, so i.i.d. resampling would
    # overstate precision.
    rng = np.random.default_rng(SEED + 500)
    by_utterance: dict[str, list[float]] = {}
    for record in records:
        by_utterance.setdefault(record["text_sample_id"], []).append(record["wer"])
    keys = list(by_utterance.keys())
    cluster_means = []
    for _ in range(10_000):
        chosen = rng.integers(0, len(keys), len(keys))
        values = [w for index in chosen for w in by_utterance[keys[index]]]
        cluster_means.append(sum(values) / len(values))
    wer_cluster_ci = [float(np.percentile(cluster_means, 2.5)),
                      float(np.percentile(cluster_means, 97.5))]

    chained = [r for r in records if "chained" in r]
    stats["e1"] = {
        "n": len(data["records"]),
        "failures": data["failure_count"],
        "n_utterance_clusters": len(keys),
        "mean_wer": sum(r["wer"] for r in records) / len(records),
        "mean_wer_cluster_ci95": wer_cluster_ci,
        "speech_confidence": {
            "definition": "exp(mean faster-whisper segment avg_logprob), clipped to [0,1]",
            "below_command_threshold_0.75": sum(
                1 for r in records if float(r["speech_confidence"]) < 0.75
            ),
            "used_by_command_gate": False,
        },
        "command_path_B_vs_C_always_accept_execution_disagreements": sum(
            1
            for r in records
            if r["chained"]["conditions"]["B"]["executed"]
            != r["chained"]["conditions"]["C_always_accept"]["executed"]
        ),
        "chained_intent_accuracy": sum(r["chained"]["intent_correct"] for r in chained)
        / len(chained),
        "chained_unsafe": {
            c: sum(r["chained"]["conditions"][c]["unsafe_executed"] for r in chained)
            for c in CONDITIONS
        },
        "chained_missed_valid": {
            c: sum(r["chained"]["conditions"][c]["missed_valid"] for r in chained)
            for c in CONDITIONS
        },
    }


def analyse_e4(stats: dict) -> None:
    data = load("e4-scenario-results.json")
    records = data["records"]
    n = len(records)
    rows = []
    success_flags = {
        c: [int(r["conditions"][c]["task_success"]) for r in records] for c in CONDITIONS
    }
    unsafe_flags = {
        c: [int(r["conditions"][c]["unsafe_events"] > 0) for r in records]
        for c in CONDITIONS
    }
    for condition in CONDITIONS:
        totals = {
            key: sum(r["conditions"][condition][key] for r in records)
            for key in ("unsafe_events", "incorrect_records", "invalid_transitions_executed",
                        "label_false_accepts", "label_abstentions", "confirmations",
                        "blocked_events", "missed_valid")
        }
        successes = sum(success_flags[condition])
        rate, low, high = wilson(successes, n)
        rows.append([condition, n, successes, f"{rate:.4f}", f"{low:.4f}", f"{high:.4f}",
                     *totals.values()])
    write_csv(
        "e4-condition-summary.csv",
        ["condition", "scenarios", "task_success", "success_rate", "wilson_low",
         "wilson_high", "unsafe_events", "incorrect_records",
         "invalid_transitions_executed", "label_false_accepts", "label_abstentions",
         "confirmations", "blocked_events", "missed_valid"],
        rows,
    )
    stats["e4"] = {
        "n": n,
        "primary_outcome": "scenario_with_one_or_more_unsafe_events",
        "task_failure_matches_any_unsafe_event": {
            condition: all(
                (not bool(success)) == bool(unsafe)
                for success, unsafe in zip(
                    success_flags[condition], unsafe_flags[condition]
                )
            )
            for condition in CONDITIONS
        },
        "contrasts": {},
    }
    for index, (first, second) in enumerate(
        [("A", "C_attentive"), ("B", "C_attentive"), ("A", "B"),
         ("C_always_accept", "C_attentive")], start=20
    ):
        pairs = list(zip(unsafe_flags[first], unsafe_flags[second]))
        only_first = sum(1 for a, b in pairs if a and not b)
        only_second = sum(1 for a, b in pairs if b and not a)
        diff, low, high = bootstrap_diff_ci(pairs, seed_offset=index)
        stats["e4"]["contrasts"][f"{first}_vs_{second}"] = {
            "outcome": "scenario_with_one_or_more_unsafe_events",
            "discordant": [only_first, only_second],
            "p_mcnemar": mcnemar_exact(only_first, only_second),
            "risk_difference": diff,
            "rd_ci95": [low, high],
        }
    stats["e4"]["holm_adjusted"] = holm(
        {name: c["p_mcnemar"] for name, c in stats["e4"]["contrasts"].items()}
    )
    # Interventions per prevented unsafe event (C_attentive vs B).
    prevented = sum(
        r["conditions"]["B"]["unsafe_events"] - r["conditions"]["C_attentive"]["unsafe_events"]
        for r in records
    )
    confirmations = sum(r["conditions"]["C_attentive"]["confirmations"] for r in records)
    stats["e4"]["confirmations_per_prevented_unsafe_vs_B"] = (
        confirmations / prevented if prevented else None
    )
    prevented_vs_a = sum(
        r["conditions"]["A"]["unsafe_events"] - r["conditions"]["C_attentive"]["unsafe_events"]
        for r in records
    )
    stats["e4"]["confirmations_per_prevented_unsafe_vs_A"] = (
        confirmations / prevented_vs_a if prevented_vs_a else None
    )


def failure_taxonomy() -> None:
    counts: dict[str, int] = {}

    def bump(category: str, amount: int = 1) -> None:
        counts[category] = counts.get(category, 0) + amount

    e1 = load("e1-speech-results.json")
    for record in e1["records"]:
        if record.get("status") != "succeeded":
            bump("speech: adapter failure")
        elif record["wer"] > 0 and record["chained"]["intent_correct"] == 0:
            bump("speech: transcription error broke command")
        elif record["wer"] > 0:
            bump("speech: transcription error, command survived")
    e2 = load("e2-text-results.json")
    for record in e2["records"]:
        if record["intent_correct"]:
            continue
        category = record["category"]
        if category == "paraphrase_uncovered":
            bump("interpretation: paraphrase outside rule grammar")
        elif category == "multi_command":
            bump("interpretation: multi-command guard failure"
                 if record["canonical"]["intent"] != "UNSUPPORTED"
                 else "interpretation: multi-command rejected late")
        else:
            bump("interpretation: other intent error")
    e3 = load("e3-vision-results.json")
    for record in e3["records"]:
        decisions = record["decisions"]
        truth = record["truth"]
        if truth != "match" and decisions["C_outcome"] == "MATCH":
            bump("vision: OCR normalised a defaced label into a false match")
        elif truth == "match" and decisions["C_outcome"] == "MISMATCH":
            bump("vision: degraded matching label conservatively rejected")
        elif decisions["C_outcome"] == "UNCERTAIN":
            bump("vision: abstention (intended safeguard, not failure)")
    write_csv(
        "failure-taxonomy.csv",
        ["category", "count"],
        sorted(counts.items(), key=lambda item: -item[1]),
    )


def main() -> None:
    stats: dict[str, object] = {"seed": SEED}
    analyse_e1(stats)
    analyse_e2(stats)
    analyse_e3(stats)
    analyse_e4(stats)
    failure_taxonomy()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stats-tests.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(OUT)


if __name__ == "__main__":
    main()
