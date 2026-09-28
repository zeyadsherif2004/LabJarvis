"""Protocol answers are grounded, cited, and abstain when the document is silent.

The failure this guards against is a confident answer to a question the protocol
never addresses. Abstaining is the correct behaviour there, even though it is the
less impressive one.
"""

from datetime import UTC, datetime
from decimal import Decimal

from labjarvis.domain.grounding import (
    ABSTENTION,
    compose_answer,
    retrieve,
    tokenise,
)
from labjarvis.domain.models import (
    ProtocolRequirement,
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    SourceReference,
)

SOURCE = "\n".join(
    [
        "Ethanol precipitation protocol",
        "Store all ethanol stocks below four degrees Celsius before use.",
        "1. Add 10 mL ethanol to the demonstration vessel.",
        "2. Centrifuge the vessel for 15 minutes.",
        "3. Decant the supernatant carefully.",
    ]
)


def _version(steps: tuple[ProtocolStepDraft, ...] | None = None) -> ProtocolVersion:
    default = (
        ProtocolStepDraft(
            id="step-1",
            number=1,
            title="Add ethanol",
            instruction="Add 10 mL ethanol to the demonstration vessel.",
            requirements=(
                ProtocolRequirement(
                    requirement_id="step-1-req-1",
                    name="ethanol",
                    quantity=Decimal("10"),
                    unit="mL",
                ),
            ),
            duration_seconds=None,
            safety_notes=("Warning: ethanol is flammable.",),
            source=SourceReference(
                page=1,
                section="Preparation",
                excerpt="1. Add 10 mL ethanol to the demonstration vessel.",
            ),
        ),
        ProtocolStepDraft(
            id="step-2",
            number=2,
            title="Centrifuge",
            instruction="Centrifuge the vessel for 15 minutes.",
            requirements=(),
            duration_seconds=900,
            safety_notes=(),
            source=SourceReference(
                page=2,
                section="Separation",
                excerpt="2. Centrifuge the vessel for 15 minutes.",
            ),
        ),
    )
    return ProtocolVersion(
        id="version-1",
        protocol_id="protocol-1",
        version_number=1,
        revision=2,
        status=ProtocolVersionStatus.APPROVED,
        steps=steps if steps is not None else default,
        source_sha256="0" * 64,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        source_text=SOURCE,
        approved_at=datetime(2026, 3, 1, tzinfo=UTC),
    )


# ------------------------------------------------------------------ grounding


def test_a_question_answerable_from_a_step_returns_that_step() -> None:
    result = retrieve(_version(), "how long should I centrifuge the vessel")

    assert result.supported
    assert result.passages[0].step_id == "step-2"


def test_an_answer_carries_a_citation_that_can_be_checked() -> None:
    result = retrieve(_version(), "how long should I centrifuge the vessel")

    citation = result.passages[0].citation
    assert "page 2" in citation
    assert "Separation" in citation
    assert "step 2" in citation


def test_the_composed_answer_quotes_the_protocol_rather_than_paraphrasing() -> None:
    answer = compose_answer(retrieve(_version(), "how long should I centrifuge"))

    assert "Centrifuge the vessel for 15 minutes." in answer
    assert "page 2" in answer


def test_material_only_in_the_source_text_is_still_answerable() -> None:
    # The reviewer captured three steps; the storage note was not one of them,
    # but it is still part of the approved document.
    result = retrieve(_version(), "what temperature should ethanol stocks be stored below")

    assert result.supported
    assert "four degrees Celsius" in result.passages[0].text


# ------------------------------------------------------------------ abstention


def test_a_question_the_protocol_does_not_address_is_refused() -> None:
    result = retrieve(_version(), "what is the maximum autoclave pressure")

    assert not result.supported
    assert compose_answer(result) == ABSTENTION


def test_the_abstention_tells_the_operator_what_to_do_instead() -> None:
    assert "not specified in the approved protocol" in ABSTENTION
    assert "supervisor" in ABSTENTION


