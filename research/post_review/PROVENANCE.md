# September 2026 evaluation provenance

Created: 2026-09-01

This record covers the September 2026 post-review computational work. The
original July E1--E4 study remains separately attributed and was not
recomputed or overwritten. All paths are relative to the repository root.

## Study boundary

| Item | Value |
|---|---|
| Original study date | 2026-07-20 |
| Original evaluated commit | `d5b7f04837c647a875ab34bc743d8b5dda38a79a` |
| Original result manifest | `post_review/original-result-hashes.json` |
| Original raw results | `results/raw/e1-speech-results.json`, `results/raw/e2-text-results.json`, `results/raw/e3-vision-results.json`, `results/raw/e4-scenario-results.json` |
| Post-review base commit | `1399c89a496bc4e7c6075dd2ef82e60d48e09e69` |
| Evidence class | Synthetic/source-independent post-review computations, not human or real-world validation |

The July raw-result SHA-256 values are:

| File | SHA-256 |
|---|---|
| `results/raw/e1-speech-results.json` | `62fdd510b807bc9dbecc06b286affd4e0691094a2c2ac8792c5314fe929620f5` |
| `results/raw/e2-text-results.json` | `f11106f654fd8cd5c78d196a227a010b949ac916ad7cf4e551cfd77b78255eb8` |
| `results/raw/e3-vision-results.json` | `bdebed4136e531e0c7ceb19499cd2446b793b68b9e6692f4c55459b051eb8951` |
| `results/raw/e4-scenario-results.json` | `58f2ee647443542502bfa1f76da973b936791f6ad0ca51dc7da22e65148333cb` |

## Software environment

| Item | Value |
|---|---|
| Operating system (run records) | Windows-11-10.0.26200-SP0 |
| Repository Python | 3.12.13 |
| Ollama | 0.33.2 |
| faster-whisper | 1.2.1 |
| EasyOCR | 1.7.2 |
| ONNX Runtime | 1.27.0 |
| Pydantic | 2.13.4 |
| Requests | 2.34.2 |
| NumPy | 2.5.1 |
| SciPy | 1.18.0 |
| pytest | 9.1.1 |

The hosted credential was read only from `LABJARVIS_HOSTED_API_KEY`; no
credential value was printed or stored.

## Local model digests

Ollama reported these digests on 2026-09-01:

| Model | SHA-256 digest | Size | Ollama `modified_at` (local time) |
|---|---|---:|---|
| `qwen2.5:0.5b` | `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67` | 397,821,319 B | 2026-09-01 16:11:18 +03:00 |
| `qwen2.5:1.5b` | `65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b` | 986,061,892 B | 2026-09-01 16:17:03 +03:00 |

The hosted text model was `gpt-5.6-terra` through the
`openai-responses-json-schema` adapter.

## Frozen inputs

| Input | SHA-256 |
|---|---|
| `data/manifests/text-commands.jsonl` | `4c903406d1ddf214efeabb7d91aae60af80a591c6a4409d4fd99190d6d69231f` |
| `data/manifests/speech-clips.jsonl` | `87ded65f9957d7f7154a0bcc5880a687bbab4ba89ecbae09d9ad87723a25ef4b` |
| `data/manifests/label-images.jsonl` | `aeeb1a73cb16e22fbb1294548cf67c211094241ea134dde0155e3a08edebbec1` |
| `post_review/configs/camera-ready.yaml` | `eba8cf69b44f025b790bca4dd4575541e23dd603192d687fd198ce2cbbc2a296` |
| `post_review/public_protocols/cases.jsonl` | `ce6d8df27f40dbfca1f704ad35c28f5e343affcebb96969250f5b18007ed85f0` |
| `post_review/public_protocols/sources.json` | `57d44ff58ab56f5954e8fe668735bd325ed20850c651987ca02c27a4d4d0085f` |

The public sources were retrieved on 2026-09-01 and licensed CC BY 4.0. Their
declared source-content hashes are:

| Source | DOI | Source-content SHA-256 |
|---|---|---|
| Transformation | `10.17504/protocols.io.hcnb2ve` | `a69b0417c99e899fdd327fcc80e842cfcc8ce6f93ff944c3dadb6a629614ded1` |
| PLATERO plate-reader calibration | `10.17504/protocols.io.kxygxzjz4v8j/v1` | `91f14ee48f61a20588969f0c5f9e6f611aecee93bceb2c2be736092936ea293a` |
| 3D distance probes | `10.17504/protocols.io.3byl4zkwjvo5/v1` | `5c3c725c9ed89ebd16180ed3ec1347ba3b04af4be9d11f15fecec282f8967b51` |

`cases.jsonl` contains nine command cases and nine grounding cases. The
command-case runs used the nine commands; the grounding run used the nine
grounding cases. The cases were frozen before the recorded candidate runs.

## Run commands and times

