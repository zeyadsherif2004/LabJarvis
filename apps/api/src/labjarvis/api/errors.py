"""RFC 9457-style problem responses without internal exception details."""

from __future__ import annotations

from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from labjarvis.application.errors import ApplicationError
from labjarvis.infrastructure.documents import DocumentError
from labjarvis.infrastructure.model_errors import (
    InferenceTimeoutError,
    ModelInputError,
    ModelOutputError,
    ModelUnavailableError,
)
from labjarvis.ports.repositories import RepositoryConflict
from labjarvis.schemas.api import ProblemDetail


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else str(uuid4())


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    problem = ProblemDetail(
        type=f"https://labjarvis.local/problems/{code.casefold().replace('_', '-')}",
        title=code.replace("_", " ").title(),
        status=status,
        detail=detail,
        code=code,
        correlation_id=correlation_id,
    )
    structlog.get_logger("labjarvis.api").warning(
        "api_problem_returned",
        component="api",
        operation=f"{request.method} {request.url.path}",
        outcome="rejected" if status < 500 else "failed",
        error_code=code,
        status_code=status,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=status,
        content={**problem.model_dump(mode="json"), **(extra or {})},
        media_type="application/problem+json",
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return problem_response(
            request,
            status=exc.status,
            code=exc.code,
            detail=str(exc),
        )

    @app.exception_handler(DocumentError)
    async def document_error_handler(request: Request, exc: DocumentError) -> JSONResponse:
        size_limit_codes = {
            "DOCUMENT_TOO_LARGE",
            "DOCUMENT_EXPANDED_TOO_LARGE",
            "DOCUMENT_TEXT_TOO_LARGE",
            "DOCUMENT_TOO_MANY_ENTRIES",
        }
        status = 413 if exc.code in size_limit_codes else 422
        code = exc.code
        if exc.code == "DOCUMENT_SIGNATURE_MISMATCH":
            status = 415
            code = "UNSUPPORTED_DOCUMENT"
        return problem_response(request, status=status, code=code, detail=str(exc))

    @app.exception_handler(RepositoryConflict)
    async def repository_conflict_handler(
        request: Request,
        exc: RepositoryConflict,
    ) -> JSONResponse:
        return problem_response(request, status=409, code=exc.code, detail=str(exc))

    @app.exception_handler(ModelInputError)
    async def model_input_handler(request: Request, exc: ModelInputError) -> JSONResponse:
        return problem_response(
            request,
            status=422,
            code="MODEL_INPUT_INVALID",
            detail=str(exc),
        )

    @app.exception_handler(ModelOutputError)
    async def model_output_handler(request: Request, exc: ModelOutputError) -> JSONResponse:
        return problem_response(request, status=502, code=exc.code, detail=str(exc))

    @app.exception_handler(ModelUnavailableError)
    async def model_unavailable_handler(
        request: Request, exc: ModelUnavailableError
    ) -> JSONResponse:
        return problem_response(request, status=503, code=exc.code, detail=str(exc))

    @app.exception_handler(InferenceTimeoutError)
    async def inference_timeout_handler(
        request: Request, exc: InferenceTimeoutError
    ) -> JSONResponse:
        return problem_response(request, status=503, code=exc.code, detail=str(exc))

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        if exc.code.endswith("_NOT_FOUND"):
            status = 404
        elif "CONFLICT" in exc.code or exc.code in {
            "CONFIRMATION_AUDIO_REPLAYED",
            "VOICE_ONLY_CONFIRMATION_DISABLED",
            "PENDING_ACTION_EXISTS",
            "PENDING_ACTION_RESOLVED",
            "PROTOCOL_ALREADY_REVIEWED",
            "PROTOCOL_NOT_EDITABLE",
            "REAGENT_VERIFICATION_PENDING",
            "REAGENT_VERIFICATION_RESOLVED",
            "REAGENT_VERIFICATION_STALE",
        }:
            status = 409
        elif exc.code.endswith("_UNAVAILABLE"):
            # A control the deployment does not have configured is a service
            # condition, not a malformed request; the caller should fall back
            # rather than reword what it sent.
            status = 503
        else:
            status = 422
        return problem_response(request, status=status, code=exc.code, detail=str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        return problem_response(
            request,
            status=422,
            code="REQUEST_VALIDATION_FAILED",
            detail="request did not match the documented schema",
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        structlog.get_logger("labjarvis.api").error(
            "unexpected_api_exception",
            component="api",
            operation=f"{request.method} {request.url.path}",
            outcome="failed",
            error_code="INTERNAL_ERROR",
            exception_type=type(exc).__name__,
            correlation_id=_correlation_id(request),
        )
        return problem_response(
            request,
            status=500,
            code="INTERNAL_ERROR",
            detail="an unexpected error occurred; use the correlation ID for diagnostics",
        )
