# ADR 0011: Deterministic core, realtime conversational shell

**Status:** Accepted  
**Date:** 2026-08-18

## Context

ADR 0004 established that LabJarvis uses a deterministic state machine rather
than giving models authority over session state. Model output is a proposal
that application code revalidates before it can affect anything.

What changed is the interaction model. Push-to-talk requires a hand: the
operator presses, speaks, releases. In a sterile workflow that is precisely the
hand that is unavailable — the entire premise of the project. Making the
assistant genuinely usable without touching anything requires a continuously
listening, low-latency, interruptible speech-to-speech layer, and that layer is
a large language model with a persistent conversational context.

That creates a real tension. A realtime model holds conversation state, will
happily assert things it inferred several turns ago, and can be induced to claim
an action succeeded. None of that is acceptable near an experimental record.

The question this ADR answers is: how do we get the interaction quality without
letting the model near the authority?

## Options considered

1. **No realtime layer; keep push-to-talk.** Safest and simplest. Rejected
   because it does not solve the problem the project exists to solve — the
   advertised hands-free workflow was, in practice, tap-to-start and
   tap-to-stop.

2. **Realtime model as the orchestrator.** Let the realtime session hold protocol
   state, decide what is legal, and call the database directly. This is the
   architecture most voice-agent examples show. Rejected: it makes the model the
   source of truth for experiment state, which contradicts ADR 0004 and every
   safety property the project claims. A single hallucinated "step three is
   complete" would corrupt a laboratory notebook with no way to detect it.

3. **Realtime model with a private tool implementation.** Give the realtime layer
   its own handlers, separate from the ones the UI uses. Rejected on a narrower
   ground: two implementations of "complete a step" will diverge, and the one
   used by the less-tested path will be the one that is wrong. Divergence here is
   not a style problem — it is how a confirmation requirement silently stops
   applying to voice.

4. **Realtime model as a bounded shell over one shared gateway.** Chosen.

## Decision

The realtime layer is an **input/output shell**. It carries audio, produces
candidate intents, and speaks results. It holds no authority.

Concretely:

- **One registry, one gateway.** `labjarvis.application.tools` defines every
  invocable action once — its schema, whether it mutates, which session states
  permit it, whether a human must confirm, and its audit event. Typed commands,
  UI buttons, and realtime function calls all resolve against that registry and
  execute through `LabJarvisService.invoke_tool`. There is no second path.

- **The realtime surface is a strict subset.** Only tools marked
  `realtime_exposed` are sent to the provider. `verify_reagent` is excluded: its
  input is a bounded image upload, not a function argument.

- **Confirmation policy is per tool, not per caller.** Anything that writes to
  the experimental record — a measurement, an observation, completing or skipping
  a step, ending the session — is `ALWAYS` confirmed, whoever proposed it.
  Reversible controls (pause, resume, timers) are `MODEL_PROPOSALS_ONLY`: a
  button press is itself the confirmation, a model proposal is not.

- **The model learns outcomes from the database.** A tool call returns whatever
  the backend replied — `committed`, `awaiting_confirmation`, or an error — and
  that reply is handed back verbatim as the function result. The model is never
  in a position to decide what happened.

- **Credentials are short-lived and scoped.** The browser receives an ephemeral
  grant minted server-side, already bound to one model, one voice, and the tool
  allowlist. The account key never leaves the backend.

- **Reconnect re-reads; it never replays.** A dropped connection resolves by
  reloading the authoritative snapshot. Tool calls carry an idempotency key
  derived from the provider call id, so a redelivered call reports the recorded
  outcome instead of applying it twice.

- **The feature is off by default.** `LABJARVIS_REALTIME_ENABLED=false` ships as
  the default, and the entire product — import, review, approval, session
  execution, verification, notebook — works without it. Realtime is an
  enhancement to an already-complete deterministic product, not a dependency
  of it.

- **No model identifier is assumed.** `LABJARVIS_REALTIME_MODEL_NAME` has no
  default and startup fails without it. Provider aliases move; an evaluation
  result is only reproducible if the exact identifier that produced it was
  recorded. The resolved identifier the provider echoes back is what goes into
  provenance, not the alias that was requested.

## Consequences

**Gained.** Hands-free operation that is genuinely hands-free, without widening
what a model can do. The safety argument did not get more complicated when voice
was added — it got simpler, because there is now exactly one place where actions
are authorised rather than one per entry point.

**Cost.** Every realtime action pays a round trip to the backend before the model
can speak the result. That is a deliberate latency cost: the alternative is the
model narrating an outcome it has not confirmed.

**Cost.** The registry is one more thing to keep in step. It is mitigated by
publishing it at `GET /api/v1/tools`, so the frontend reads the policy rather
than restating it, and by tests that assert the realtime surface is a strict
subset of it.

**Not claimed.** The instructions given to the realtime model tell it never to
assert state from memory and never to report an action as done. Those
instructions reduce noise; they are not a safety mechanism, because a model can
ignore them. The safety mechanism is that the gateway re-validates every call
regardless of what the model was told.

## Related

- ADR 0004 — deterministic state machine (upheld; this ADR shows how it survives
  a conversational interface)
- ADR 0015 — structured, revalidated command proposals
- ADR 0016 — optional hosted model execution (realtime inherits the same
  explicit opt-in)
- ADR 0012 — selective high-integrity transcription
