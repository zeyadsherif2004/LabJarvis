"""Shared FastAPI dependencies for services and mutation preconditions."""

import re
from typing import Annotated

from fastapi import Header, Request

from labjarvis.api.errors import ApiError
from labjarvis.application.backup import BackupService
from labjarvis.application.data_management import DataManagementService
from labjarvis.application.service import LabJarvisService

ETAG_PATTERN = re.compile(r'^(?:W/)?"?(?P<version>\d+)"?$')


def get_service(request: Request) -> LabJarvisService:
    service: LabJarvisService = request.app.state.service
    return service


def get_data_management(request: Request) -> DataManagementService:
    service: DataManagementService = request.app.state.data_management
    return service


def get_backup_service(request: Request) -> BackupService:
    service: BackupService = request.app.state.backup_service
    return service


def require_if_match(
    value: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int:
    if value is None:
        raise ApiError(428, "IF_MATCH_REQUIRED", "If-Match version header is required")
    match = ETAG_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ApiError(400, "IF_MATCH_INVALID", "If-Match must contain a numeric version")
    return int(match.group("version"))


def require_idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if value is None or not value.strip():
        raise ApiError(
            428,
            "IDEMPOTENCY_KEY_REQUIRED",
            "Idempotency-Key header is required",
        )
    if len(value) > 120:
        raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", "idempotency key is too long")
    return value