def test_a_blank_question_abstains_rather_than_returning_the_first_step() -> None:
    assert not retrieve(_version(), "   ").supported
    assert not retrieve(_version(), "the of and").supported


def test_a_protocol_with_no_steps_and_no_source_abstains() -> None:
    empty = ProtocolVersion(
        id="version-2",
        protocol_id="protocol-2",
        version_number=1,
        revision=1,
        status=ProtocolVersionStatus.APPROVED,
        steps=(),
        source_sha256="0" * 64,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        source_text="",
    )

    assert not retrieve(empty, "how long should I centrifuge").supported


# -------------------------------------------------------------------- scoping


def test_retrieval_only_ever_searches_the_version_it_was_given() -> None:
    result = retrieve(_version(), "centrifuge")

    # Every returned chunk id is namespaced by the version, so an answer can
    # always be traced back to the exact approved version it came from.
    assert result.protocol_version_id == "version-1"
    assert all(passage.chunk_id.startswith("version-1:") for passage in result.passages)


def test_results_are_ordered_by_relevance_and_capped() -> None:
    result = retrieve(_version(), "ethanol vessel centrifuge decant", limit=2)

    assert len(result.passages) <= 2
    scores = [passage.score for passage in result.passages]
    assert scores == sorted(scores, reverse=True)


def test_raising_the_floor_makes_retrieval_abstain_more_readily() -> None:
    # Only one of the two query terms appears in the protocol, so this scores
    # partially: low enough to accept by default, not enough for a strict floor.
    question = "centrifuge the autoclave"

    assert retrieve(_version(), question, floor=0.0).supported
    assert not retrieve(_version(), question, floor=0.95).supported


def test_a_structured_step_outranks_the_same_sentence_quoted_from_the_source() -> None:
    # Both can score identically. The reviewed step is preferred because it
    # carries exact page, section, and step provenance.
    result = retrieve(_version(), "how long should I centrifuge the vessel")

    assert result.passages[0].step_id == "step-2"
    assert result.passages[0].page == 2


def test_ranking_is_reproducible_for_the_same_input() -> None:
    # A reviewer must be able to re-run a recorded answer and get it back.
    first = retrieve(_version(), "ethanol")
    second = retrieve(_version(), "ethanol")

    assert [item.chunk_id for item in first.passages] == [
        item.chunk_id for item in second.passages
    ]
    assert [item.score for item in first.passages] == [item.score for item in second.passages]


# --------------------------------------------------------------- tokenisation


def test_sentence_punctuation_does_not_hide_a_term_from_retrieval() -> None:
    # "flammable." must match "flammable", or the safety note attached to a step
    # becomes invisible to any question about it.
    assert "flammable" in tokenise("Warning: ethanol is flammable.")
    assert "sources" in tokenise("Keep away from ignition sources.")


def test_interior_dots_and_hyphens_are_preserved_because_they_carry_meaning() -> None:
    assert "0.5" in tokenise("Add 0.5 mL")
    assert "ph-7" in tokenise("Adjust to pH-7 buffer")


def test_a_safety_note_attached_to_a_step_is_retrievable() -> None:
    result = retrieve(_version(), "is ethanol flammable")

    assert result.supported
    assert "flammable" in result.passages[0].text.casefold()


def test_a_generic_verb_does_not_outrank_the_term_the_question_is_about() -> None:
    """The defect behind gq-006, stated as a property rather than a fixture.

    "Which vessel do I use" is about a vessel. In a corpus of a dozen short
    passages, though, a word that appears once gets a large rarity weight, so
    "use" was enough for an unrelated storage sentence to outrank the step that
    names the vessel. Retrieval must key on what the question is *about*.
    """

    version = _version()

    result = retrieve(version, "which vessel do I use")

    assert result.supported
    best = result.passages[0]
    assert best.step_id == "step-1"
    assert "vessel" in best.text.casefold()


def test_query_framing_words_alone_cannot_answer_anything() -> None:
    """A question made only of framing words has no content to retrieve on."""

    version = _version()

    assert not retrieve(version, "what should I do").supported
    assert not retrieve(version, "how do I use it").supported
