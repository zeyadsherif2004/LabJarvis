from datetime import UTC, datetime

import pytest

from labjarvis.domain.models import EntrySource, NotebookEntry, ProvenanceError

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def test_notebook_entry_requires_correlation_provenance() -> None:
    with pytest.raises(ProvenanceError, match="correlation"):
        NotebookEntry(
            id="entry-1",
            session_id="session-1",
            entry_type="measurement_proposed",
            source=EntrySource.USER_CONFIRMED_SPEECH,
            payload={"value": "37", "unit": "degC"},
            created_at=NOW,
            correlation_id="",
        )


def test_correction_supersedes_instead_of_erasing_history() -> None:
    original = NotebookEntry(
        id="entry-1",
        session_id="session-1",
        entry_type="measurement",
        source=EntrySource.USER_CONFIRMED_SPEECH,
        payload={"value": "37", "unit": "degC"},
        created_at=NOW,
        correlation_id="request-1",
    )

    correction = original.corrected(
        entry_id="entry-2",
        payload={"value": "36.5", "unit": "degC"},
        source=EntrySource.TYPED_INPUT,
        correlation_id="request-2",
        at=NOW,
    )

    assert original.payload["value"] == "37"
    assert correction.payload["value"] == "36.5"
    assert correction.supersedes_entry_id == original.id
    assert correction.entry_type == "correction"