Commands were run from the repository root with the repository virtual
environment. The hosted commands additionally read the existing credential
from the local environment.

### Interpreters

| Output | UTC result time | Command |
|---|---|---|
| `post_review/results/rule-interpreter.json` | 2026-09-01 12:52:23 | `python -m research.post_review.run_interpreters --kind rule --model fake-text-rules-v1 --output research/post_review/results/rule-interpreter.json` |
| `post_review/results/hosted-gpt-5.6-terra.json` | 2026-09-01 12:58:04 | `python -m research.post_review.run_interpreters --kind hosted --model gpt-5.6-terra --output research/post_review/results/hosted-gpt-5.6-terra.json` |
| `post_review/results/ollama-qwen2.5-0.5b.json` | 2026-09-01 14:35:23 | `python -m research.post_review.run_interpreters --kind ollama --model qwen2.5:0.5b --output research/post_review/results/ollama-qwen2.5-0.5b.json` |
| `post_review/results/ollama-qwen2.5-1.5b.json` | 2026-09-01 14:37:34 | `python -m research.post_review.run_interpreters --kind ollama --model qwen2.5:1.5b --output research/post_review/results/ollama-qwen2.5-1.5b.json` |

The two files in
`post_review/results/superseded-invalid-local-schema/` preserve earlier Qwen
runs made before the strict closed-variant command-schema fix. They are
provenance only and are not used in the reported comparisons.

### Public protocol set

| Output | UTC result time | Command |
|---|---|---|
| `post_review/results/public-protocol-rule.json` | 2026-09-01 13:08:23 | `python -m research.post_review.run_interpreters --kind rule --model fake-text-rules-v1 --manifest research/post_review/public_protocols/cases.jsonl --output research/post_review/results/public-protocol-rule.json` |
| `post_review/results/public-protocol-hosted.json` | 2026-09-01 13:08:57 | `python -m research.post_review.run_interpreters --kind hosted --model gpt-5.6-terra --manifest research/post_review/public_protocols/cases.jsonl --output research/post_review/results/public-protocol-hosted.json` |
| `post_review/results/public-protocol-grounding.json` | 2026-09-01 13:07:28 | `python -m research.post_review.evaluate_protocol_breadth` |

### Media candidates

| Output | UTC result time | Command |
|---|---|---|
| `post_review/results/speech-tiny-en.json` | 2026-09-01 14:29:45 | `python -m research.post_review.run_media_candidates --modality speech --model tiny.en --output research/post_review/results/speech-tiny-en.json` |
| `post_review/results/speech-base-en.json` | 2026-09-01 14:30:44 | `python -m research.post_review.run_media_candidates --modality speech --model base.en --output research/post_review/results/speech-base-en.json` |
| `post_review/results/speech-small-en.json` | 2026-09-01 13:51:15 | `python -m research.post_review.run_media_candidates --modality speech --model small.en --output research/post_review/results/speech-small-en.json` |
| `post_review/results/vision-english-g2.json` | 2026-09-01 14:29:22 | `python -m research.post_review.run_media_candidates --modality vision --model english_g2 --output research/post_review/results/vision-english-g2.json` |
| `post_review/results/vision-latin-g2.json` | 2026-09-01 14:30:40 | `python -m research.post_review.run_media_candidates --modality vision --model latin_g2 --output research/post_review/results/vision-latin-g2.json` |

### Analysis

The analysis seed was `20260901`, with 10,000 bootstrap resamples per paired
confidence interval. Regenerate the processed summaries with:

```powershell
python -m research.post_review.analyze
```

The processed outputs were regenerated at 2026-09-01 14:47:38--14:47:40 UTC.

## Raw post-review result hashes

