# Model selection record (2026-09-26/27)

Protocol: `docs/evaluation/model-selection-protocol.md` (predeclared, hashed in
`research/interpreter/results/FREEZE.json`; deviations D1-D5 in
`research/evidence/protocol-deviations.md`). Machine-readable record:
`research/final_report_evidence/model-selection/model-selection.csv`;
generated table and figures in the same folder
(`fig-risk-coverage`, `fig-category-accuracy`, `fig-latency`).

Hardware: Windows 11, RTX 4050 Laptop 6 GB (Ollama on GPU), 16 GB RAM.
Contract `command-contract-v2`, prompt `interpreter-prompt-v2`
(sha256 `c52996c7...`), schema sha256 `232b8e37...`, temperature 0.
Benchmark = 81 authored commands (55 valid); held-out A = 138 public
human-written items (98 supported); held-out B = 139 untouched items
(100 supported, 4 intents). "frozen/adj" = frozen labels / D4 slot adjudication.

## Text interpreter

| Configuration | Provider | Model (verifier) | Schema valid | Bench exact frozen/adj | Bench unsafe frozen/adj | Held-out exact | Held-out unsafe | LLM-routed (held-out) | LLM-routed exact | Held-out B exact | p95 ms | Decision |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| rules | deterministic | rule-grammar-v1 | - | 0.818 | 0 / 0 | 2/98 | 0 | 0/98 | - | 0.07 | 0 | REJECT (A3) |
| qwen2.5-0.5b-llm | Ollama | qwen2.5:0.5b | 0.987 | 0.600 / 0.618 | 9 / 8 | 44/98 | 13 | 98/98 | 0.449 | - | 226 | REJECT (A2, A3, A4) |
| qwen2.5-1.5b-llm-zeroshot | Ollama | qwen2.5:1.5b, no examples | 1.0 | 0.600 / 0.618 | 18 / 17 | 33/98 | 13 | 98/98 | 0.336 | - | 498 | REJECT (A2, A3, A4) |
| qwen2.5-1.5b-llm | Ollama | qwen2.5:1.5b | 1.0 | 0.854 / 0.927 | 9 / 5 | 68/98 | 2 | 98/98 | 0.693 | 0.70 | 548 | REJECT (A3, A4) |
| qwen2.5-3b-llm | Ollama | qwen2.5:3b | 1.0 | 0.800 / 0.836 | 10 / 8 | 89/98 | 1 | 98/98 | 0.908 | 0.94 | 862 | REJECT (A4) |
| qwen3-4b-nothink-llm | Ollama | qwen3:4b, think off | 1.0 | 0.872 / 0.945 | 12 / 8 | 88/98 | 2 | 98/98 | 0.898 | - | 953 | REJECT (A4) |
| qwen3-4b-instruct-llm | Ollama | qwen3:4b-instruct-2507 | 1.0 | 0.781 / 0.836 | 7 / 4 | 68/98 | 1 | 98/98 | 0.693 | - | 1140 | REJECT (A3, A4) |
| llama3.2-1b-llm | Ollama | llama3.2:1b | 1.0 | 0.763 / 0.781 | 18 / 17 | 71/98 | 8 | 98/98 | 0.724 | 0.82 | 488 | REJECT (A2, A3, A4) |
| llama3.2-3b-llm | Ollama | llama3.2:3b | 1.0 | 0.800 / 0.872 | 12 / 8 | 68/98 | 10 | 98/98 | 0.693 | - | 797 | REJECT (A3, A4) |
| gemma3-4b-llm | Ollama | gemma3:4b | - | 0 | 0 | 0/98 | 0 | 98/98 | 0 | 0 | 20031 | REJECT: every request hit the 20 s timeout under the grammar |
| phi4-mini-llm | Ollama | phi4-mini | 1.0 | 0.872 / 0.945 | 11 / 7 | 81/98 | 6 | 98/98 | 0.826 | 0.86 | 977 | REJECT (A4) |
| qwen2.5-1.5b-cascade | Ollama | qwen2.5:1.5b | 1.0 | 0.872 / 0.945 | 4 / 0 | 68/98 | 2 | 96/98 | 0.687 | 0.71 | 447 | REJECT (A3, A4, C2) |
| qwen2.5-1.5b-agreement | Ollama | qwen2.5:1.5b | 1.0 | 0.854 / 0.927 | 4 / 0 | 68/98 | 2 | 96/98 | 0.687 | - | 548 | REJECT (A3, A4, C2) |
| **qwen2.5-3b-cascade** | Ollama | qwen2.5:3b | 1.0 | 0.872 / 0.909 | 4 / 2 | **89/98** | 1 | 96/98 | **0.906** | **0.94** | **315** | REJECT (A4) - *provisional local default* |
| qwen3-4b-instruct-cascade | Ollama | qwen3:4b-instruct | 1.0 | 0.890 / 0.945 | 4 / 1 | 70/98 | 1 | 96/98 | 0.708 | 0.65 | 437 | REJECT (A3, A4, C2) |
| qwen3-4b-instruct-agreement | Ollama | qwen3:4b-instruct | 1.0 | 0.781 / 0.836 | 4 / 1 | 68/98 | 1 | 96/98 | 0.708 | - | 1151 | REJECT (A3, A4, C2) |
| llama3.2-3b-cascade | Ollama | llama3.2:3b | 1.0 | 0.890 / 0.963 | 6 / 2 | 68/98 | 10 | 96/98 | 0.687 | - | 782 | REJECT (A3, A4, C2) |
| qwen2.5-3b-cascade-verified-qwen3-4b (D5) | Ollama | qwen2.5:3b / qwen3:4b | 1.0 | 0.872 / 0.909 | 3 / 1 | 84/98 | 0 | 96/98 | 0.854 | 0.91 | 14368 | REJECT (A4, A5: two models swap in 6 GB) |
| qwen2.5-3b-cascade-verified-llama3.2-3b (D5) | Ollama | qwen2.5:3b / llama3.2:3b | 1.0 | 0.872 / 0.909 | 3 / 1 | 77/98 | 0 | 96/98 | 0.781 | 0.93 | 2091 | REJECT (A3, A4, C2) |
| qwen2.5-1.5b-cascade-verified-qwen2.5-3b (D5) | Ollama | qwen2.5:1.5b / qwen2.5:3b | 1.0 | 0.854 / 0.890 | 2 / **0** | 66/98 | **0** | 96/98 | 0.666 | 0.71 | 490 | REJECT (A3, C2) - *safest local* |
| deepseek-flash-llm | DeepSeek (compatible) | deepseek-flash, thinking off | 0.963 | 0.890 / 0.963 | 5 / 1 | 86/98 | 0 | 98/98 | 0.877 | - | 997 | REJECT (A4) |
| glm-4.5-air-llm | GLM / Z.ai (compatible) | glm-4.5-air, thinking off | 0.963 | 0.872 / 0.945 | 5 / 1 | 75/98 | 0 | 98/98 | 0.765 | - | 1310 | REJECT (A3, A4) |
| openai-gpt-4.1-nano-llm | OpenAI | gpt-4.1-nano | 1.0 | 0.872 / 0.945 | 12 / 8 | 77/98 | 4 | 98/98 | 0.785 | - | 2062 | REJECT (A3, A4) |
| **deepseek-flash-cascade** | local grammar + DeepSeek | deepseek-flash, thinking off | 1.0 | 0.909 / **0.981** | 4 / **0** | 83/98 | **0** | 96/98 | 0.843 | **0.88 (0 unsafe)** | 1023 | **ACCEPT** (only with D4 adjudication; frozen labels: REJECT A4) |

