# Protocol deviations

The research and analysis plans were frozen before final experiment execution.
This record distinguishes departures from those plans from changes to the
benchmark or hypotheses.

## Dataset-size targets

The plan targeted at least 120 text commands and 100 label images. The final
fixed sets contain 81 text commands and 90 images. Generation stopped with the
available set because the project schedule did not permit independent case
authoring or acquisition of real media. All generated cases in the final
manifests were retained; no case was removed after examining experiment
outcomes. The manuscript reports the actual counts and treats the authored set
as a finite stress benchmark rather than a population sample.

## E4 outcome representation

The analysis plan named unsafe executed actions as the primary E4 outcome. The
earlier report tested the complementary task-success flag. The corrected
analysis uses, for each scenario, the presence of one or more unsafe events.
On the fixed 24 scenarios, task failure and the primary unsafe-event flag are
identical under all four reported conditions. The numerical risk differences
therefore retain their magnitude, while their signs and wording now follow the
planned safety outcome.

## Fixture and harness corrections before final runs

Two smoke-test issues were corrected before the recorded final runs: a
resume-state ground-truth label and an image fixture that printed an extra lot
line. A later source-interface change required the harness to unwrap the
command result object before rerunning all four experiments at the fixed source
version. The experiment registry preserves both the smoke and rerun records.
No final benchmark label was changed in response to a reported effect.

## Final remediation pass (2026-09-26): held-out human-written paraphrase set

All three changes below were made **after seeing only the coverage counts of
the candidate held-out set and before any interpreter was evaluated on it**.
No model output on the held-out set existed when they were made.

- **D1 - timer cell cap.** The predeclared rule R8 capped every (source,
  intent) cell at 12 items. Applied to CLINC150 timers it left only 12 items
  with a safety-critical duration, below the predeclared minimum of 20 numeric
  items. The timer cell cap was raised to 40 (all eligible items).
- **D2 - CLINC150 validation split.** The predeclared sources used CLINC150's
  test split only. Even uncapped it held 16 timer utterances with a single
  duration. The validation split is equally unseen by this project (CLINC150
  is not used in development, prompts or examples), so both splits are used.
- **D3 - additional numeric rules.** OpenWetWare notebooks (rule R6) produced
  no single-value measurement reports that pass the predeclared filters:
  notebook text is written as methods ("was set at", "was decreased to").
  Two rules were added: R6b (CookDial user turns that report a current value,
  e.g. "the oven is at 170 degrees Celsius"; an unscaled "degrees" report is
  gold `UNSUPPORTED`) and R9 (CLINC150 `measurement_conversion` questions as
  numeric distractors, gold `UNSUPPORTED`).

Consequence reported with the results: the frozen held-out set has **138
items**, 29 with a safety-critical number (27 timers, 2 measurements). Human
evidence for dictated **measurements is minimal (n = 2)**; PAUSE/RESUME/END,
VERIFY_REAGENT, CORRECT_PREVIOUS_ENTRY, GET_CURRENT_STEP and dictated
observations have **no** held-out human-written items. Those intents rest on the
authored benchmark only.

## Final remediation pass (2026-09-26): after the first sweep

- **D4 - benchmark slot adjudication (post-results).** Four frozen-benchmark
  paraphrases (text-046 to text-049) carry a gold intent but no slots, so the
  September harness scores *any* execution of them, even an exactly right one
  ("log the temperature at 37 celsius" -> temperature 37 degC), as unsafe. This
  inflated every unsafe count, including the September hosted result. The frozen
  manifest is unchanged; `research/data/manifests/text-commands-slot-adjudication.json`
  supplies the obvious slots and every table reports frozen and adjudicated
  counts side by side. The adjudication was made after seeing model outputs.
- **D5 - dual-model verified cascade (post-results).** After the first sweep
  showed that the remaining unsafe executions were wrong-but-valid state
  changes on LLM-routed utterances, three verifier pairs were added to the
  sweep configuration and fixed before any was run. Because they were designed
  after seeing held-out A, the untouched held-out B set (built from source rows
  never used in A) is reported as their confirmation.