| File | SHA-256 |
|---|---|
| `post_review/results/rule-interpreter.json` | `80fffb6c9e1a7532dedecfe3b69baf0ba93b5c11feecc1c3469a2c5a19842cc2` |
| `post_review/results/hosted-gpt-5.6-terra.json` | `7701b1ab957ff37ee75dd80a6a5953b1e3a64b67d6622611ea038cb84ab4ca18` |
| `post_review/results/ollama-qwen2.5-0.5b.json` | `0a8fa289fac2866da86e3ac36b2d4f0a759a8c9db973c87eb22abdcc7f6a73c3` |
| `post_review/results/ollama-qwen2.5-1.5b.json` | `8ad1bf4a2e6a75f54006990ecb41a014d8aca6b70fc5634e797ed0ffdcaa1b06` |
| `post_review/results/public-protocol-rule.json` | `68bda907dfbcbcabadd5e08a905c2e0c78d9b69f842e53a078620c6fb7a2f726` |
| `post_review/results/public-protocol-hosted.json` | `9e15072dcd19b32302fea1189414f25a2c81d4d72e7c121857af64ec823b32f5` |
| `post_review/results/public-protocol-grounding.json` | `76ce5cd95feb56babdcf28672f17cd830071af187261d4683c79994d42ca3c29` |
| `post_review/results/speech-tiny-en.json` | `3ff6fb9333c0aa38147eb2d74aac9f92c1932fe8ec22d0fd772aab83e5953648` |
| `post_review/results/speech-base-en.json` | `d7ffe28bbce43517f8982c6c1c9427d1e27418e2a42c824fa7b8a664c84bec69` |
| `post_review/results/speech-small-en.json` | `f7a81e517018ee276ca34dc988b73b2788211e9ec822c110a20f80288ed8e7a0` |
| `post_review/results/vision-english-g2.json` | `69125b109102fdd610159601575bfdc38b01f794192e68ec6b861db4a12021ff` |
| `post_review/results/vision-latin-g2.json` | `09a326e0092ef5536db625297b98612d337ed67ce7683ab2865f51151a8e204b` |
| `post_review/results/superseded-invalid-local-schema/ollama-qwen2.5-0.5b.json` | `341a6046732a1207b24a952692192b0a9d5070cbdf475992ec8f455efda60f19` |
| `post_review/results/superseded-invalid-local-schema/ollama-qwen2.5-1.5b.json` | `fb2f58e3404629b04b1a324ba12f498d5577e14090121172c09fd09828ff6127` |

Processed output hashes:

| File | SHA-256 |
|---|---|
| `post_review/results/processed/interpreter-summary.csv` | `5da1de76d1d3f85e08a7bd85a6d4cefbacbba33897f2d6dbad744193928f6a0c` |
| `post_review/results/processed/component-ablations.csv` | `8822e013876aa15bb5cb9657e7c0b9d3c0d021926d030e174bdc3a55dc0015e1` |
| `post_review/results/processed/postreview-interpreters.tex` | `3a1d845676dc71588b6271f297a6bd9af70f84897b35062551042f2248d22521` |
| `post_review/results/processed/protocol-grounding.csv` | `631caf2b6dbcdc0b7ce9329658e7296b9ee925d9224f0d9e66d7d09362277122` |
| `post_review/results/processed/speech-candidates.csv` | `e68070ca9bc11e5460f6477911e74ec961066b18b1b34cce7d82e1874636936b` |
| `post_review/results/processed/vision-candidates.csv` | `bf00c0e8f1db0f5ac48e98656c1bf0b8f4ef510309069100df1f3e96c6687d66` |
| `post_review/results/processed/post-review-stats.json` | `2cbfd9133d83b73dfdad6792b04471bc1058a9811cfd479d4760c64a36f42429` |

## Dirty-worktree state

The post-review runs record that the working tree was dirty at base commit
`1399c89a496bc4e7c6075dd2ef82e60d48e09e69`. At result freeze:

* `git diff --no-ext-diff | SHA-256`: `bb744f3aaa662cbf61db045b64faf0e37f4ea2d91392c7db36331f8929e2f640`
* `git status --porcelain=v1 | SHA-256`: `4c298fed0c2ef8eeadd04341c87a2f0d6fbe85de663e9ff7656f297a0c15ea2d`

These hashes do not hash the contents of untracked files. They preserve the
tracked-diff and path/status snapshot for interpretation of the run records.

At final package preparation:

* `git diff --no-ext-diff | SHA-256`: `f4c368ba0fd21041f1bff17c837ff9e231f5bb021167983b2424433888067be9`
* `git status --porcelain=v1 | SHA-256`: `89d7b21fd2e3fd338d355f35cfe5f0deb2b656202525bc72035d7615d76f830c`

## Verification commands

Focused tests:

```powershell
python -m pytest research/post_review apps/api/tests/unit/evaluation/test_hosted_candidates.py apps/api/tests/unit/infrastructure/test_local_models.py apps/api/tests/unit/infrastructure/test_hosted_text.py -q
```

Broader isolated unit run:

```powershell
$env:LABJARVIS_MODEL_MODE='fake'
python -m pytest apps/api/tests/unit research/post_review -q
```

Lint:

```powershell
python -m ruff check research/post_review research/experiments apps/api/src/labjarvis/evaluation/run.py apps/api/src/labjarvis/infrastructure/command_contract.py apps/api/src/labjarvis/infrastructure/real_models.py apps/api/tests/unit/evaluation/test_hosted_candidates.py apps/api/tests/unit/infrastructure/test_hosted_text.py apps/api/tests/unit/infrastructure/test_local_models.py
```

The focused suite passed 49 tests, the broader suite passed 332 tests, and the
lint command passed. Original-result hashes were checked before packaging.

## Evidence limits

These results do not establish human performance, real microphone robustness,
real photographed-label robustness, live laboratory safety, confirmation
effectiveness by actual operators, usability, or workload. Public protocols are
source-independent but not human-adjudicated. Media models were compared only
on the existing synthetic speech clips and rendered label images.
