"""Realtime voice session brokering.

The browser asks this endpoint for permission to open a realtime session. It
receives a short-lived grant and the authoritative session snapshot together, so
a newly opened or reconnected voice session always begins from database state
rather than from whatever the previous conversation appeared to establish.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from labjarvis.api.dependencies import get_service
from labjarvis.api.errors import ApiError
from labjarvis.application.service import LabJarvisService
from labjarvis.application.tools import realtime_tool_schemas
from labjarvis.infrastructure.realtime import REALTIME_INSTRUCTIONS
from labjarvis.schemas.api import RealtimeSessionRequest, RealtimeSessionResponse

router = APIRouter(prefix="/api/v1", tags=["realtime"])


@router.post("/realtime/sessions", response_model=RealtimeSessionResponse)
def create_realtime_session(
    body: RealtimeSessionRequest,
    request: Request,
    service: Annotated[LabJarvisService, Depends(get_service)],
) -> RealtimeSessionResponse:
    """Mint a short-lived realtime grant scoped to one laboratory session.

    The long-lived provider key stays in this process. What the browser gets is
    a credential that expires within minutes and is already bound to one model,
    one voice, and the realtime-exposed tool allowlist.

    The response also carries a full session snapshot. That is deliberate: it
    means a reconnecting client has no reason to reconstruct state from the
    conversation, and any outstanding proposal is visible before the model
    speaks again.
    """

    settings = request.app.state.settings
    if not settings.realtime_enabled:
        raise ApiError(
            409,
            "REALTIME_DISABLED",
            "realtime voice is not enabled on this deployment; typed and manual "
            "controls remain available",
        )
    factory = getattr(request.app.state, "realtime_sessions", None)
    if factory is None:  # pragma: no cover - composition guarantees this pairing
        raise ApiError(
            503,
            "REALTIME_UNAVAILABLE",
            "realtime voice is enabled but no provider is configured",
        )

    # Loading the snapshot first also validates that the session exists and is
    # readable before any billable provider call is made.
    snapshot = service.get_session_snapshot(body.session_id)
    tools = realtime_tool_schemas()
    grant = factory.create_grant(instructions=REALTIME_INSTRUCTIONS, tools=tools)
    service.record_realtime_grant(
        body.session_id,
        provider=factory.provider_name,
        model_name=grant.model_name,
        transport=grant.transport,
        voice=grant.voice,
        provider_session_id=grant.provider_session_id,
        tool_names=grant.tool_names,
        correlation_id=request.state.correlation_id,
    )
    return RealtimeSessionResponse.from_domain(
        grant,
        snapshot,
        max_utterance_seconds=settings.realtime_max_utterance_seconds,
        idle_timeout_seconds=settings.realtime_idle_timeout_seconds,
    )
