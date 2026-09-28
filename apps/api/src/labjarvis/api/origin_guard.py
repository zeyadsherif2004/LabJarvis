"""Refuse state changes requested by a page LabJarvis does not serve.

CORS is a *read* control. It stops a hostile page from seeing a response; it
does not stop the request from being made, and for a "simple" request - a form
post or a ``multipart/form-data`` upload with no custom headers - the browser
sends it without asking permission first. A bounded security scan confirmed the
consequence on this codebase: a request carrying ``Origin: https://attacker.example``
reached the protocol upload endpoint and was answered with ``201 Created``. The
attacker could not read the reply, but a protocol record existed afterwards, and
in a system whose whole premise is that laboratory records are trustworthy that
is the part that matters.

The fix is an allowlist check on the ``Origin`` header itself, applied to every
state-changing method before routing.

**Requests with no ``Origin`` header are allowed**, and that is a deliberate
decision rather than an oversight:

* A browser attaches ``Origin`` to every cross-origin request and to every
  same-origin request that is not a ``GET`` or ``HEAD``. The attack this
  addresses therefore always carries the header, and is always caught.
* Non-browser clients - ``curl``, the repository's own scripts, an integration
  test - do not send one. Rejecting them would break local operation without
  closing any browser-reachable path.

That trade is only sound because the supported deployment binds to loopback and
serves a single local operator, so "a process on this machine" is already inside
the trust boundary. Exposing the API to a network would need a real
authentication and CSRF design, which SECURITY.md states is out of scope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from labjarvis.api.errors import problem_response

#: Methods that can change stored state. ``OPTIONS`` is excluded so the CORS
#: preflight still answers normally; a preflight changes nothing by itself, and
#: the request it precedes is checked here on arrival.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _normalise(origin: str) -> str:
    return origin.strip().rstrip("/").casefold()


async def origin_guard_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Reject a state-changing request from an origin that is not allowlisted."""

    if request.method not in STATE_CHANGING_METHODS:
        return await call_next(request)

    origin = request.headers.get("Origin")
    if origin is None or not origin.strip():
        # No browser context. See the module docstring for why this is allowed.
        return await call_next(request)

    allowed = {
        _normalise(candidate) for candidate in request.app.state.settings.cors_origins
    }
    if _normalise(origin) in allowed:
        return await call_next(request)

    # Refused before routing, so no handler runs and nothing is written.
    return problem_response(
        request,
        status=403,
        code="ORIGIN_NOT_ALLOWED",
        detail=(
            "this request came from a web page LabJarvis does not serve, so it was "
            "refused before anything was read or written"
        ),
    )
