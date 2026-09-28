# Real-media evaluation package

Everything needed to evaluate LabJarvis on **real** speech and **real**
photographed labels, prepared before any participant media was collected. No
participant media is in this repository and none may be committed: recordings of people are
personal data. Put media under `research/real_media/private/` (git-ignored),
ideally on an encrypted volume.

## What to collect

**Speech** (browser recorder, `webm/opus`, i.e. the production path): record in
the running app with "Hold to speak" or the push-to-talk key, with raw-media
retention switched on only for the collection session
(`LABJARVIS_RETAIN_RAW_MEDIA=true`, then copy the files out and switch it off).

| Set | Count | Content |
|---|---:|---|
| Core commands | 30 | the benchmark command texts (research/data/manifests/text-commands.jsonl, valid ones) |
| Scientific vocabulary | ~20 | reagent names (isopropanol, acetonitrile, sodium chlorate, Tris-HCl...), decimals (21.5), temperatures, uL vs mL, "pH 7.4", negations ("do not record ..."), close terms (ethanol/methanol) |
| Paraphrases | ~10 | the speaker's own wording of supported commands |

Speakers: you plus 4-6 consenting adults. Conditions: quiet laptop microphone
and the same with lab-like background noise played at a fixed level.

**Labels**: >= 100 photographs, >= 10 reagent-name bases, >= 40 non-matches;
printed labels on real bottles/jars filled with water. Cover frontal, ~30
degree angle, glare, low light, curved bottles, correct matches, wrong
chemicals, close names (ethanol/methanol, acetone/acetonitrile,
sodium chloride/chlorate), letter defacements (ETHANOI), digit-for-letter
defacements (METHAN0L), and blank labels. For multi-frame evaluation take three
frames per label in quick succession (`frame_paths`).

## Manifests

Copy the templates in `templates/`, one JSON object per line. Freeze each
manifest before running (record its SHA-256 in
`research/final_report_evidence/manifest-hashes.json` with
`python -m research.real_media.freeze <manifest>`), and have a second person
check the ground truth if possible. Split `dev` vs `test`: tune nothing on
`test`.

## Run

```powershell
# full chain through the production service (speech -> text -> validation -> pending -> confirm; labels -> OCR -> comparator)
.venv/Scripts/python -m research.real_media.run_chain --scenario manifest --speech research/real_media/private/speech.jsonl --labels research/real_media/private/labels.jsonl --speech-device cuda --vision-gpu --out research/final_report_evidence/real-chain/real-media.json
# conditioned vs unconditioned ASR and the dual-transcription cross-check
.venv/Scripts/python -m research.speech.asr_conditioning --manifest research/real_media/private/speech.jsonl --device cuda
# single vs multi-frame consensus on real bursts
.venv/Scripts/python -m research.vision.multiframe_ablation --gpu   # (after pointing MANIFEST at the real label manifest)
```

Metrics produced: WER, number/unit accuracy, end-to-end command success,
unsafe proposals per gate, confirmation outcomes, OCR false accept / false
reject, MATCH/MISMATCH/UNCERTAIN rates, per-stage and total p50/p95 (cold and
warm), RSS/VRAM, and - by running the synthetic manifests through the same
commands - the synthetic-versus-real domain shift.
