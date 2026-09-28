# Related-work synthesis notes

Organised by the dimensions relevant to the literature review: modality,
grounding source, autonomy, state enforcement, uncertainty handling, human
intervention, evaluation.

## 1. Laboratory assistants and automation

- Austerjost et al. (2018) is the closest prior system: a voice user interface
  controlling laboratory instruments through a commercial assistant platform.
  It demonstrates hands-busy feasibility but has no document grounding, no
  protocol state model, no perceptual verification, and no uncertainty-driven
  intervention. LabJarvis's gap sits precisely there.
- Self-driving labs (Abolhasani & Kumacheva 2023) and LLM agents that plan and
  execute chemistry (Boiko et al. 2023; M. Bran et al. 2024) occupy the
  opposite end of the autonomy spectrum: the system acts. Their authors note
  safety concerns; LabJarvis takes the complementary position that in
  human-executed protocol work the AI proposes and deterministic code plus the
  operator dispose.

## 2. Why unconstrained generation is risky

- Hallucination is systemic in neural generation (Ji et al. 2023); modern
  network confidences are miscalibrated (Guo et al. 2017). Together these
  justify (a) schema-constrained decoding (Willard & Louf 2023) for syntactic
  validity and (b) deterministic semantic validation because syntax guarantees
  do not exclude wrong content — which our E2 multi-command guard failure
  illustrates concretely.

## 3. Selective prediction and abstention

- Chow (1970) formalises the error/reject trade-off; Geifman & El-Yaniv (2017)
  operationalise risk-coverage for deep networks. LabJarvis's reagent policy is
  a hand-built selective classifier: exact-equality acceptance, an uncertainty
  band that abstains to the operator, and mismatch otherwise. E3's threshold
  sweep is a risk-coverage analysis of that design point.

## 4. Human oversight and its failure modes

- Parasuraman & Riley (1997) and Lee & See (2004): design for appropriate
  reliance. Goddard et al. (2012): automation bias is frequent; verification
  prompts mitigate only partially. Bucinca et al. (2021): cognitive forcing
  functions reduce overreliance at a satisfaction cost. These predict our H3
  result: condition C's benefit over B disappears under an always-accept
  operator, and abstentions rubber-stamped into acceptances can convert
  safeguards into false accepts.
- Amershi et al. (2019): show uncertainty, support correction, scope
  capabilities — reflected in the operator UI (pending confirmations, explicit
  correction paths, provenance).

## Gap addressed by LabJarvis

Prior laboratory voice interfaces lack protocol grounding and verification;
autonomous lab agents remove the human from execution; selective-prediction
theory is rarely embedded in an end-to-end multimodal procedural assistant and
evaluated against its human-oversight failure modes. LabJarvis + this study
fill that intersection: a deterministic, uncertainty-aware intervention
cascade around pre-trained speech/vision models, evaluated by ablation with
operator-attentiveness sensitivity.
