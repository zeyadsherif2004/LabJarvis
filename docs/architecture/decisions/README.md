# Architecture decision records

These records explain the design choices that are still useful for understanding
LabJarvis. General architecture, data retention, and setup are documented in
the [overview](../overview.md), [privacy guide](../../operations/privacy-retention.md),
and [developer setup](../../development/setup.md).

| ADR | Decision | Status |
|---|---|---|
| [0004](0004-deterministic-state-machine.md) | Deterministic state machine, not an autonomous agent | Accepted |
| [0011](0011-deterministic-core-realtime-shell.md) | Deterministic core, realtime conversational shell | Accepted |
| [0012](0012-selective-high-integrity-transcription.md) | Selective high-integrity transcription | Accepted |
| [0013](0013-deterministic-protocol-grounding.md) | Deterministic protocol grounding with abstention | Accepted |
| [0014](0014-spoken-confirmation-requires-fresh-human-audio.md) | Spoken confirmation is a fresh microphone recording taken after the readback, never a function call (extends 0004, 0011, 0012) | Accepted |
| [0015](0015-deterministic-first-cascade.md) | Deterministic-first cascade and an intent-discriminated command contract | Accepted |
| [0016](0016-per-stage-provider-neutral-hosting.md) | Per-stage, provider-neutral model execution | Accepted |
| [0017](0017-hands-free-listening-and-voice-only-confirmation.md) | Hands-free listening, voice-only confirmation and live label scanning (extends 0011, 0012, 0014) | Accepted |
| [0018](0018-hands-free-bench-workflow.md) | Hands-free and eyes-free bench workflow: session-long listening, timer alarms, voice Q&A, skip/correct/timer/session control by voice, one profile, kiosk launch (extends 0014, 0017) | Accepted |
| [0019](0019-in-app-configuration-applied-at-startup.md) | In-app Settings: saved choices applied only at startup, supervised restart (extends 0016) | Accepted |
| [0020](0020-local-neural-voice-output.md) | Kokoro as the local neural voice for spoken responses, browser speech as fallback (extends 0018) | Accepted |
