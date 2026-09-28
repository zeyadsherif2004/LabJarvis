from hashlib import sha256

from labjarvis.infrastructure.media_storage import (
    DiscardRawMediaStore,
    LocalRawMediaStore,
)


def test_discard_store_does_not_create_files(tmp_path) -> None:
    store = DiscardRawMediaStore()

    result = store.store(
        session_id="session-1",
        modality="audio",
        content_type="audio/webm",
        data=b"private audio",
    )

    assert result is None
    assert list(tmp_path.iterdir()) == []


def test_local_store_is_content_addressed_and_ignores_untrusted_names(tmp_path) -> None:
    store = LocalRawMediaStore(tmp_path)
    data = b"private image"

    first = store.store(
        session_id="../../operator supplied session",
        modality="image",
        content_type="image/jpeg",
        data=data,
    )
    second = store.store(
        session_id="../../operator supplied session",
        modality="image",
        content_type="image/jpeg",
        data=data,
    )

    assert first is not None
    assert first == second
    assert first.sha256 == sha256(data).hexdigest()
    assert "operator" not in first.reference
    retained = list((tmp_path / "raw-media").rglob("*.bin"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == data
