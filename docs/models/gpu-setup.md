# GPU setup for local speech and vision (verified 2026-09-27)

CPU is the conservative default and remains fully supported. On the project
laptop (NVIDIA RTX 4050 Laptop GPU, 6 GB, driver 616.92, CUDA UMD 13.4) both
local perception models were verified to run on the GPU through the production
adapters. Evidence: `research/final_report_evidence/devices/device-benchmark.json`
(synthetic TTS clips and rendered labels; real latency, 20 warm calls each).

| Model | Device | Cold (ms) | Warm p50 (ms) | Warm p95 (ms) |
|---|---|---:|---:|---:|
| faster-whisper `small.en` | CPU int8 | 3182 | 1914 | 1994 |
| faster-whisper `small.en` | **CUDA float16** | 2225 | **378** | **391** |
| EasyOCR `english_g2` | CPU | 2579 | 647 | 1312 |
| EasyOCR `english_g2` | **CUDA** (370 MB) | 4881 | **84** | **163** |

## Setup commands (Windows, Python 3.12 venv)

```powershell
# PyTorch with CUDA (EasyOCR). cu130 wheels exist for torch 2.13.0.
.venv/Scripts/python -m pip install --no-deps --force-reinstall "torch==2.13.0+cu130" "torchvision==0.28.0+cu130" --index-url https://download.pytorch.org/whl/cu130
# CUDA 12 cuBLAS for CTranslate2 (faster-whisper). cuDNN is shared with PyTorch.
.venv/Scripts/python -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
.venv/Scripts/python scripts/benchmark_devices.py --out research/final_report_evidence/devices/device-benchmark.json
```

Then run with:

```powershell
$env:LABJARVIS_SPEECH_DEVICE="cuda"; $env:LABJARVIS_SPEECH_COMPUTE_TYPE="float16"; $env:LABJARVIS_VISION_GPU="true"
```

## One process, two CUDA stacks

CTranslate2 4.8.1 is built against CUDA 12 and cuDNN 9; the available PyTorch
wheel is CUDA 13 with its own cuDNN 9. Loading the CUDA-12 cuDNN wheel first
made EasyOCR fail in the same process (`CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`).
`register_cuda_libraries()` in `infrastructure/real_models.py` therefore imports
PyTorch first and registers only the cuBLAS directory, so both models share
PyTorch's cuDNN. If the GPU libraries are missing the adapter now reports
"GPU runtime libraries ... missing; use LABJARVIS_SPEECH_DEVICE=cpu" instead of
the old, misleading "audio could not be decoded".

The Ollama text model uses the GPU through Ollama itself; with qwen2.5:3b
loaded (2.2 GB) plus Whisper and EasyOCR the 6 GB card has headroom. Loading two
Ollama models at once (the verified cascade with a 4B verifier) caused swapping
and p95 of about 14 s, which is why that configuration is not the default.
