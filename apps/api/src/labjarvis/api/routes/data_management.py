"""Explicit local aggregate-deletion endpoints."""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends

from labjarvis.api.dependencies import get_backup_service, get_data_management
from labjarvis.api.errors import ApiError
from labjarvis.application.backup import BackupError, BackupService
from labjarvis.application.data_management import DataManagementError, DataManagementService
from labjarvis.schemas.api import (
    BackupPackageResponse,
    DeleteConfirmationRequest,
    DeletionReceiptResponse,
    RestoreValidationResponse,
)

router = APIRouter(prefix="/api/v1/data", tags=["data-management"])


def _raise_api_error(exc: DataManagementError) -> NoReturn:
    if exc.code.endswith("_NOT_FOUND"):
        status = 404
    elif exc.code in {"PROTOCOL_DELETE_REFERENCED", "PROTOCOL_DELETE_NOT_ELIGIBLE"}:
        status = 409
    elif exc.code == "DELETION_RECEIPT_WRITE_FAILED":
        status = 500
    else:
        status = 422
    raise ApiError(status, exc.code, str(exc)) from exc


def _raise_backup_error(exc: BackupError) -> NoReturn:
    if exc.code == "BACKUP_NOT_FOUND":
        status = 404
    elif exc.code in {"BACKUP_CREATE_FAILED", "RESTORE_STAGING_FAILED"}:
        status = 500
    elif exc.code == "BACKUP_PROFILE_UNSUPPORTED":
        status = 409
    else:
        status = 422
    raise ApiError(status, exc.code, str(exc)) from exc


@router.post("/backups", response_model=BackupPackageResponse, status_code=201)
def create_backup(
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> BackupPackageResponse:
    try:
        package = service.create_backup()
    except BackupError as exc:
        _raise_backup_error(exc)
    return BackupPackageResponse.from_domain(package)


@router.post(
    "/backups/{backup_name}/validate",
    response_model=RestoreValidationResponse,
)
def validate_restore(
    backup_name: str,
    service: Annotated[BackupService, Depends(get_backup_service)],
) -> RestoreValidationResponse:
    try:
        result = service.validate_restore(backup_name)
    except BackupError as exc:
        _raise_backup_error(exc)
    return RestoreValidationResponse.from_domain(result)


@router.delete("/sessions/{session_id}", response_model=DeletionReceiptResponse)
def delete_session(
    session_id: str,
    body: DeleteConfirmationRequest,
    service: Annotated[DataManagementService, Depends(get_data_management)],
) -> DeletionReceiptResponse:
    try:
        receipt = service.delete_session_aggregate(
            session_id,
            confirmation=body.confirmation,
        )
    except DataManagementError as exc:
        _raise_api_error(exc)
    return DeletionReceiptResponse.from_domain(receipt)


@router.delete("/protocols/{protocol_id}", response_model=DeletionReceiptResponse)
def delete_protocol(
    protocol_id: str,
    body: DeleteConfirmationRequest,
    service: Annotated[DataManagementService, Depends(get_data_management)],
) -> DeletionReceiptResponse:
    try:
        receipt = service.delete_protocol_aggregate(
            protocol_id,
            confirmation=body.confirmation,
        )
    except DataManagementError as exc:
        _raise_api_error(exc)
    return DeletionReceiptResponse.from_domain(receipt)
