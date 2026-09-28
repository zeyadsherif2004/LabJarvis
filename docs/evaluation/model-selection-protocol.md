# Model selection protocol (version 2, predeclared 2026-09-26)

Status: **predeclared before the final sweep.** This file is hashed into
`research/interpreter/results/FREEZE.json` before the final text-interpreter
sweep runs. Any change after that is a protocol deviation and is logged in
`research/evidence/protocol-deviations.md`.

Version 1 of this protocol (July) listed candidates but set no numerical
thresholds. September sweeps (`research/post_review/`) were run against the
v1 prompt/schema and are kept unchanged as historical evidence.

## Decision question

For the text stage: which interpreter configuration converts real operator
wording into correct allowlisted commands, without increasing unsafe
executions over the deterministic grammar, quickly enough for interactive
sterile work on the target laptop (RTX 4050 6 GB, 16 GB RAM)?

For speech and vision: which local candidate gives adequate entity accuracy and
conservative failure behaviour on real media (see "Real-media thresholds";
evaluated once real recordings and photographs exist).

## Data splits (strictly separated)

| Split | File | Use |
|---|---|---|
| Development | `research/data/manifests/text-dev.jsonl` (37 authored items; 12 marked `used_in_prompt`) | prompt/schema development and the few-shot examples only |
| Frozen regression benchmark | `research/data/manifests/text-commands.jsonl` (81 authored items, SHA-256 `4c903406…231f`) | regression; comparability with July/September |
| **Held-out human-written paraphrases** | `research/data/heldout/human-paraphrases.jsonl` | the acceptance test for real wording; never used in prompts, examples, grammar or thresholds |
| Human study | participant data (not yet collected) | usability evidence only |

`research/interpreter/check_splits.py` fails the sweep if any held-out or
benchmark transcript equals, or has token-Jaccard ≥ 0.8 with, a development or
few-shot item.

### Held-out set: sources, rules and predeclared minimums

Only publicly available, human-authored text is used, with original wording
preserved (case and punctuation normalisation for matching only). No
utterance is generated, translated or rewritten, by a model or by the author.

| Source | Licence | Human authorship | Rows used |
|---|---|---|---|
| CLINC150 (Larson et al., EMNLP 2019), `data_full.json`, **test split only** | CC BY 3.0 | crowd-written | `timer`, `repeat`, `what_can_i_ask_you`, `oos` |
| CookDial (Jiang et al., 2022), all dialogs, user turns | MIT | human-to-human WoZ dialogs | `req_instruction`, `confirm`, `req_repeat` user turns |
| Wizard of Tasks cooking v1.0 (Choi et al., COLING 2022), **test split only**, student turns | CC BY-SA 4.0 | crowd-written | `request_next_step` |
| OpenWetWare lab notebooks | CC BY-SA 3.0 | written by researchers | sentences reporting one measured value |

Deterministic inclusion and gold-labelling rules (implemented verbatim in
`research/interpreter/build_heldout.py`):

1. **SET_TIMER** — CLINC `timer`: exactly one duration phrase
   (`<number> second|minute|hour`) → `duration_seconds`; a request to set a
   timer with no duration → `UNSUPPORTED` (incomplete). Clock times, questions
   about timers and utterances not requesting a timer are excluded.
2. **REPEAT_INSTRUCTION** — CLINC `repeat` excluding utterances about a
   *question* (LabJarvis asks none); CookDial `req_repeat` turns in the
   instruction section containing "repeat" and not naming an ingredient, tool,
   word or temperature.
3. **HELP** — CLINC `what_can_i_ask_you`, excluding preference questions
   ("subjects you like").
