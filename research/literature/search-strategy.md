# Literature search strategy

Conducted 2026-07-17 using web search over publisher pages (arXiv abstract
pages, ACM DL, IEEE/dblp, SAGE, Nature, Oxford Academic, PMLR, NeurIPS
proceedings). Every retained source's authors, title, venue, year, and
DOI/identifier were confirmed on the publisher or proceedings page (or dblp),
never from a snippet alone; the verification URL is recorded in the source
ledger.

## Queries (representative)

- whisper robust speech recognition large-scale weak supervision ICML 2023
- voice user interface laboratory instruments SLAS Technology
- selective classification deep neural networks NeurIPS; Chow reject option 1970
- calibration modern neural networks ICML 2017
- trust in automation appropriate reliance; automation use misuse disuse abuse
- automation bias systematic review JAMIA
- guidelines for human-AI interaction CHI 2019
- cognitive forcing functions overreliance AI CSCW 2021
- autonomous chemical research large language models Nature; ChemCrow
- self-driving labs chemical materials sciences review
- efficient guided generation large language models (structured output)
- hallucination natural language generation survey
- CRAFT character region awareness text detection; CRNN end-to-end sequence recognition

## Inclusion criteria

Peer-reviewed venue or, for very recent model/systems work without a
peer-reviewed alternative, an arXiv report clearly labelled as such; direct
relevance to at least one project research question (models used, related systems,
selective prediction/abstention, human oversight, or safeguards); primary
sources preferred over surveys except where the survey itself is the point
(hallucination, automation bias).

## Exclusion criteria

Blog posts, press releases, unverifiable metadata, sources found only as
snippets, duplicative surveys, and work unrelated to the project's claims.
No source was cited without opening its official page.

## 2026-07-20 update

I extended the search to recent work on abstention in language models and
human-in-the-loop assistive systems. The retained Wen et al. (2025) and Bellos
et al. (2025) sources, their venues, and verification URLs are recorded in
[`source-ledger.csv`](source-ledger.csv). These papers help place LabJarvis's
grounding and human confirmation choices in context; they do not establish
that no similar laboratory system exists.
