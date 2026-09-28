# Dataset manifests

Each dataset is newline-delimited JSON. Keep raw audio and images outside Git and refer to them with paths relative to the manifest. Do not use real patient, experiment, institution, or participant identifiers.

## Speech record

```json
{"id":"speech-001","modality":"speech","synthetic":false,"media_path":"private/audio/001.wav","content_type":"audio/wav","expected_text":"record the temperature as 37 degrees Celsius","duration_seconds":3.2}
```

The reference transcript must be independently checked. Include representative quiet/noisy conditions, accents that the study is ethically able to recruit, short measurement commands, unsupported commands, and natural corrections. Record condition labels in a separate de-identified sampling log until the schema is deliberately extended.

## Text record

```json
{"id":"text-001","modality":"text","synthetic":false,"transcript":"record the temperature as 37 degrees Celsius","expected_intent":"RECORD_MEASUREMENT","expected_parameters":{"kind":"temperature","value":"37","unit":"degrees Celsius"},"active_instruction":"Record incubation temperature."}
```

Include every allowlisted intent, ambiguous and multi-action requests, prompt-injection attempts, missing values, out-of-scope scientific questions, and realistic speech-recognition errors. Label intent and slots before running candidate models; adjudicate disagreements without viewing model predictions.

## Vision record

```json
{"id":"vision-001","modality":"vision","synthetic":false,"media_path":"private/labels/001.jpg","content_type":"image/jpeg","expected_text":"Ethanol","expected_reagent":"ethanol","expected_verification":"MATCH"}
```

Include match, mismatch, and uncertain ground truth; confusable reagent names; blur; glare; rotation; occlusion; low light; handwriting if in scope; and labels with concentration/brand text. Ground truth covers visible text only. It must not claim bottle contents, sterility, expiry, or chemical safety.

## Collection checklist

- Define inclusion/exclusion criteria and target counts before comparing models.
- Keep train/fine-tuning data out of scope; these are evaluation records for pre-trained models.
- Obtain consent and ethics approval/checks where required.
- Assign opaque IDs; store the re-identification key separately, if one is genuinely needed.
- Scan media and manifests for names, voices, faces, screens, addresses, and lab identifiers.
- Record provenance, licence/permission, collection device, environment, date range, and exclusions.
- Make an immutable copy of the final manifest and record its SHA-256 hash in the evidence register.