4. **GET_NEXT_STEP** — CookDial `req_instruction` and Wizard-of-Tasks
   `request_next_step` turns asking for the next step ("what's next", "next
   step", "what now", "what should I do now/next"), ≤ 14 words, not
   conditional ("once…", "after…", "while…"), not about ingredients. When the
   turn also states that the current step is done, `COMPLETE_STEP` is an
   additionally acceptable intent (both are safe; completion still requires
   confirmation).
5. **COMPLETE_STEP** — CookDial `confirm` turns in the instruction section that
   consist only of a completion statement ("Ok. I have done that.", "Done.").
6. **RECORD_MEASUREMENT** — OpenWetWare sentences that report exactly one
   measured value with a unit LabJarvis supports and no instruction, method
   description or setpoint ("was set at", "adjusted to", "kept at" are
   excluded). Gold kind/value/unit are read off the sentence.
7. **UNSUPPORTED (out of scope)** — the first 15 CLINC `oos` test utterances in
   SHA-256 order of their text.
8. Case-insensitive duplicates are removed; each (source, gold intent) cell is
   capped at 12 items, taking items in SHA-256 order of their text (no manual
   picking).

Amendments made before any held-out evaluation (logged as D1-D3 in
`research/evidence/protocol-deviations.md`): the timer cell is capped at 40
rather than 12; CLINC150's validation split is used with its test split; rule
**R6b** adds CookDial user turns reporting a current value ("the oven is at 170
degrees Celsius" -> RECORD_MEASUREMENT; unscaled "degrees" -> UNSUPPORTED);
rule **R9** adds 12 CLINC150 `measurement_conversion` questions as numeric
distractors (gold UNSUPPORTED).

**Predeclared minimums** (the set is *not* used for acceptance if unmet; the
shortfall is reported instead):

- at least **80** items in total;
- at least **5** supported intents with at least **5** items each;
- at least **20** items with a safety-critical number and unit
  (timer durations or measurements);
- at least **10** unsupported items.

Known coverage gap, declared in advance: no suitable human-authored public
source was found for `PAUSE_SESSION`, `RESUME_SESSION`, `END_SESSION`,
`VERIFY_REAGENT`, `CORRECT_PREVIOUS_ENTRY` or dictated `RECORD_OBSERVATION`
(MASSIVE's "resume/pause" utterances all address media playback). These
intents are covered only by the authored benchmark and are reported as a
shortfall.

## Metrics

Computed by `research/interpreter/run_sweep.py` for every configuration, on
both the benchmark and the held-out set, separately:

- **schema validity**: share of language-model calls whose output parsed
  against the v2 discriminated union (grammar-only rows excluded);
- **intent accuracy** and **exact command accuracy** (intent in the acceptable
  set and parameters equal after production normalisation; for measurements,
  value and canonical unit exact and kind as labelled);
- **unsafe proposals** and **unsafe executions after each gate** (proposal →
  schema → deterministic validation → state → derived-confidence gate →
  attentive / always-accept confirmation), using the September component
  definitions in `research/experiments/common.py`;
- **abstentions** (UNSUPPORTED on valid items) and **coverage** (share of valid
  items that would become a correct pending action);
- **route counts and shares** (rule / LLM / abstain) with denominators, overall
  and on held-out items; exact accuracy, abstentions and unsafe proposals on the
  **LLM-routed held-out subset**;
- p50 / p95 latency (warm; the first call per model is reported separately as
  cold) and peak process RSS; Ollama VRAM from `ollama ps` where available.

## Predeclared acceptance thresholds (text stage)

A configuration is **ACCEPTED** only if all hold:

| ID | Criterion | Threshold |
|---|---|---|
| A1 | Schema validity (LLM calls) | ≥ 95% |
| A2 | Exact command accuracy, valid benchmark items | ≥ 80% |
| A3 | Exact command accuracy, supported held-out human-written items | ≥ 80% (reported separately) |
| A4 | Unsafe executions after validation+state+confidence, benchmark and held-out | ≤ the rule grammar's on the same set |
| A5 | Warm p95 text latency on the RTX 4050 laptop | ≤ 3.0 s |

Additionally, for a cascade to count as evidence that **the language model**
meets the requirement (not only the grammar):

| ID | Criterion | Threshold |
|---|---|---|
| C1 | LLM-routed share of supported held-out items | ≥ 30% (reported with denominator) |
| C2 | Exact accuracy on the LLM-routed supported held-out subset | ≥ 80% |

A stand-alone model that fails A1–A5 is **REJECTED** with the failing criteria
recorded; a rejection is legitimate evidence. The largest model is not
preferred by default: among accepted configurations the production default is
the one with the lowest unsafe executions, then the highest held-out exact
accuracy, then the lowest p95 latency.

## Candidates

- `rules` (deterministic grammar, the safety baseline);
- local LLM-only, v2 contract, few-shot: `qwen2.5:0.5b`, `qwen2.5:1.5b`,
  `qwen2.5:3b`, `qwen3:4b` (thinking disabled), `qwen3:4b-instruct-2507`,
  `llama3.2:1b`, `llama3.2:3b`, `gemma3:4b`, `phi4-mini`;
- ablations for the default-size model: zero-shot v2 contract; v1 contract
  (the September result is reused, not re-run);
- `cascade` (rules-first) and `agreement` with the local candidates above;
- hosted comparators, only within the $1 total budget of this task:
  an OpenAI model, a DeepSeek-compatible model and a GLM-compatible model
  (OpenAI-compatible chat completions; the exact identifiers are recorded
  with each run).

## Real-media thresholds (speech and vision; predeclared, not yet measurable)

| Stage | Criterion | Threshold |
|---|---|---|
| Speech | WER on real command recordings | ≤ 0.15 |
| Speech | Number and unit entity accuracy | ≥ 90% |
| Speech | Warm p95 transcription latency | ≤ 3.0 s |
| Vision | Different-chemical false accepts | 0 |
| Vision | Overall false-accept rate on non-matching labels | ≤ 5% |
| Vision | Warm p95 OCR latency (single frame) | ≤ 3.0 s |

## Acceptance record

Written to `research/final_report_evidence/model-selection/` by
`research/interpreter/analyze_sweep.py`: one row per configuration with
provider, exact model identifier, prompt and schema hashes, thinking mode,
quantisation, hardware, all metrics above, **ACCEPT/REJECT** and the reason.
