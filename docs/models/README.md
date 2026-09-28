# Local model adapters and supply chain

LabJarvis has three kinds of model execution:

- `fake` (default) uses visibly labelled deterministic adapters, downloads nothing, and proves application plumbing only.
- `local` injects three genuine pre-trained model adapters into the same application use cases.
- `hosted` (and per-stage hybrids) use OpenAI or OpenAI-compatible providers; see the section at the end and [ADR 0016](../architecture/decisions/0016-per-stage-provider-neutral-hosting.md).

## Provisional runtime manifest

| Domain | Adapter/runtime | Configured model | Role | Approximate model download | Licence/source status | Selection |
|---|---|---|---|---:|---|---|
| Audio | `faster-whisper==1.2.1`, CTranslate2 | `small.en` → `Systran/faster-whisper-small.en`; CPU `int8` (GPU `float16` supported), English, beam 5 | speech-to-text | 486 MB repository | runtime and converted repository marked MIT; upstream Whisper code/weights MIT | Provisional; measured on synthetic speech only |
| Text | rule grammar first, then local Ollama with grammar-constrained JSON schema | `qwen2.5:3b` rules-first cascade (optional verifier, e.g. `qwen2.5:3b` verifying `qwen2.5:1.5b`) | intent-discriminated command proposal | ~1.9 GB | Qwen2.5-3B-Instruct: Qwen Research licence (check before redistribution); 1.5B Apache-2.0 | Provisional default: highest held-out accuracy (89/98) but fails the strict unsafe criterion A4; see `docs/evaluation/model-selection-record.md` |
| Image | `easyocr==1.7.2`, Torch | `english_g2` + default CRAFT detector; CPU (GPU supported) | visible reagent-label OCR | ~14.0 MB recognizer zip + ~77.3 MB detector zip | EasyOCR Apache-2.0; upstream config publishes MD5 values; component/training-data review still required | Provisional; measured on rendered labels only |
| Voice output | `kokoro-onnx==0.6.1`, ONNX Runtime (CPU) | Kokoro-82M `kokoro-v1.0.onnx` (full precision) + `voices-v1.0.bin`, voice `af_heart` | spoken replies (presentation only, not part of the record) | 325.5 MB + 28.2 MB, SHA-256 pinned in `infrastructure/speech_output.py` | Kokoro-82M Apache-2.0; kokoro-onnx MIT; phonemizer and the eSpeak NG bundled by espeakng-loader are GPL-3.0 | Chosen over int8 by a CPU benchmark ([ADR 0020](../architecture/decisions/0020-local-neural-voice-output.md)); browser voice is the fallback |

Sizes are upstream catalog/HTTP artifact sizes observed on 2026-07-16, not
measured installed disk/RAM. Python ML dependencies are substantially larger.
Latency on the project laptop (CPU and GPU) is in [GPU setup](gpu-setup.md);
accuracy evidence and its limits are in the
[model-selection record](../evaluation/model-selection-record.md).

## Observed local readiness on 2026-08-19 (historical)

> Superseded: on 2026-09-26/27 all three local models were installed and ran
> end to end through the production service (`python scripts/tasks.py
> smoke-local`, `research/final_report_evidence/real-chain/`).

In the 2026-08-19 baseline, `python scripts/setup_models.py check` found faster-whisper `small.en` and the
EasyOCR `english_g2`/CRAFT weights cached with their Python dependencies
installed. This proves installation/cache presence only: neither adapter was
inference-tested in the final pass. The configured loopback Ollama service was
unreachable and `qwen2.5:1.5b` was not cached, so the intended local profile was
not ready and no combined three-domain session was run **in that baseline**.
Later model-selection and chain results are documented in the
[model-selection record](../evaluation/model-selection-record.md) and
[evidence index](../../research/final_report_evidence/INDEX.md).

## Verified primary source record

