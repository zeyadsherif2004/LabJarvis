"""In-app configuration: read, validate and save choices; restart to apply them.

Model and execution choices apply at startup only (ADR 0019). Saving never
touches a running model; restarting is a single, fixed action carried out by
the launcher that started LabJarvis, requested only from this computer.
No response here contains a credential, an endpoint URL or a provider body.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from labjarvis.api.errors import ApiError, problem_response
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import SessionState
from labjarvis.infrastructure.restart import RestartController
from labjarvis.saved_configuration import (
    ModelConfiguration,
    configuration_state,
    current_environment,
    next_start_settings,
    validate_configuration,
    write_saved_configuration,
)
from labjarvis.settings import LOOPBACK_HOSTS, Settings

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

ACTIVE_SESSION_WARNING = (
    "A laboratory session is currently active. Restarting LabJarvis will temporarily "
    "interrupt the interface. All already-persisted protocol, session and notebook state "
    "will be preserved; anything typed but not yet submitted on screen will be lost."
)


class RestartRequest(BaseModel):
    """The whole restart request. There is nothing to choose but consent."""

    model_config = ConfigDict(extra="forbid")

    acknowledge_active_session: bool = False


def _require_this_computer(request: Request) -> None:
    host = request.client.host if request.client is not None else None
    if host not in LOOPBACK_HOSTS:
        raise ApiError(
            403,
            "SETTINGS_LOCAL_ONLY",
            "configuration can only be changed, and LabJarvis restarted, from the computer "
            "it runs on",
        )


def _active_sessions(service: LabJarvisService) -> int:
    return len(
        service.list_sessions(
            limit=100, states=(SessionState.ACTIVE, SessionState.PAUSED)
        )
    )


def _state(request: Request) -> dict[str, object]:
    controller: RestartController = request.app.state.restart_controller
    kokoro = request.app.state.kokoro
    readiness = kokoro.readiness()
    return configuration_state(
        request.app.state.settings,
        restart=controller.availability().as_dict(),
        voice_output={
            "kokoro_installed": bool(readiness.dependency_installed and readiness.model_cached),
            "kokoro_detail": readiness.detail,
            "voices": kokoro.voices(),
        },
        active_sessions=_active_sessions(request.app.state.service),
        instance_id=request.app.state.instance_id,
    )


@router.get("")
def read_configuration(request: Request) -> dict[str, object]:
    """Active configuration, saved configuration, and whether a restart is needed."""

    return _state(request)


@router.put("")
def save_configuration(request: Request, proposed: ModelConfiguration) -> JSONResponse:
    """Validate the whole proposal as the next startup would; save only if valid."""

    _require_this_computer(request)
    settings: Settings = request.app.state.settings
    _, problems = validate_configuration(
        proposed,
        current_environment(settings),
        local_model_check=request.app.state.local_model_check,
    )
    if problems:
        return problem_response(
            request,
            status=422,
            code="CONFIGURATION_INVALID",
            detail=" ".join(problems),
            extra={"problems": problems},
        )
    write_saved_configuration(settings.data_dir, proposed)
    return JSONResponse(status_code=200, content=_state(request))


@router.post("/restart", status_code=202)
def restart_labjarvis(
    request: Request, body: RestartRequest, background: BackgroundTasks
) -> dict[str, object]:
    """Ask the supervising launcher to restart LabJarvis with the saved configuration."""

    _require_this_computer(request)
    controller: RestartController = request.app.state.restart_controller
    availability = controller.availability()
    if not availability.available:
        raise ApiError(409, "RESTART_UNAVAILABLE", availability.reason or "restart unavailable")
    settings: Settings = request.app.state.settings
    next_settings, problem = next_start_settings(settings.data_dir)
    if next_settings is None:
        raise ApiError(
            409,
            "SAVED_CONFIGURATION_INVALID",
            f"LabJarvis would not start with the saved configuration: {problem}",
        )
    missing = request.app.state.local_model_check(next_settings)
    if missing:
        raise ApiError(
            409,
            "SAVED_CONFIGURATION_INVALID",
            "LabJarvis would not start with the saved configuration: " + "; ".join(missing),
        )
    if _active_sessions(request.app.state.service) and not body.acknowledge_active_session:
        raise ApiError(409, "RESTART_CONFIRMATION_REQUIRED", ACTIVE_SESSION_WARNING)
    controller.request_restart()
    # Runs after this response is sent: the page learns the restart began.
    background.add_task(controller.shutdown)
    return {"restarting": True, "instance_id": request.app.state.instance_id}
