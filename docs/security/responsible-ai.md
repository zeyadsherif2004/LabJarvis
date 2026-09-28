# Responsible AI and safety boundaries

## Intended use

LabJarvis assists an adult student or researcher in following a protocol that they have already reviewed and approved. It reduces touch interaction, surfaces exact current/next instructions, proposes structured records from speech, and compares visible label text with a reviewed reagent requirement.

It is an academic prototype for controlled usability/model evaluation, not validated clinical, diagnostic, industrial, or hazardous-laboratory software.

## Human responsibility

The operator remains responsible for:

- deciding whether the protocol and risk assessment are authorized and current;
- checking quantities, units, equipment, environment, SDS information, supervision, and institutional procedure;
- visually checking the physical container and every warning;
- confirming/rejecting model proposals and stopping when uncertain;
- maintaining an independent compliant laboratory record where required.

Supervisors and institutions, not LabJarvis, decide competence, permissions, emergency response, and safe deviations.

## What the models may and may not do

| Model role | Allowed output | Never treated as authority for |
|---|---|---|
| Speech-to-text | proposed transcript and confidence | what was actually said, measurement correctness, authorization |
| Text understanding | allowlisted intent/slot proposal | scientific advice, protocol modification, state mutation, truth |
| OCR | proposed visible label text and confidence | bottle contents, concentration, expiry, contamination, integrity, chemical safety |

Application code revalidates every proposal. Read-only guidance comes from reviewed protocol text, not generated scientific prose. Consequential commands and OCR results require an explicit human decision.

## Known failure modes

- Accent, noise, masks, equipment, and domain terms may degrade speech recognition.
- A schema-valid language-model result can still be semantically wrong or overconfident.
- Glare, curvature, blur, small type, occlusion, handwriting, and confusable names can degrade OCR.
- Similarity thresholds can falsely accept or conservatively reject; aliases can introduce unintended matches.
- Browser media permissions/codecs and CPU/GPU memory/latency vary by machine.
- Protocol extraction can split or order unusual layouts incorrectly, which is why approval is mandatory.
- A spoken response can be missed or misheard; the visible equivalent is authoritative within the application.

## Uncertainty and recovery

- Low-confidence, ambiguous, unsupported, malformed, timed-out, or unavailable-model cases fail without applying an action.
- Label classification has three outcomes. `UNCERTAIN` is a valid safe outcome, not a failure to hide.
- The reagent panel shows the outcome, expected and observed text, comparison score and decision state without relying on color; thresholds and model provenance are shown in the session notebook. Language models report no confidence of their own; command confidence is derived deterministically (ADR 0015).
- Typed commands and uploaded images remain available when microphone/camera access fails.
- A correction appends new evidence and preserves the prior entry.

## Fairness and accessibility considerations

Speech evaluation must include the intended accents, speaking styles, masks/noise conditions, and relevant vocabulary with consent; aggregate WER alone can conceal group-specific harm. Do not infer identity, emotion, competence, or protected characteristics. OCR data should span label typography/lighting relevant to the context without collecting unrelated personal imagery.

Interaction supports keyboard input, visible equivalents to speech, semantic warnings, reduced motion, non-color status, large targets, and permission fallbacks. Automated accessibility checks do not replace testing with representative users and assistive technology.

## Data and evaluation honesty

The included fixtures are synthetic and check the software path. For
real-world performance claims I would need consented, representative samples,
exact configurations and hardware, metrics fixed in advance, error inspection,
and stated limitations. I report unsuccessful candidates and false accepts,
and I do not tune on a test set or remove hard cases without saying so.

User studies require university ethics/consent review, voluntary adult participation, withdrawal instructions, minimal collection, and a deletion plan. Never claim participants, findings, or iteration that did not occur.

## Prohibited or unsupported uses

- Controlling laboratory equipment or executing model-generated actions automatically.
- Replacing supervision, training, risk assessment, SDS/emergency procedures, or regulated records.
- Recommending unreviewed steps, quantities, substitutions, deviations, or chemical compatibility.
- Inferring invisible bottle properties or certifying that a reagent is correct/safe.
- Always-on covert recording, surveillance, worker/student scoring, or identity inference.
- Remote multi-user or high-hazard deployment without a new validated architecture and governance process.

## Release decision rule

No real model should be described as selected until target-hardware evaluation meets predeclared context thresholds, particularly unsupported-command unsafe-action rate and reagent false-accept rate. If evidence is inadequate, keep the adapter provisional, reduce scope, or reject the candidate.

