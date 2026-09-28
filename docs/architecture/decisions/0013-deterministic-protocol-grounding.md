# ADR 0013: Deterministic protocol grounding with abstention

**Status:** Accepted  
**Date:** 2026-08-18

## Context

An operator at a bench asks things the protocol may or may not answer: "how much
ethanol", "how long do I centrifuge", "what temperature do I store this at",
"what is the maximum autoclave pressure".

The last of those is the dangerous one. It is a reasonable laboratory question
that this particular protocol simply does not address. A language model asked it
with the protocol in context will very often produce a plausible number — drawn
from its training data, from a different protocol, or from nothing at all. In a
laboratory, a plausible number is worse than no number, because it will be acted
on.

## Options considered

1. **Send the protocol to a language model and ask.** Highest coverage of
   phrasings. Rejected as the primary path: it is precisely the arrangement that
   produces confident answers to unanswerable questions, and there is no reliable
   way to tell afterwards which kind of answer was returned.

2. **Vector retrieval plus a grounded generation step.** Better than option 1,
   and the conventional choice. Rejected for this system on two grounds. First,
   the corpus is one short document with consistent vocabulary — the retrieval
   quality gain over lexical matching is small. Second, an embedding index is
   opaque: a marker or a supervisor cannot re-derive why a passage was returned,
   whereas term overlap with a rarity weight can be worked out by hand from the
   document.

3. **Answer only from the structured approved steps.** Fully auditable, but too
   narrow. The structured steps are a *reduction* of the document; a storage
   temperature or a hazard note that the reviewer did not turn into a step is
   still part of the approved protocol and should still be quotable.

4. **Deterministic lexical retrieval over the approved version, with an explicit
   abstention floor.** Chosen.

## Decision

Protocol questions are answered by `labjarvis.domain.grounding`, in ordinary
code, with no model involved.

- **The corpus is one approved version.** `retrieve` takes a `ProtocolVersion`
  and searches only it. Retrieving from an unrelated protocol or an unapproved
  draft is not merely forbidden — it is not expressible in the interface.

- **Two passage kinds.** Structured approved steps (carrying exact page, section,
  and step number) and paragraph-sized excerpts of the original source text. On
  an equal score the structured step wins, because it is the reviewed form and
  carries better provenance.

- **Ranking is term overlap weighted by rarity**, normalised by the query so
  scores are comparable between questions. Reproducible and hand-checkable.

- **Below the relevance floor, abstain.** The answer becomes "That is not
  specified in the approved protocol. Check the original document or ask your
  supervisor before proceeding." No passage is returned and nothing is
  paraphrased into the gap.

- **Every answer is quoted with its location.** The response contains the exact
  passage and a citation — page, section, step — so the operator can check it
  against the document rather than trusting the assistant.

- **It is a read-only tool.** `answer_protocol_question` sits in the same
  registry as everything else, is exposed to the realtime layer, and can never
  change state.

## Consequences

**Gained.** A class of dangerous answer is structurally unavailable: the system
cannot state a quantity the document does not contain, because there is no
generation step that could invent one.

**Gained.** Answers are reproducible. Re-running a recorded question against the
same approved version returns the same passage and the same score, which matters
for evaluation and for defending a result.

**Cost.** Coverage of paraphrased questions is lower than an embedding-based or
generative approach would give. A question worded very differently from the
protocol's own vocabulary will abstain where a model might have answered
correctly. That is the intended direction of the trade: a false abstention costs
the operator a look at the document, a false answer costs them the experiment.

**Cost.** The relevance floor is a tuned constant. It is exposed as a parameter
and covered by tests at both extremes, but it has not been calibrated against a
labelled question set from real users.

**Not claimed.** This is not question answering in the general sense. It finds
and quotes the most relevant passage of one document, or says it cannot. The
language model is used for command interpretation when the grammar does not
match; it does not invent answers or restructure uploaded protocols.

## Related

- ADR 0015 — structured, revalidated command proposals
- ADR 0011 — deterministic core, realtime shell