- **Grounding v2 not adopted.** The v2 floor (0.15) was chosen on the
  development split only. On the four held-out test protocols v2 raised answer
  recall (0.60 -> 0.80) but answered more unsupported questions (0.33 -> 0.50)
  and failed the existing authored safety gate; production keeps v1. No test
  result was used to tune v2.
- **Outcome.** No text configuration met every predeclared criterion (A4 fails
  for every language-model configuration). The production default was changed
  from `qwen2.5:1.5b` to the `qwen2.5:3b` rules-first cascade as a *provisional*
  choice with the recorded limitation, not as an ACCEPT.


## Post-hoc extension (2026-09-27): generation-matched OpenAI comparators

Recorded **before any of these calls was made** (configuration
`research/interpreter/configs/d6-d7-openai.yaml`, sha256 `83ad533e1aecf7eba640675730ae14fe758eeb7ba106fcb9a34bff38ad861a1f`). All runs
re-use the frozen benchmark and held-out sets A and B, which have already been
scored for 24 configurations, so every result below is **comparative evidence,
not a fresh model selection**. No threshold, prompt, schema or grammar was
changed for these runs.

Paid calls are bounded by the global, cumulative ledger cap of **$2.00**
(including the $0.4839 already committed; $1.5161 remained when this was
written). Every request is authorised against its worst case - prompt
over-estimated at 2 characters/token plus the full 512-token output budget
(reasoning tokens included) at list price - and refused if it could cross the
cap. After the call the provider-reported usage (cached prompt tokens billed at
the cached price) replaces the bound. If the cap stops a run mid-dataset the
partial result is kept and labelled **INCOMPLETE** with the items covered.

Datasets (sha256): benchmark `4c903406d1ddf214efeabb7d91aae60af80a591c6a4409d4fd99190d6d69231f` (81 items), held-out A `a65bdf163d8ad30b8eec232e7e0b42280982fcc27796ca100b2cc93bb6f953e8` (138; 98
supported), held-out B `d4fc46744251bbcc3a1d04ccf2fd9a8339d6b4e0e4b070a7deeeffaee6e5a22e` (139; 100 supported), development `59b8898c99e57ca1d67fd565bdbcc0628e04a080ae1469f38bd61fc900650c69` (37;
preflight only, never scored as evidence). Prompt `interpreter-prompt-v2`,
contract `command-contract-v2`, OpenAI Responses API with strict JSON schema,
`max_output_tokens` 512, no temperature parameter, no tools.

### D6 - gpt-5.4-nano and gpt-5.6-luna (highest priority)

- **Why:** the only OpenAI comparator so far (`gpt-4.1-nano`) was chosen for a
  low, boundable price, not for generation parity with `deepseek-flash`.
- **Models:** `gpt-5.4-nano` (default snapshot `gpt-5.4-nano-2026-03-17`) and
  `gpt-5.6-luna`, both with `reasoning.effort = none`, documented as supported
  on both model pages on 2026-09-27. The value used is recorded per call.
- **Order:** (0) preflight on the development split only, to confirm that the
  reasoning setting is accepted and that 512 output tokens do not truncate;
  (1) model-alone runs (`llm`) on benchmark + A + B; (2) grammar-first runs
  (`cascade`, `rules_first`): the model is called **only** for items the
  grammar cannot parse (about 25 + 136 + 132 calls) and the grammar's own
  answers are merged, so every score covers the full set.
- **Metrics (as in the protocol):** schema validity of model calls; exact
  command accuracy on valid benchmark items (frozen and D4-adjudicated labels)
  and on supported held-out items; unsafe proposals and unsafe executions after
  the validation, state and confidence gates; share and accuracy of LLM-routed
  items; warm p50/p95 latency; cost per run from the ledger.
- **Decision rule (fixed now):** a D6 configuration becomes the *recommended
  hosted OpenAI text option* in the documentation only if, on held-out A, its
  unsafe executions are at most 4 and its exact count is at least 77/98 (the
  current OpenAI comparator, `gpt-4.1-nano-llm`), and on held-out B it has 0
  unsafe executions and exact accuracy of at least 0.85. Among qualifying
  configurations the choice is by fewer unsafe executions (A+B), then higher
  A+B exact accuracy, then lower p95 latency, then lower cost. The original
  A1-A5 verdict (ACCEPT/REJECT) is reported for each configuration too. D6
  cannot change the local default (`qwen2.5:3b` cascade) or the recommended
  hybrid profile (DeepSeek); that would need a new predeclared protocol.
