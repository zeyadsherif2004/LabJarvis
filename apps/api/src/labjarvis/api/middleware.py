"""Request correlation and conservative browser-facing response headers."""

import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import Request, Response

CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


async def correlation_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    proposed = request.headers.get("X-Correlation-ID", "")
    correlation_id = proposed if CORRELATION_PATTERN.fullmatch(proposed) else str(uuid4())
    request.state.correlation_id = correlation_id
    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    finally:
        structlog.get_logger("labjarvis.http").info(
            "http_request_completed",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            correlation_id=correlation_id,
        )
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
