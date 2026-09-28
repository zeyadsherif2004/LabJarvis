# LabJarvis documentation

I keep setup instructions and the command reference in the repository
[`README.md`](../README.md). The pages below cover the application,
design decisions, evaluation methods, and measured results.

## Current - engineering

- [Architecture overview](architecture/overview.md) and [selected architecture decisions](architecture/decisions/README.md)
- [Developer setup](development/setup.md), [testing](development/testing.md), [API](development/api.md), [migrations](development/migrations.md), and [troubleshooting](development/troubleshooting.md)
- [Model adapters](development/model-adapters.md), [model manifest](models/README.md), [GPU setup](models/gpu-setup.md), [evaluation harness](../evaluation/README.md), and [dataset manifests](../evaluation/datasets/README.md)
- [Threat model](security/threat-model.md), [responsible AI](security/responsible-ai.md), and [privacy/retention operations](operations/privacy-retention.md)
- Configuration reference: [`.env.example`](../.env.example)

## Current - operator and evaluation

- [User guide](user-guide.md), [local deployment](operations/local-deployment.md), and [accessibility checklist](accessibility-checklist.md)
- [Model-selection protocol](evaluation/model-selection-protocol.md) and [model-selection record](evaluation/model-selection-record.md)

## Results and provenance

- [Evidence index](../research/final_report_evidence/INDEX.md) and [protocol deviations](../research/evidence/protocol-deviations.md). The [readiness script](../scripts/readiness.py) runs software checks in a disposable copy and writes results to ignored local output.
- [Real-voice session record](../research/final_report_evidence/real-world/session-2026-09-28-saline.md) and [model-selection record](evaluation/model-selection-record.md)

Generated fixtures and screenshots are explicitly labelled. Real-media,
participant, manual-accessibility and submission-video evidence is described
only where it exists.
