# Research benchmark data card

All datasets were constructed programmatically for this study on 2026-07-17.
They are clearly synthetic, contain no personal data, no human speech, no
photographs, and no hazardous information, and are redistributable with the
repository under the MIT licence.

## Provenance and generation

| Dataset | Manifest | Size | Generator | Media |
|---|---|---|---|---|
| Text commands | `manifests/text-commands.jsonl` | 81 samples | `generators/generate_text_dataset.py` | none (text only) |
| Speech clips | `manifests/speech-clips.jsonl` | 75 WAV clips (60 clean, 15 noise-augmented) | `generators/generate_speech_dataset.py` | `media/speech/` |
| Label images | `manifests/label-images.jsonl` | 90 PNG images | `generators/generate_vision_dataset.py` | `media/vision/` |
| Scenarios | `manifests/scenarios.json` | 24 scenarios (117 events) | `generators/generate_scenarios.py` | none |

- **Speech** is machine-synthesised with the Windows SAPI voices Microsoft
  David Desktop and Microsoft Zira Desktop (22.05 kHz WAV), optionally with
  seeded Gaussian noise at 5 dB SNR (seed 20260717). It is labelled
  `synthetic_speech: true` and must never be presented as human-speaker
  evidence.
- **Label images** are programmatically drawn mock labels (640x320 PNG,
  Arial/Segoe Script text on a bordered white label). Degradations: rotation
  (15/30 deg), Gaussian blur (radius 3/8), low contrast, occlusion bar, glare
  overlay, 5x downscale-upscale, blank label. `truth` is the physical ground
  truth of the drawn label (`match`/`mismatch`/`unreadable`); the misspelled
  defacements (e.g. `METHAN0L`) are ground-truth `mismatch`.
- **Text commands** cover 9 categories: valid measurements (15), observations
  (6), timers (5), control/navigation (19), grammar-uncovered paraphrases (10),
  invalid-but-parseable values/units (8), out-of-scope requests (10),
  multi-command utterances (3), incomplete fragments (5).
  `expected_parameters` use the canonical post-validation form;
  `action_valid: false` marks commands whose execution would be an error.
- **Scenarios** script 24 event sequences over a synthetic three-step protocol
  in 8 families (happy, corrupted value, state conflict, invalid value, label
  mismatch, label uncertainty, unsupported, mixed). Deliberately corrupted
  transcripts are flagged `perturbed: true`; they are controlled synthetic
  perturbations, not recorded model output.

## Known limitations

- Rendered labels and TTS speech are cleaner than real laboratory captures;
  absolute error rates will differ on real media. The benchmark supports
  *policy comparisons under identical inputs*, not deployment-grade
  performance claims.
- Chemical names are used only as text strings; no dataset item verifies or
  implies real chemical contents or safety.
- English only; two synthetic voices; no accent or microphone diversity.

## Intended use

Reproducible evaluation of orchestration/intervention policies in LabJarvis
and comparable systems. Not suitable for claims about human-speech
recognition accuracy, real-label OCR accuracy in the field, or user
experience.

## Additions in the final remediation pass (2026-09-26/27)

| Dataset | Location | Size | Evidence class | Licence / authorship |
|---|---|---|---|---|
| Development interpreter pool | `manifests/text-dev.jsonl` | 37 authored items (12 = few-shot examples) | synthetic, development only | authored by the project developer |
| Held-out human-written paraphrases A | `heldout/human-paraphrases.jsonl` (+ `SOURCES.json`) | 138 | public human-written text | CLINC150 (CC BY 3.0), CookDial (MIT), Wizard of Tasks (CC BY-SA 4.0) |
| Held-out human-written paraphrases B | `heldout/human-paraphrases-B.jsonl` | 139 (4 supported intents) | public human-written text, untouched confirmation set | CLINC150 train split, Wizard of Tasks train/validation |
| Benchmark slot adjudication | `manifests/text-commands-slot-adjudication.json` | 4 items | adjudication (post-results, deviation D4) | project developer |
| Grounding test protocols | `../grounding/protocols-test.json` + `cases.jsonl` | 4 protocols, 50 questions | public protocol text (steps verbatim) + authored questions | OpenWetWare, CC BY-SA 3.0, pinned revisions |
| Chain demo clips | `media/chain/*.wav` | 6 clips | synthetic TTS (Windows SAPI) | generated |
| Wake-phrase clips | `media/wake/*.wav` | 32 clips | synthetic TTS | generated |

The held-out sets are text written by people, not speech; the human-written
wording is preserved exactly. Coverage gaps (no held-out items for pause,
resume, end, verify reagent, correct entry, current step or dictated
observations; only 2 measurements) are reported with every result.
CC BY-SA material (Wizard of Tasks, OpenWetWare) is redistributed under the
same licence with attribution in `heldout/SOURCES.json` and
`grounding/protocols-test.json`.

## Real media

This July evaluation dataset contains no real speech recording or photograph.
The separate final-report evidence includes one session export from my own
voice and a handwritten label, but no raw audio or label image. The
collection protocol, consent route, manifests and runners are in
`research/real_media/README.md`; media stays under the git-ignored
`research/real_media/private/`. Consent: written informed consent under the
approved university ethics process; participants
are identified only by codes (P01...); recordings are personal data, kept
encrypted and deleted after the report is marked unless consent says otherwise.