- [faster-whisper repository and MIT licence](https://github.com/SYSTRAN/faster-whisper), with `small.en` mapped to the [Systran converted model](https://huggingface.co/Systran/faster-whisper-small.en). The catalog showed 486 MB and revision prefix `d1d751a`; capture the full resolved cache revision at experiment time.
- [OpenAI Whisper repository](https://github.com/openai/whisper) states its code and model weights are MIT licensed.
- [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md) documents a JSON schema in `format`; application-side Pydantic/domain validation remains mandatory.
- The [Ollama Qwen2.5 registry](https://registry.ollama.com/library/qwen2.5/tags) listed `qwen2.5:1.5b-instruct` as 986 MB with digest prefix `65ec06548149`. A prefix/current tag is not adequate immutable evidence; record `ollama show`/manifest output from the installed artifact.
- The upstream [Qwen2.5-1.5B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) identifies 1.54B parameters and Apache-2.0.
- [EasyOCR](https://github.com/JaidedAI/EasyOCR) is Apache-2.0 and its [official model hub](https://www.jaided.ai/easyocr/modelhub/) links the weights. Upstream `config.py` records `english_g2` MD5 `5864788e1821be9e454ec108d61b887d` and CRAFT MD5 `2f8227d2def4037cdb3b34389dcf9ec1`.

These records describe upstream metadata, not a completed legal review. Check model cards/licences again immediately before publication or redistribution. LabJarvis never redistributes weights.

## Installation

1. Install [Ollama](https://ollama.com/download) and ensure its local service is available.
2. Explicitly install pinned optional dependencies, download the configured Whisper and EasyOCR weights and the Kokoro voice (verified against its pinned SHA-256; `setup_models.py kokoro` fetches only the voice), and `ollama pull` the configured text model:

   ```powershell
   python scripts/tasks.py models
   ```

3. Inspect without allowing another download:

   ```powershell
   .venv/Scripts/python scripts/setup_models.py check
   ollama list
   ollama show qwen2.5:3b --modelfile
   ```

4. Keep the default loopback Ollama URL, run `python scripts/tasks.py dev`, then choose **Local** (or **Custom** for GPU) in **Settings**, save and press **Restart LabJarvis**.

Model downloads are disabled during normal application startup. `LABJARVIS_ALLOW_MODEL_DOWNLOAD=true` is an explicit setup opt-in, not a production recommendation. `LABJARVIS_ALLOW_REMOTE_MODEL_SERVICE=false` rejects non-loopback Ollama by default.

## Experiment identity capture

Before every real smoke/benchmark, append an evidence record containing:

```text
Git commit and dirty/clean state
Python/OS/CPU/RAM/GPU/driver
requirements-models.lock SHA-256
faster-whisper package version + resolved Hugging Face snapshot/revision + file hashes
Ollama version + full model manifest/digest + quantization
EasyOCR version + english_g2/CRAFT file hashes
all LABJARVIS speech/text/vision/threshold/timeout/concurrency settings
warm/cold run definition and dataset/config hashes
```

Do not copy model-cache paths or media containing personal/lab information into public evidence.

## Smoke tests

Use consented, non-sensitive WAV and JPEG fixtures outside Git:

```powershell
$env:LABJARVIS_RUN_MODEL_TESTS="1"
$env:LABJARVIS_SMOKE_AUDIO="C:\private-fixtures\command.wav"
$env:LABJARVIS_SMOKE_IMAGE="C:\private-fixtures\label.jpg"
$env:LABJARVIS_SMOKE_SCANNED_PDF="C:\private-fixtures\scanned-protocol.pdf"
python scripts/tasks.py test-models
```

Without the opt-in, all four smoke tests are collected and skipped. They cover
speech, structured text, label OCR, and scanned-document OCR. A smoke pass
proves adapter wiring, not quality.

## Candidate comparison

Two comparison paths exist:

- **Text interpreter (done):** `research/interpreter/run_sweep.py` with
  `research/interpreter/configs/final-sweep.yaml` and `d6-d7-openai.yaml`, under
  the predeclared [model-selection protocol](../evaluation/model-selection-protocol.md);
  results in the [model-selection record](../evaluation/model-selection-record.md).
- **General harness (for real media):** `evaluation/configs/local-candidates.yaml`
  compares `tiny.en`, `base.en`, and `small.en`; Qwen2.5 0.5B and 1.5B; and
  EasyOCR `english_g2`/`latin_g2`. Run it once consented real media exist (see
  `research/real_media`). Select the smallest
  candidate meeting context requirements for accuracy, false acceptance,
  unsupported-command safety, latency, memory, and recovery.

## Hosted and local-server options (2026-09-27)

- **Reasoning models.** `LABJARVIS_HOSTED_TEXT_REASONING_EFFORT` (and
  `LABJARVIS_TEXT_VERIFIER_REASONING_EFFORT`) sends `reasoning.effort` on the
  OpenAI Responses API or `reasoning_effort` on an OpenAI-compatible chat API.
  Reasoning tokens count against `LABJARVIS_HOSTED_TEXT_MAX_OUTPUT_TOKENS`; a
  response cut off by that limit ends "incomplete" and fails closed (no
  proposal). Documented values (checked 2026-09-27): gpt-5.4-nano `none`
  (default) to `xhigh`; gpt-5.6-luna and gpt-5.6-terra `none` to `max`, default
  `medium`. The value used is recorded in each inference's provenance.
- **OpenAI and DeepSeek side by side.** Both are named providers with their
  own key (`LABJARVIS_OPENAI_API_KEY` or the older `LABJARVIS_HOSTED_API_KEY`;
  `LABJARVIS_DEEPSEEK_API_KEY`), endpoint and options (DeepSeek: JSON-object
  output and thinking disabled by default), so the text stage can be switched
  between Local, OpenAI and DeepSeek in Settings. DeepSeek serves the text stage
  only. The generic `openai_compatible` provider (GLM, gateways, local servers)
  keeps using the per-stage `LABJARVIS_HOSTED_<STAGE>_BASE_URL`/`_API_KEY`.
- **Verifier on any provider.** `LABJARVIS_TEXT_VERIFIER_PROVIDER` selects local
  Ollama (default), `openai`, `deepseek` or `openai_compatible` for the
  cascade's second model, with its own base URL, key, label, output mode and
  reasoning effort.
- **Local OpenAI-compatible servers.** A text or vision endpoint on
  `localhost`, `127.0.0.1` or `::1` (vLLM, llama.cpp server, LM Studio) uses
  the compatible adapter but is labelled **local** in readiness, the header
  chips, Diagnostics and every inference row.
- **Live label scanning** is local-OCR only: it probes a frame every few hundred
  milliseconds, so it is refused when the vision stage is hosted.

## Cache and licence hygiene

Model caches, Ollama blobs, media fixtures, databases, and raw evaluation output are ignored and must not be committed. Retain upstream notices where required. Re-run dependency audits and review transitive ML/runtime licences, especially Torch/CUDA/FFmpeg/system components, for the actual deployment platform.