| openai-gpt-5.4-nano-llm (D6) | OpenAI | gpt-5.4-nano, reasoning none | 1.0 | 0.855 / 0.927 | 7 / 3 | 86/98 | 0 | 98/98 | 0.878 | 0.94 (0 unsafe) | 1609 | REJECT (A4) |
| **openai-gpt-5.4-nano-cascade (D6)** | local grammar + OpenAI | gpt-5.4-nano, reasoning none | 1.0 | 0.891 / 0.964 | 5 / 1 | **89/98** | **0** | 96/98 | 0.906 | **0.96 (0 unsafe)** | 1533 | REJECT (A4, 1 benchmark item) - *recommended hosted OpenAI option (D6 rule)* |
| openai-gpt-5.6-luna-llm (D6) | OpenAI | gpt-5.6-luna, reasoning none | 1.0 | 0.855 / 0.927 | 6 / 2 | 75/98 | 0 | 98/98 | 0.765 | 0.78 (0 unsafe) | 2358 | REJECT (A3, A4) |
| openai-gpt-5.6-luna-cascade (D6) | local grammar + OpenAI | gpt-5.6-luna, reasoning none | 1.0 | 0.891 / 0.964 | 4 / 0 | 72/98 | 0 | 96/98 | 0.729 | 0.79 (0 unsafe) | 2014 | REJECT (A3, C2) |

D6 rows are a **post-hoc extension** re-using the same frozen sets (see
`research/evidence/protocol-deviations.md`, D6-D8): comparative evidence, not
a fresh selection.

### Decisions and reasons

