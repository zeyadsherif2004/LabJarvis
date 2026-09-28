# ADR 0012: Selective high-integrity transcription

**Status:** Accepted  
**Date:** 2026-08-18

## Context

The realtime layer (ADR 0011) optimises for latency, which is the right trade for
conversation and the wrong one for a number. "Five millilitres" and "fifty
millilitres" differ by one phoneme and by a factor of ten, and the second is
written into a laboratory notebook as fact.

Operator confirmation already guards this: nothing is recorded until a human
approves it, and the confirmation dialog shows the normalised value and unit. But
confirmation is weakest exactly where it matters — an operator with their hands
in a fume hood, hearing "recording fifty millilitres" while believing they said
five, is being asked to catch an error by listening carefully at the least
attentive moment of the task.

A second, more accurate reading of the same audio gives an independent check.

## Options considered

1. **Trust the realtime transcript; rely on confirmation alone.** Simplest and
   cheapest. Rejected for the reason above: it makes a single point of failure
   out of the operator's attention at the worst possible moment.

2. **Always run both transcription paths.** Maximum protection, but it doubles
   the latency and cost of *every* interaction, including "what is the next
   step" — the most common utterance, whose answer comes from structured state
   and cannot be affected by a mis-heard word. Rejected as protection bought
   where there is nothing to protect.

3. **Let a model reconcile the two transcripts.** Rejected outright. Asking a
   model which of two numbers was really said reintroduces exactly the failure
   the second reading exists to catch, and makes it undetectable.

4. **Pick whichever transcript has higher reported confidence.** Rejected:
   confidence scores are not comparable across models and are not calibrated for
   this decision. A confident wrong number is the worst outcome available.

5. **Escalate selectively, and stop on disagreement.** Chosen.

## Decision

Take a second reading only for utterances that could change the experimental
record, and when the two readings disagree materially, **record nothing and ask**.

**When escalation happens.** After the primary transcript is produced, a
deterministic check (`_needs_high_integrity_reading`) looks for a numeral or a
record-writing verb — record, measure, complete, skip, end, timer, pH,
temperature, volume, mass, concentration. Anything else proceeds on one reading.

**How disagreement is judged.** `labjarvis.domain.transcripts` compares the two
readings in ordinary code, with no model involved:

- Differences that *cannot* change a record — casing, spacing, punctuation,
  filler words, "ten" versus "10", "millilitres" versus "mL" — are equivalent.
  Asking the operator to repeat themselves over those would make hands-free
  operation unusable, and would train them to confirm reflexively.
- Any difference in a **number**, a **unit**, a **negation**, or the substantive
  words is a conflict.

**What happens on conflict.** No command is interpreted and no pending action is
created. The operator hears what was heard both ways and is asked to repeat or
type it. Crucially, nothing is left behind for someone to confirm later without
realising there had been a disagreement.

**What is stored.** Every cross-checked utterance produces a durable record: both
transcripts, the model that produced each, the deterministic verdict, and the
canonical wording that was acted on — empty when there was a conflict, because
nothing was acted on.

**Off by default.** `LABJARVIS_HIGH_INTEGRITY_TRANSCRIPTION_ENABLED=false`, and
enabling it requires naming the model explicitly. As with ADR 0011, no default
identifier is assumed.

## Consequences

**Gained.** The 5 mL / 50 mL case now fails safe rather than relying on the
operator noticing. It is covered by an end-to-end test.

**Cost.** Consequential utterances pay a second transcription in latency and, in
hosted mode, in money. That cost is bounded by the selectivity rule and falls on
exactly the utterances where it is justified.

**Cost.** Some conflicts will be spurious — two readings differing over a word
that did not matter but that the comparison could not prove harmless. The
operator repeats themselves. The comparison is deliberately tuned to prefer that
outcome over the alternative.

**Not claimed.** This reduces the chance of a mis-heard number reaching the
notebook. It does not eliminate it: two models can be wrong in the same way, and
the escalation rule can miss an utterance that had no numeral and no keyword.
Operator confirmation remains the primary control; this is defence in depth.

## Related

- ADR 0011 — deterministic core, realtime shell
- ADR 0015 — structured, revalidated command proposals
