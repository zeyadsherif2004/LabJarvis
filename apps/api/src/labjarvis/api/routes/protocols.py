"""Protocol upload, review, and approval routes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from labjarvis.api.dependencies import (
    get_service,
    require_idempotency_key,
    require_if_match,
)
from labjarvis.api.errors import ApiError
from labjarvis.application.service import LabJarvisService
from labjarvis.schemas.api import (
    ApproveAndStartResponse,
    ProtocolImportResponse,
    ProtocolListResponse,
    ProtocolResponse,
    ProtocolSourceResponse,
    ProtocolVersionResponse,
    ReviewProtocolRequest,
    SessionResponse,
)

router = APIRouter(prefix="/api/v1", tags=["protocols"])


async def read_bounded(file: UploadFile, limit: int, *, code: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        while chunk := await file.read(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ApiError(413, code, "uploaded media exceeds the configured size limit")
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)


@router.get("/protocols", response_model=ProtocolListResponse)
def list_protocols(
    service: Annotated[LabJarvisService, Depends(get_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=250)] = None,
    archived: Annotated[bool, Query()] = False,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> ProtocolListResponse:
    """List recent protocols with their newest version for the dashboard."""

    return ProtocolListResponse.from_domain(
        service.list_protocols(
            limit=limit,
            query=q,
            archived=archived,
            created_from=created_from,
            created_to=created_to,
        )
    )


@router.post(
    "/protocol-versions/{version_id}/approve-and-start",
    response_model=ApproveAndStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def approve_and_start(
    version_id: str,
    expected_revision: Annotated[int, Depends(require_if_match)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ApproveAndStartResponse:
    """Approve a reviewed draft and open its session in one transaction.

    Approving and creating a session as two calls left a window where the
    protocol was approved but no session existed, and a client that lost the
    response could not tell which half had happened. Here either both are
    committed or neither is.
    """

    version, session = service.approve_protocol_and_create_session(
        version_id,
        expected_revision=expected_revision,
    )
    return ApproveAndStartResponse(
        version=ProtocolVersionResponse.from_domain(version),
        session=SessionResponse.from_domain(session),
    )


@router.post(
    "/protocol-versions/{version_id}/review-approve-and-start",
    response_model=ApproveAndStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_approve_and_start(
    version_id: str,
    body: ReviewProtocolRequest,
    expected_revision: Annotated[int, Depends(require_if_match)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    request: Request,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ApproveAndStartResponse:
    """Save reviewed steps, approve them, and open the session in one transaction.

    The browser used to do this as ``PUT`` then ``POST``. When the second call
    failed, the reviewed revision was already persisted and the browser held a
    stale revision number, so every retry failed the ``If-Match`` check with no
    way to see what had happened. Here either all three effects are committed or
    none are, and replaying the idempotency key returns the original result
    rather than approving twice.
    """

    version, session = service.review_approve_and_create_session(
        version_id,
        tuple(step.to_domain() for step in body.steps),
        expected_revision=expected_revision,
        label=body.label,
        idempotency_key=idempotency_key,
        correlation_id=request.state.correlation_id,
    )
    return ApproveAndStartResponse(
        version=ProtocolVersionResponse.from_domain(version),
        session=SessionResponse.from_domain(session),
    )


@router.post(
    "/protocols/upload",
    response_model=ProtocolImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_protocol(
    request: Request,
    file: Annotated[UploadFile, File()],
    service: Annotated[LabJarvisService, Depends(get_service)],
    ocr: Annotated[bool, Query()] = False,
) -> ProtocolImportResponse:
    settings = request.app.state.settings
    data = await read_bounded(file, settings.max_document_bytes, code="DOCUMENT_TOO_LARGE")
    imported = await run_in_threadpool(
        service.import_protocol,
        file.filename or "upload",
        file.content_type or "application/octet-stream",
        data,
        allow_ocr=ocr,
        correlation_id=request.state.correlation_id,
    )
    return ProtocolImportResponse(
        protocol_id=imported.protocol.id,
        title=imported.protocol.title,
        version=ProtocolVersionResponse.from_domain(imported.version),
    )


@router.get("/protocols/{protocol_id}", response_model=ProtocolResponse)
def get_protocol(
    protocol_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolResponse:
    return ProtocolResponse.from_domain(service.get_protocol(protocol_id))


@router.get("/protocol-versions/{version_id}", response_model=ProtocolVersionResponse)
def get_protocol_version(
    version_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolVersionResponse:
    return ProtocolVersionResponse.from_domain(service.get_protocol_version(version_id))


@router.post(
    "/protocol-versions/{version_id}/copy",
    response_model=ProtocolVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_protocol_version(
    version_id: str,
    request: Request,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolVersionResponse:
    return ProtocolVersionResponse.from_domain(
        service.copy_approved_protocol_version(
            version_id,
            idempotency_key=idempotency_key,
            correlation_id=request.state.correlation_id,
        )
    )


@router.post("/protocols/{protocol_id}/archive", response_model=ProtocolResponse)
def archive_protocol(
    protocol_id: str,
    request: Request,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolResponse:
    return ProtocolResponse.from_domain(
        service.set_protocol_archived(
            protocol_id,
            archived=True,
            idempotency_key=idempotency_key,
            correlation_id=request.state.correlation_id,
        )
    )


@router.post("/protocols/{protocol_id}/restore", response_model=ProtocolResponse)
def restore_protocol(
    protocol_id: str,
    request: Request,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolResponse:
    return ProtocolResponse.from_domain(
        service.set_protocol_archived(
            protocol_id,
            archived=False,
            idempotency_key=idempotency_key,
            correlation_id=request.state.correlation_id,
        )
    )


@router.get(
    "/protocol-versions/{version_id}/source",
    response_model=ProtocolSourceResponse,
)
def get_protocol_source(
    version_id: str,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolSourceResponse:
    """Return the complete extracted source text for side-by-side review.

    Served separately from the version itself so that listing protocols, loading
    a snapshot, and restoring a session do not each carry a whole document.
    """

    return ProtocolSourceResponse.from_domain(service.get_protocol_source(version_id))


@router.put(
    "/protocol-versions/{version_id}",
    response_model=ProtocolVersionResponse,
)
def review_protocol(
    version_id: str,
    body: ReviewProtocolRequest,
    expected_revision: Annotated[int, Depends(require_if_match)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolVersionResponse:
    reviewed = service.update_protocol_draft(
        version_id,
        tuple(step.to_domain() for step in body.steps),
        expected_revision=expected_revision,
    )
    return ProtocolVersionResponse.from_domain(reviewed)


@router.post(
    "/protocol-versions/{version_id}/approve",
    response_model=ProtocolVersionResponse,
)
def approve_protocol(
    version_id: str,
    expected_revision: Annotated[int, Depends(require_if_match)],
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> ProtocolVersionResponse:
    approved = service.approve_protocol(version_id, expected_revision=expected_revision)
    return ProtocolVersionResponse.from_domain(approved)