- **Stopping rule:** the ledger cap only; no early stop on results.

### D7 - gpt-5.6-luna vs gpt-5.6-terra (only with budget left after D6)

- **Input set:** held-out A only (138 human-written items, the evidence class
  that matters most), model-alone (`llm`), in file order. A full three-set run
  of `gpt-5.6-terra` (list price $2 / $12 per million tokens) cannot fit the cap.
- **Reasoning:** both at `reasoning.effort = none`, documented as supported by
  both model pages; this compares the two sizes at matched effort. The
  provider default (`medium`) is not tested (cost). That is a stated
  limitation, not an assumption that `none` is Terra's best setting.
- **Runs, in order:** (a) Terra on A; (b) Luna repeat on A (Luna's D6 A run is
  its first run); (c) Terra repeat on A. Each run starts only if its projected
  cost - the model's measured mean cost per call so far x items x 1.25 - fits
  the remaining budget; if not, the largest file-order prefix of A that fits is
  run and labelled INCOMPLETE (prefix length recorded). Only cost is used for
  this, never accuracy.
- **Metrics:** as D6 on held-out A, plus **consistency** - for each model, the
  share of items whose repeated run produced the identical canonical command,
  and every item whose safety outcome (unsafe execution yes/no) changed.
- **Decision rule (fixed now):** D7 is descriptive and changes no default. It
  reports whether Terra's held-out A exact count and unsafe count differ from
  Luna's; with 98 supported items a difference of fewer than 5 items is
  reported as no evidence of a difference.

### D8 - lower priority (only if budget remains after D7)

Recorded before any D8 call (runner `research/live/d8_hosted.py`, sha256
`db764260f984a88a4181b0102dfc13b9cad849af50ffbd832326ab1341f84f92`). Every hosted request - text inside the cascade, vision and speech -
passes through a budgeted client that authorises its worst case against the
same global $2.00 ledger before sending (images bounded at 6,000 tokens each,
audio at the per-minute price plus a token bound, 512 output tokens).

- **D8a hosted vision - PROVISIONAL, SYNTHETIC.** The 90 rendered labels,
  one frame each, read by `gpt-5.4-nano` (OpenAI; image input documented) and
  `glm-4.6v-flashx` (GLM, OpenAI-compatible). The comparator and thresholds are
  the production ones (match 92, uncertain 72); outcomes are classified exactly
  as the EasyOCR single-frame policy in `multiframe_ablation` (same labels), so
  the rows are directly comparable. Metrics: false accepts, false rejects,
  uncertain, coverage of true matches, errors, latency, cost. Rendered labels
  may favour or disfavour vision-language models; the result must be repeated
  on real photographs before any claim.
- **D8b deployment profiles.** The six demo scenarios (four TTS utterances:
  grammar measurement, two human-written paraphrases, "check reagent"; one
  matching and one mismatching rendered label) through the production service
  under three profiles: fully local (Whisper small.en GPU, qwen2.5:3b cascade,
  EasyOCR GPU), hybrid (local speech and vision, DeepSeek `deepseek-flash`
  text cascade, thinking off) and fully hosted OpenAI (`gpt-4o-mini-transcribe`,
  `gpt-5.4-nano` cascade at reasoning `none`, `gpt-5.4-nano` vision). Metrics:
  scenarios reaching the expected result, route, per-stage and end-to-end
  latency, cost. Six scenarios are a demonstration, not an accuracy estimate.
- **Order and stopping rule:** D8a, then D8b; each part runs only if budget
  remains after D7, and the ledger cap stops any request that could cross it
  (partial results labelled INCOMPLETE).
- **Decision rule:** descriptive only; no default changes.


### Outcomes of D6-D8 (added after the runs; the rules above were not changed)

**D6.** Preflight (development split only): 0 errors, 0 truncations; outputs
at most 36 tokens with 0 reasoning tokens at `none`, so `max_output_tokens`
stayed 512. OpenAI reported cached prompt tokens for `gpt-5.4-nano` but none
for `gpt-5.6-luna`.