* **No fully local configuration meets every predeclared criterion.** Criterion
  A4 (unsafe executions no higher than the grammar's 0) fails for every local
  language model. The residual errors of the best local cascade are
  *wrong-but-valid* proposals ("we are done here" -> complete step; "mark every
  step as complete" -> complete step; "the fryer is now heated to 185 degrees"
  -> complete step) that no deterministic gate can recognise and that only human
  confirmation stops.
* **Local default (provisional): `qwen2.5:3b` rules-first cascade.** Highest
  held-out accuracy of any configuration (89/98; LLM-routed 0.906; untouched B
  0.94 with 0 unsafe), fastest local LLM p95 (315 ms), and far fewer unsafe
  proposals than any LLM-only run (benchmark 2 adjudicated vs 8). It is shipped
  as a *provisional* default, not an ACCEPT, with confirmation as the stated
  last barrier.
* **Safest local alternative: `qwen2.5:1.5b` cascade verified by `qwen2.5:3b`**
  (0 unsafe on all three sets, but only 0.67 held-out accuracy) - available with
  `LABJARVIS_TEXT_MODEL_NAME=qwen2.5:1.5b LABJARVIS_TEXT_VERIFIER_MODEL_NAME=qwen2.5:3b`.
* **Only ACCEPT: hybrid local grammar + DeepSeek cascade**, and only under the
  post-hoc D4 adjudication (with frozen labels its four "unsafe" items are exactly
  the four unlabelled paraphrases, all executed correctly). Confirmed on the
  untouched B set (0.88, 0 unsafe). It sends utterance text to a hosted
  provider and is therefore the second demo profile, not the default.
* **The v2 contract is the decisive change**, not model size: qwen2.5:1.5b went
  from 9.9% exact (September, v1) to 85.4%; the zero-shot ablation (61.8%)
  shows the few-shot specification matters; `qwen3:4b` works only with
  thinking disabled.
* **Rejected as unusable:** gemma3:4b (grammar-constrained decoding did not
  finish within 20 s on any request), qwen2.5:0.5b and llama3.2:1b (low accuracy,
  most unsafe proposals).
* The cascade keeps the language model meaningful: **96 of 98** supported
  held-out items and 93/100 of held-out B were routed to the LLM; the grammar
  alone solves 2/98.

### LLM-routed demonstration

`research/final_report_evidence/real-chain/chain-demo.json`: real Whisper
(small.en) -> cascade -> validation -> pending -> confirmation -> notebook with
the human-written CLINC150 wording "give me a 10 minute timer" (LLM route,
SET_TIMER 600 s, recorded) and "log the temperature at 37 celsius" (LLM route,
37 degC, recorded), next to a grammar-route measurement and EasyOCR MATCH and
MISMATCH labels. Inputs are synthetic TTS speech and rendered labels.

## Speech and vision

| Candidate | Evidence | Result | Decision |
|---|---|---|---|
| faster-whisper `small.en` | synthetic TTS (75 clips); GPU float16 | WER 0.038, number/unit accuracy 44/44, warm p95 0.39 s GPU / 1.99 s CPU | ACCEPT (provisional; real speech pending) |
| protocol conditioning (prompt / hotwords) | same | WER 0.036 / 0.044 vs 0.038 off; no entity change | not enabled by default (no measured benefit; real speech pending) |
| dual transcription, verifier base.en / tiny.en | same | 0 primary entity errors to catch; nuisance re-asks 5/44 (11%) / 9/44 (20%) | kept optional; recall unmeasurable on this set |
| EasyOCR `english_g2` single frame | 90 rendered labels | 6 false accepts (all digit-for-letter), warm p95 0.16 s GPU / 1.31 s CPU | ACCEPT with documented limitation |
| unanimous 3-frame consensus | same + seeded perturbations | false accepts unchanged (6); true-match coverage 0.67 -> 0.56 | shipped for hands-free capture (never less safe), benefit unproven |
| Tesseract / PaddleOCR / docTR / VLM | - | not run: requires the real photograph set (H3) | pending |

## Post-hoc extension (2026-09-27): generation-matched OpenAI models and deployment profiles

Recorded before running as deviations D6-D8; full numbers and outcomes in
`research/evidence/protocol-deviations.md`; raw results in
`research/interpreter/results/`, `research/final_report_evidence/model-selection/d7-luna-vs-terra.json`
and `research/final_report_evidence/live/d8-*.json`.

* **Model generation mattered, and "bigger/newer" did not mean better here.**
  `gpt-5.4-nano` (reasoning `none`) was the strongest OpenAI option on human
  wording: 89/98 on held-out A and 0.96 on B as a rules-first cascade with 0
  held-out unsafe executions, against 77/98 and 4 unsafe for `gpt-4.1-nano`.
  `gpt-5.6-luna` reached only 72-75/98. Under the rule fixed before the run,
  the `gpt-5.4-nano` cascade is now the recommended hosted OpenAI text option.
  It still fails A4 by one adjudicated benchmark item, so it is not an ACCEPT,
  and it changes neither the local default nor the recommended hybrid
  (DeepSeek) profile.
* **Luna vs Terra (D7, held-out A, reasoning `none`).** Terra 88/98, Luna 75/98
  and 72/98 (two runs); both 0 unsafe. Repeatability: 97.1% (Luna) and 98.3%
  (Terra, second run INCOMPLETE at 120/138 items) identical commands; no item
  changed its safety outcome. Terra costs about 10x Luna per call at list price.
* **Hosted vision (D8a, PROVISIONAL, synthetic labels).** A vision-language
  model is *not* a safer label reader: `gpt-5.4-nano` produced 10 false accepts
  (EasyOCR 6), every one a deliberately defaced decoy "corrected" to the
  expected word. `glm-4.6v-flashx` failed its output contract on 47/90 images
  (reasoning text leaked into the answer) and failed closed.
* **Deployment profiles (D8b).** Fully local, hybrid and fully hosted all
  completed the six demo scenarios; hosted stages added roughly 0.5-1.1 s per
  stage over warm local models on this laptop.
