"""Voice-only confirmation through the wake-phrase listener (opt-in).

Invariant under test: the listener can *answer* a proposal only with a
value-bearing reply that starts with the wake phrase and that both
transcriptions agree on. A bare acknowledgement, a wrong or conflicting value,
unaddressed speech, an echo of the prompt, a stale proposal, or an attempt to
confirm a mismatching label never commits anything. Every assertion is about
database state, not wording.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from labjarvis.api.app import create_app
from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import (
    EntrySource,
    PendingActionStatus,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore
from labjarvis.settings import Settings

SAMPLE = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)
AUDIO = "audio/wav"
keys = count(1)


def build_service(*, voice_only: bool = True) -> LabJarvisService:
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    return LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
        speech=FakeSpeechToText(),
        verification_speech=FakeSpeechToText(verification=True),
        text=FakeTextUnderstanding(),
        labels=FakeLabelRecognition(),
        inference_runner=DirectInferenceRunner(),
        clock=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        new_id=lambda: f"id-{next(ids)}",
        command_confidence_threshold=0.75,
        reagent_match_threshold=92,
        reagent_uncertain_threshold=72,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        wake_phrase="Jarvis",
        voice_only_confirmation=voice_only,
    )


@pytest.fixture
def service() -> LabJarvisService:
    return build_service()


def active(service: LabJarvisService):  # type: ignore[no-untyped-def]
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    approved = service.approve_protocol(imported.version.id, expected_revision=0)
    session = service.create_session(approved.id)
    return service.start_session(session.id, expected_version=0)


def propose(service: LabJarvisService, tool: str, parameters: dict[str, str]):  # type: ignore[no-untyped-def]
    session = service.get_session(active_session_id(service))
    result = service.invoke_tool(
        session.id,
        tool,
        parameters,
        source=EntrySource.MODEL_PROPOSAL,
        model_proposed=True,
        correlation_id="propose",
        expected_version=session.version,
        idempotency_key=f"propose-{next(keys)}",
    )
    assert result.pending_action is not None, result
    return result.pending_action


def active_session_id(service: LabJarvisService) -> str:
    # Cached on the service itself: an id()-keyed cache is unsafe because
    # CPython reuses object ids once a previous test's service is collected.
    cached = getattr(service, "_test_session_id", None)
    if cached is None:
        cached = active(service).id
        service._test_session_id = cached  # type: ignore[attr-defined]
    return str(cached)


def say(service: LabJarvisService, action_id: str, words: str):  # type: ignore[no-untyped-def]
    return service.confirm_pending_action_by_voice(
        action_id,
        f"FAKE:{words}".encode(),
        content_type=AUDIO,
        idempotency_key=f"voice-{next(keys)}",
        correlation_id="voice",
        via_wake_phrase=True,
    )


def status_of(service: LabJarvisService, action_id: str) -> PendingActionStatus:
    with service._uow_factory() as uow:  # authoritative state, not a response
        action = uow.pending_actions.get(action_id)
        assert action is not None
        return action.status


def entries(service: LabJarvisService, entry_type: str) -> list[object]:
    review = service.get_session_review(active_session_id(service))
    return [e for e in review.entries if e.entry_type == entry_type]


def measurement(service: LabJarvisService):  # type: ignore[no-untyped-def]
    return propose(service, "record_measurement", {"kind": "volume", "value": "10", "unit": "mL"})


# --- configuration ------------------------------------------------------------


def test_voice_only_confirmation_needs_listening_and_the_cross_check(tmp_path: Path) -> None:
    base = {"data_dir": tmp_path, "database_url": f"sqlite+pysqlite:///{tmp_path / 'a.db'}"}
    with pytest.raises(ValidationError, match="HANDS_FREE_LISTENING"):
        Settings(**base, voice_only_confirmation_enabled=True)
    with pytest.raises(ValidationError, match="high-integrity"):
        Settings(**base, voice_only_confirmation_enabled=True, hands_free_listening_enabled=True)


def test_when_switched_off_the_listener_cannot_answer_a_proposal() -> None:
    service = build_service(voice_only=False)
    action = measurement(service)
    with pytest.raises(ApplicationError) as caught:
        say(service, action.id, "Jarvis, confirm 10 millilitres")
    assert caught.value.code == "VOICE_ONLY_CONFIRMATION_DISABLED"
    assert status_of(service, action.id) is PendingActionStatus.PENDING
    assert entries(service, "measurement") == []


# --- valid value-bearing confirmations ----------------------------------------------


def test_a_value_bearing_reply_commits_a_measurement(service: LabJarvisService) -> None:
    action = measurement(service)
    result = say(service, action.id, "Jarvis, confirm 10 millilitres")
    assert result.committed is True
    assert status_of(service, action.id) is PendingActionStatus.ACCEPTED
    assert len(entries(service, "measurement")) == 1


def test_a_timer_is_confirmed_by_its_duration(service: LabJarvisService) -> None:
    action = propose(service, "set_timer", {"duration_seconds": "600"})
    assert say(service, action.id, "Jarvis, confirm 5 minutes").committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING
    assert say(service, action.id, "Jarvis, confirm 10 minutes").committed is True


def test_a_step_is_confirmed_by_its_number(service: LabJarvisService) -> None:
    session = service.get_session(active_session_id(service))
    version = service.get_protocol_version(session.protocol_version_id)
    number = next(s.number for s in version.steps if s.id == session.active_step_id)
    action = propose(service, "complete_step", {})
    assert say(service, action.id, f"Jarvis, confirm step {number + 1}").committed is False
    assert say(service, action.id, "Jarvis, confirm").committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING
    assert say(service, action.id, f"Jarvis, confirm step {number}").committed is True


def test_a_session_control_is_confirmed_by_naming_it(service: LabJarvisService) -> None:
    action = propose(service, "pause_session", {})
    assert say(service, action.id, "Jarvis, confirm resume").committed is False
    assert say(service, action.id, "Jarvis, yes").committed is False
    assert say(service, action.id, "Jarvis, confirm pause").committed is True
    assert service.get_session(active_session_id(service)).state.value == "PAUSED"


# --- replies that must never commit -------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "Jarvis, yes",
        "Jarvis, confirm",
        "Jarvis, OK",
        "Jarvis, do it",
        "Jarvis, confirmed",
        "Jarvis, confirm 50 millilitres",
        "Jarvis, confirm 10 microlitres",
        "Jarvis, confirm 10",
        "Jarvis, confirm 10 or 50 millilitres",
        "Jarvis, confirm 10 millilitres, no wait, 50",
        "Jarvis, maybe confirm 10 millilitres",
        "Jarvis, say confirm 10 millilitres",
    ],
)
def test_ambiguous_or_wrong_replies_leave_the_proposal_waiting(
    service: LabJarvisService, words: str
) -> None:
    action = measurement(service)
    result = say(service, action.id, words)
    assert result.committed is False
    assert result.decision == "ambiguous"
    assert status_of(service, action.id) is PendingActionStatus.PENDING
    assert entries(service, "measurement") == []
    # Spoken prompts never contain the wake phrase, so an echo of this reply
    # can never pass the wake-phrase check.
    assert "jarvis" not in result.response_text.casefold()


@pytest.mark.parametrize(
    "words",
    [
        "confirm 10 millilitres",  # no wake phrase
        "Say the wake word, then: confirm 10 millilitres",  # echo of the prompt
        "tell Jarvis to confirm 10 millilitres",  # wake phrase not leading
        "I think the reading was 10 millilitres",  # background conversation
    ],
)
def test_unaddressed_speech_is_ignored_without_a_trace(
    service: LabJarvisService, words: str
) -> None:
    action = measurement(service)
    before = service.get_session_review(active_session_id(service))
    result = say(service, action.id, words)
    after = service.get_session_review(active_session_id(service))
    assert result.decision == "not_addressed"
    assert result.committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING
    # Nothing was written: no transcript, no utterance, no inference, no entry.
    assert len(after.entries) == len(before.entries)
    assert len(after.inferences) == len(before.inferences)


def test_disagreeing_transcriptions_never_commit(service: LabJarvisService) -> None:
    action = measurement(service)
    result = say(
        service, action.id, "Jarvis, confirm 10 millilitres||Jarvis, confirm 50 millilitres"
    )
    assert result.committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING


def test_a_voice_rejection_records_nothing(service: LabJarvisService) -> None:
    action = measurement(service)
    result = say(service, action.id, "Jarvis, reject")
    assert result.decision == "reject"
    assert result.committed is False
    assert status_of(service, action.id) is PendingActionStatus.REJECTED
    assert entries(service, "measurement") == []


def test_a_replaced_proposal_cannot_be_confirmed(service: LabJarvisService) -> None:
    first = measurement(service)
    say(service, first.id, "Jarvis, reject")
    second = propose(service, "record_measurement", {"kind": "volume", "value": "12", "unit": "mL"})
    with pytest.raises(ApplicationError):
        say(service, first.id, "Jarvis, confirm 10 millilitres")
    assert status_of(service, second.id) is PendingActionStatus.PENDING
    assert entries(service, "measurement") == []


def test_actions_without_a_voice_phrase_need_push_to_talk_or_the_screen(
    service: LabJarvisService,
) -> None:
    action = propose(service, "record_observation", {"observation": "solution turned cloudy"})
    result = say(service, action.id, "Jarvis, confirm solution turned cloudy")
    assert result.committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING


def test_push_to_talk_behaviour_is_unchanged_for_a_timer(service: LabJarvisService) -> None:
    action = propose(service, "set_timer", {"duration_seconds": "600"})
    result = service.confirm_pending_action_by_voice(
        action.id, b"FAKE:yes", content_type=AUDIO, idempotency_key="ptt-1", correlation_id="c"
    )
    assert result.committed is True


def test_push_to_talk_now_refuses_conflicting_values(service: LabJarvisService) -> None:
    action = measurement(service)
    result = service.confirm_pending_action_by_voice(
        action.id,
        b"FAKE:confirm 10 or 50 mL",
        content_type=AUDIO,
        idempotency_key="ptt-2",
        correlation_id="c",
    )
    assert result.committed is False
    assert status_of(service, action.id) is PendingActionStatus.PENDING


# --- reagent readings -------------------------------------------------------------------


def reagent_session(service: LabJarvisService):  # type: ignore[no-untyped-def]
    session = service.get_session(active_session_id(service))
    version = service.get_protocol_version(session.protocol_version_id)
    while not next(s for s in version.steps if s.id == session.active_step_id).requirements:
        service.invoke_tool(
            session.id, "complete_step", {}, source=EntrySource.TYPED_INPUT,
            model_proposed=False, correlation_id="c", expected_version=session.version,
        )  # fmt: skip
        session = service.get_session(session.id)
    return session


def reading(service: LabJarvisService, label: str):  # type: ignore[no-untyped-def]
    session = reagent_session(service)
    return session, service.verify_reagent(
        session.id,
        f"FAKE:{label}".encode(),
        content_type="image/jpeg",
        correlation_id="c",
        expected_version=session.version,
        idempotency_key=f"verify-{next(keys)}",
    )


def decide(service: LabJarvisService, session_version: int, verification_id: str, words: str):  # type: ignore[no-untyped-def]
    return service.confirm_reagent_verification_by_voice(
        verification_id,
        f"FAKE:{words}".encode(),
        content_type=AUDIO,
        correlation_id="c",
        expected_version=session_version,
        idempotency_key=f"decide-{next(keys)}",
    )


def test_a_match_is_confirmed_only_by_naming_the_reagent(service: LabJarvisService) -> None:
    session, verification = reading(service, "ethanol")
    assert verification.outcome is VerificationOutcome.MATCH
    assert decide(service, session.version, verification.id, "Jarvis, yes").committed is False
    assert (
        decide(service, session.version, verification.id, "Jarvis, confirm methanol").committed
        is False
    )
    result = decide(service, session.version, verification.id, "Jarvis, confirm ethanol")
    assert result.committed is True
    assert result.verification.decision is VerificationDecision.CONFIRMED
    progress = service.get_session_snapshot(session.id).requirement_progress
    assert {item.status for item in progress} == {"confirmed"}


@pytest.mark.parametrize("label", ["methanol", "ethano1"])
@pytest.mark.parametrize(
    "words", ["Jarvis, confirm ethanol", "Jarvis, confirm methanol", "Jarvis, yes"]
)
def test_a_mismatch_or_uncertain_reading_is_never_confirmed_by_voice(
    service: LabJarvisService, label: str, words: str
) -> None:
    session, verification = reading(service, label)
    assert verification.outcome is not VerificationOutcome.MATCH
    result = decide(service, session.version, verification.id, words)
    assert result.committed is False
    assert result.verification.decision is VerificationDecision.PENDING
    assert "jarvis" not in result.response_text.casefold()


def test_any_reading_can_be_rejected_by_voice(service: LabJarvisService) -> None:
    session, verification = reading(service, "methanol")
    result = decide(service, session.version, verification.id, "Jarvis, reject")
    assert result.committed is True
    assert result.verification.decision is VerificationDecision.REJECTED


def test_unaddressed_speech_does_not_touch_a_reading(service: LabJarvisService) -> None:
    session, verification = reading(service, "ethanol")
    result = decide(service, session.version, verification.id, "confirm ethanol")
    assert result.decision == "not_addressed"
    assert result.verification.decision is VerificationDecision.PENDING


def test_a_confirmed_mismatch_reading_never_counts_as_a_completed_review(
    service: LabJarvisService,
) -> None:
    """Confirming what the camera read is not verifying the reagent."""

    session, verification = reading(service, "methanol")
    service.confirm_reagent_verification(
        verification.id,
        accepted=True,
        corrected_observed_label=None,
        correlation_id="c",
        expected_version=session.version,
        idempotency_key="screen-confirm",
    )
    progress = service.get_session_snapshot(session.id).requirement_progress
    assert {item.status for item in progress} == {"not_matching"}


# --- HTTP surface ---------------------------------------------------------------------------


def test_the_api_exposes_the_flag_and_rejects_voice_only_when_off(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'v.db').as_posix()}",
    )
    with TestClient(create_app(settings, create_schema=True)) as client:
        ready = client.get("/health/ready").json()
        assert ready["interaction"]["voice_only_confirmation"] is False
        response = client.post(
            "/api/v1/pending-actions/missing/spoken-confirmation?via_wake_phrase=true",
            headers={"Idempotency-Key": "k"},
            files={"file": ("c.wav", b"FAKE:Jarvis, confirm 10 mL", AUDIO)},
        )
        assert response.status_code in {404, 409}