| Configuration | Bench exact frozen/adj | Bench unsafe frozen/adj | Held-out A exact | A unsafe | Held-out B exact | B unsafe | p95 | A1-A5 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| gpt-5.4-nano llm | 0.855 / 0.927 | 7 / 3 | 86/98 | 0 | 0.94 | 0 | 1.6 s | REJECT (A4 bench) |
| gpt-5.4-nano cascade | 0.891 / 0.964 | 5 / 1 | 89/98 | 0 | 0.96 | 0 | 1.5 s | REJECT (A4 bench, 1 item) |
| gpt-5.6-luna llm | 0.855 / 0.927 | 6 / 2 | 75/98 | 0 | 0.78 | 0 | 2.4 s | REJECT (A3, A4) |
| gpt-5.6-luna cascade | 0.891 / 0.964 | 4 / 0 | 72/98 | 0 | 0.79 | 0 | 2.0 s | REJECT (A3, C2) |

Decision under the D6 rule: both `gpt-5.4-nano` configurations qualify
(A unsafe <= 4, A exact >= 77, B unsafe 0, B exact >= 0.85); neither
`gpt-5.6-luna` configuration does (A exact below 77). Tie-break: both have 0
held-out unsafe executions; the cascade has the higher A+B exact accuracy. The
**recommended hosted OpenAI text option is the `gpt-5.4-nano` rules-first
cascade at reasoning `none`**. It does not change the local default or the
recommended hybrid (DeepSeek) profile, and it is still not an A1-A5 ACCEPT.

**D7** (held-out A, model alone, reasoning `none` for both). Terra run 1: 88/98
exact, 0 unsafe, p95 1.6 s, $0.593 for 138 calls (no prompt caching reported).
Luna run 1 (from D6): 75/98, 0 unsafe; Luna run 2: 72/98, 0 unsafe. Terra run 2
was limited by the cost rule to the first 120 items (**INCOMPLETE, 120/138**;
the prefix happens to contain all 98 supported items): 88/98, 0 unsafe, $0.080
(the provider served most of the prompt from cache). Repeatability: Luna 97.1%
identical commands over 138 items, Terra 98.3% over 120; no item changed its
safety outcome in either model. Reading under the D7 rule: Terra minus Luna =
+13 exact items, 0 unsafe - a difference of at least 5 items, so reported as a
difference in exact accuracy on this set, with no difference in safety.
Descriptive only; no default changed.

**D8a (PROVISIONAL, SYNTHETIC).** On the 90 rendered labels, single frame:
`gpt-5.4-nano` vision 66 correct, **10 false accepts**, 9 false rejects, 5
uncertain, 5 output errors, coverage of true matches 0.80; `glm-4.6v-flashx` 37
correct, 1 false accept, 48 uncertain - 47 responses failed the output contract
because the model leaked its reasoning text into the answer (diagnosed on one
item; failing closed is the intended behaviour). EasyOCR single frame on the
same labels had 6 false accepts. Every `gpt-5.4-nano` false accept is a
deliberately defaced decoy read as the intended word (ETHANOI -> ETHANOL,
ACET0NE -> ACETONE): the model normalises exactly the errors the check exists
to catch. EasyOCR's false accepts are digit-for-letter swaps only. Cost $0.0135.

**D8b.** All three profiles reached 6/6 demo scenarios (fully local; hybrid with
DeepSeek text; fully hosted OpenAI with `gpt-4o-mini-transcribe`,
`gpt-5.4-nano` cascade and `gpt-5.4-nano` vision). Warm per-stage latency:
local speech about 0.4 s, local LLM route 0.8 s, local OCR 0.1 s; hosted text
1.2-1.4 s; hosted speech 0.8-1.4 s; hosted vision 1.2 s. The first call of each
local profile includes model loading (up to 12 s). Cost $0.0024. Six synthetic
scenarios demonstrate that each profile works end to end; they are not an
accuracy comparison.

**Spend.** This extension committed $0.9640 (D6 $0.2655 including the $0.0204
preflight; D7 $0.6826; D8 $0.0159, including a $0.0002 diagnosis call). The
cumulative ledger stands at $1.4479 of the $2.00 cap; $0.5521 was left unspent.
