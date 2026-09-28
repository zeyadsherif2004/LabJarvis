"""Session, command, confirmation, verification, and notebook routes."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from labjarvis.api.dependencies import (
    get_service,
    require_idempotency_key,
    require_if_match,
)
from labjarvis.api.routes.protocols import read_bounded
from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import EntrySource, SessionState
from labjarvis.domain.voice_controls import ListenMode
from labjarvis.schemas.api import (
    CommandResponse,
    ConfirmationResponse,
    ConfirmPendingActionRequest,
    ConfirmReagentVerificationRequest,
    CorrectNotebookEntryRequest,
    CreateSessionRequest,
    InvokeToolRequest,
    LabelProbeResponse,
    NotebookEntryResponse,
    NotebookResponse,
    ReagentVerificationResponse,
    SessionListResponse,
    SessionResponse,
    SessionSnapshotResponse,
    SpokenConfirmationResponse,
    SpokenReagentDecisionResponse,
    TextCommandRequest,
    TimerListResponse,
    ToolInvocationResponse,
    ToolRegistryResponse,
)

router = APIRouter(prefix="/api/v1", tags=["sessions"])


def correlation_id(request: Request) -> str:
    value: str = request.state.correlation_id
    return value


@router.get("/tools", response_model=ToolRegistryResponse)
def list_tools() -> ToolRegistryResponse:
    """Publish the shared allowlist so clients cannot drift from the backend.

    The web UI reads this to decide which controls to render and whether a
    control commits directly or opens a confirmation, rather than duplicating
    those rules in the frontend where they could diverge.
    """

    return ToolRegistryResponse.current()


@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(
    service: Annotated[LabJarvisService, Depends(get_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    resumable_only: Annotated[bool, Query()] = False,
    q: Annotated[str | None, Query(max_length=250)] = None,
    protocol_version_id: Annotated[str | None, Query(max_length=64)] = None,
    label: Annotated[str | None, Query(max_length=120)] = None,
    state: Annotated[SessionState | None, Query()] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    protocol_archived: Annotated[bool | None, Query()] = None,
) -> SessionListResponse:
    """List recent sessions so work can be found and resumed without a bookmark."""

    states = (state,) if state is not None else (
        (SessionState.READY, SessionState.ACTIVE, SessionState.PAUSED)
        if resumable_only
        else None
    )
    return SessionListResponse.from_domain(
        service.list_sessions(
            limit=limit,
            states=states,
            query=q,
            protocol_version_id=protocol_version_id,
            label=label,
            created_from=created_from,
            created_to=created_to,
            protocol_archived=protocol_archived,
        )
    )


@router.get("/sessions/{session_id}/snapshot", response_model=SessionSnapshotResponse)
def get_session_snapshot(
    session_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> SessionSnapshotResponse:
    """Return the authoritative view used to rehydrate after reload or reconnect."""

    return SessionSnapshotResponse.from_domain(service.get_session_snapshot(session_id))


@router.get("/sessions/{session_id}/timers", response_model=TimerListResponse)
def list_timers(
    session_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> TimerListResponse:
    return TimerListResponse.from_domain(service.list_timers(session_id))


@router.post(
    "/sessions/{session_id}/tools/{tool_name}",
    response_model=ToolInvocationResponse,
)
def invoke_tool(
    session_id: str,
    tool_name: str,
    body: InvokeToolRequest,
    request: Request,
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ToolInvocationResponse:
    """Run one allowlisted tool as a direct operator action.

    This is the path a UI button takes. It never involves a language model: the
    button already states the intent, so routing it through text understanding
    would add latency and a failure mode without adding any safety. The tool
    registry still decides legality and whether a confirmation is required.
    """

    result = service.invoke_tool(
        session_id,
        tool_name,
        body.parameters,
        source=EntrySource.TYPED_INPUT,
        model_proposed=False,
        correlation_id=correlation_id(request),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    return ToolInvocationResponse.from_domain(result)


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    body: CreateSessionRequest,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> SessionResponse:
    return SessionResponse.from_domain(
        service.create_session(body.protocol_version_id, label=body.label)
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> SessionResponse:
    return SessionResponse.from_domain(service.get_session(session_id))


@router.post("/sessions/{session_id}/start", response_model=SessionResponse)
def start_session(
    session_id: str,
    expected_version: Annotated[int, Depends(require_if_match)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> SessionResponse:
    return SessionResponse.from_domain(
        service.start_session(session_id, expected_version=expected_version)
    )


@router.post(
    "/sessions/{session_id}/commands/text",
    response_model=CommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def text_command(
    session_id: str,
    body: TextCommandRequest,
    request: Request,
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> CommandResponse:
    result = service.handle_text_command(
        session_id,
        body.transcript,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        correlation_id=correlation_id(request),
    )
    return CommandResponse.from_domain(result)


@router.post(
    "/sessions/{session_id}/commands/audio",
    response_model=CommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def audio_command(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
    require_wake_phrase: Annotated[bool, Query()] = False,
    listen_mode: Annotated[ListenMode, Query()] = ListenMode.COMMAND,
) -> CommandResponse:
    """Transcribe and interpret one utterance.

    ``require_wake_phrase`` is set by the hands-free listener: a segment that
    does not begin with the wake phrase is discarded without interpretation or
    retention and answered with the intent ``WAKE_PHRASE_ABSENT``.

    ``listen_mode`` restricts what a segment may do: ``standby`` (after "stop
    listening") accepts only "start listening"; ``barge_in`` (a segment that
    began while LabJarvis was speaking) accepts only "stop"/"quiet", "mute" and
    "stop the alarm".
    Anything else in those modes is discarded without trace.
    """

    audio = await read_bounded(
        file,
        request.app.state.settings.max_audio_bytes,
        code="AUDIO_TOO_LARGE",
    )
    result = await run_in_threadpool(
        service.handle_audio_command,
        session_id,
        audio,
        content_type=file.content_type or "application/octet-stream",
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        correlation_id=correlation_id(request),
        require_wake_phrase=require_wake_phrase,
        listen_mode=listen_mode,
    )
    return CommandResponse.from_domain(result)


@router.post(
    "/pending-actions/{action_id}/confirm",
    response_model=ConfirmationResponse,
)
def confirm_pending_action(
    action_id: str,
    body: ConfirmPendingActionRequest,
    request: Request,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ConfirmationResponse:
    result = service.confirm_pending_action(
        action_id,
        accepted=body.accepted,
        correlation_id=correlation_id(request),
    )
    return ConfirmationResponse.from_domain(result)


@router.post(
    "/pending-actions/{action_id}/spoken-confirmation",
    response_model=SpokenConfirmationResponse,
)
async def confirm_pending_action_by_voice(
    action_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
    expected_version: Annotated[int | None, Query(ge=0)] = None,
    via_wake_phrase: Annotated[bool, Query()] = False,
) -> SpokenConfirmationResponse:
    """Answer a proposal with a recording of the operator speaking.

    The evidence is audio on purpose. A realtime model can call a function, so
    any confirmation expressible as a function call is one the model could issue
    by itself; it has no way to put a microphone recording into this endpoint.
    The upload is bounded by the same audio limit as a push-to-talk command,
    transcribed through the high-integrity path, and classified deterministically.
    Nothing here trusts the model that made the proposal.

    What this does not establish: the backend cannot authenticate the client, so
    a recording is not proof that a person spoke just now. Keeping the
    assistant's own readback out of the clip is the browser's responsibility,
    and it does that by refusing to record until the readback has demonstrably
    finished. See ADR 0014.

    ``via_wake_phrase`` marks a clip from the always-on listener (voice-only
    mode, opt-in): it must begin with the wake phrase or it is ignored, and it
    must restate the content being confirmed.
    """

    audio = await read_bounded(
        file,
        request.app.state.settings.max_audio_bytes,
        code="AUDIO_TOO_LARGE",
    )
    result = await run_in_threadpool(
        service.confirm_pending_action_by_voice,
        action_id,
        audio,
        content_type=file.content_type or "application/octet-stream",
        idempotency_key=idempotency_key,
        correlation_id=correlation_id(request),
        expected_session_version=expected_version,
        via_wake_phrase=via_wake_phrase,
    )
    return SpokenConfirmationResponse.from_domain(result)


@router.post(
    "/sessions/{session_id}/reagent-scans/probe",
    response_model=LabelProbeResponse,
)
async def probe_reagent_label(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    service: Annotated[LabJarvisService, Depends(get_service)],
    requirement_id: Annotated[str | None, Query(max_length=120)] = None,
) -> LabelProbeResponse:
    """Read one live-scan frame with the local OCR; records nothing.

    Used by the live "scan until stable" mode to find a steady reading. The
    stable frames are then submitted to ``reagent-verifications/frames``,
    which creates the pending reading that a person must still confirm.
    """

    image = await read_bounded(
        file, request.app.state.settings.max_image_bytes, code="IMAGE_TOO_LARGE"
    )
    probe = await run_in_threadpool(
        service.probe_reagent_label,
        session_id,
        image,
        content_type=file.content_type or "application/octet-stream",
        requirement_id=requirement_id,
    )
    return LabelProbeResponse.from_domain(probe)


@router.post(
    "/reagent-verifications/{verification_id}/spoken-decision",
    response_model=SpokenReagentDecisionResponse,
)
async def decide_reagent_verification_by_voice(
    verification_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> SpokenReagentDecisionResponse:
    """Answer a pending label reading by voice (voice-only mode, opt-in).

    "Jarvis, confirm <reagent>" confirms a MATCH; "Jarvis, reject" rejects any
    reading. A MISMATCH or UNCERTAIN reading is never confirmed by voice.
    """

    audio = await read_bounded(
        file,
        request.app.state.settings.max_audio_bytes,
        code="AUDIO_TOO_LARGE",
    )
    result = await run_in_threadpool(
        service.confirm_reagent_verification_by_voice,
        verification_id,
        audio,
        content_type=file.content_type or "application/octet-stream",
        correlation_id=correlation_id(request),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    return SpokenReagentDecisionResponse.from_domain(result)


@router.post(
    "/sessions/{session_id}/reagent-verifications",
    response_model=ReagentVerificationResponse,
)
async def verify_reagent(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
    requirement_id: Annotated[str | None, Query(max_length=120)] = None,
) -> ReagentVerificationResponse:
    """Compare a photographed label against one named requirement of the active step.

    ``requirement_id`` may be omitted only when the step defines exactly one
    reagent; otherwise the caller must say which one, so a match for reagent A is
    never recorded against reagent B.
    """

    image = await read_bounded(
        file,
        request.app.state.settings.max_image_bytes,
        code="IMAGE_TOO_LARGE",
    )
    result = await run_in_threadpool(
        service.verify_reagent,
        session_id,
        image,
        content_type=file.content_type or "application/octet-stream",
        correlation_id=correlation_id(request),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requirement_id=requirement_id,
    )
    return ReagentVerificationResponse.from_domain(result)


@router.post(
    "/sessions/{session_id}/reagent-verifications/frames",
    response_model=ReagentVerificationResponse,
)
async def verify_reagent_frames(
    session_id: str,
    request: Request,
    frames: Annotated[list[UploadFile], File()],
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
    requirement_id: Annotated[str | None, Query(max_length=120)] = None,
    capture_mode: Annotated[
        Literal["single", "countdown", "live_scan"] | None, Query()
    ] = None,
    scan_rule: Annotated[str | None, Query(max_length=60)] = None,
    scan_frames_examined: Annotated[int | None, Query(ge=0, le=1000)] = None,
    scan_elapsed_ms: Annotated[int | None, Query(ge=0, le=600_000)] = None,
) -> ReagentVerificationResponse:
    """Compare several frames of one label and return their deterministic consensus.

    Every frame is read and compared on its own; the result is MATCH or MISMATCH
    only when all frames agree, and UNCERTAIN otherwise (``unanimous-v1``). The
    optional capture fields record how the frames were chosen (provenance only).
    """

    if not 1 <= len(frames) <= 5:
        raise ApplicationError("REAGENT_TOO_MANY_FRAMES", "send between one and five frames")
    content_types = {frame.content_type or "application/octet-stream" for frame in frames}
    if len(content_types) != 1:
        raise ApplicationError("REAGENT_FRAME_TYPES_DIFFER", "all frames must share one type")
    images = [
        await read_bounded(
            frame,
            request.app.state.settings.max_image_bytes,
            code="IMAGE_TOO_LARGE",
        )
        for frame in frames
    ]
    result = await run_in_threadpool(
        service.verify_reagent,
        session_id,
        images[0],
        content_type=content_types.pop(),
        correlation_id=correlation_id(request),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        requirement_id=requirement_id,
        additional_frames=tuple(images[1:]),
        capture_metadata={
            key: str(value)
            for key, value in {
                "mode": capture_mode,
                "scan_rule": scan_rule,
                "scan_frames_examined": scan_frames_examined,
                "scan_elapsed_ms": scan_elapsed_ms,
            }.items()
            if value is not None
        },
    )
    return ReagentVerificationResponse.from_domain(result)


@router.post(
    "/reagent-verifications/{verification_id}/confirm",
    response_model=ReagentVerificationResponse,
)
def confirm_reagent_verification(
    verification_id: str,
    body: ConfirmReagentVerificationRequest,
    request: Request,
    expected_version: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ReagentVerificationResponse:
    result = service.confirm_reagent_verification(
        verification_id,
        accepted=body.accepted,
        corrected_observed_label=body.corrected_observed_label,
        correlation_id=correlation_id(request),
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )
    return ReagentVerificationResponse.from_domain(result)


@router.get("/sessions/{session_id}/notebook", response_model=NotebookResponse)
def list_notebook(
    session_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> NotebookResponse:
    return NotebookResponse.from_domain(service.get_session_review(session_id))


@router.post(
    "/notebook/{entry_id}/corrections",
    response_model=NotebookEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_notebook_entry(
    entry_id: str,
    body: CorrectNotebookEntryRequest,
    request: Request,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> NotebookEntryResponse:
    corrected = service.correct_notebook_entry(
        entry_id,
        body.payload,
        correlation_id=correlation_id(request),
    )
    return NotebookEntryResponse.from_domain(corrected)


@router.get("/sessions/{session_id}/notebook/export")
def export_notebook(
    session_id: str,
    format: Literal["json", "csv"],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> Response:
    exported = service.export_notebook(session_id, format)
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )
