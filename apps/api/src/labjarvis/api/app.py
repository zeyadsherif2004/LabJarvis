"""LabJarvis FastAPI composition root."""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from labjarvis.api.errors import install_error_handlers
from labjarvis.api.middleware import correlation_middleware
from labjarvis.api.origin_guard import origin_guard_middleware
from labjarvis.api.routes import (
    configuration,
    health,
    protocols,
    realtime,
    sessions,
    speech_output,
)
from labjarvis.api.routes import data_management as data_management_routes
from labjarvis.application.backup import BackupService, UnavailableBackupManager
from labjarvis.application.data_management import DataManagementService
from labjarvis.application.service import LabJarvisService
from labjarvis.infrastructure.database import (
    SqlAlchemyUnitOfWork,
    create_session_factory,
    verify_schema_is_current,
)
from labjarvis.infrastructure.deletion_receipts import LocalDeletionReceiptStore
from labjarvis.infrastructure.documents import DocumentExtractor
from labjarvis.infrastructure.hands_free import preflight as hands_free_preflight
from labjarvis.infrastructure.inference import BoundedInferenceRunner, DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore, LocalRawMediaStore
from labjarvis.infrastructure.model_factory import (
    build_model_adapters,
    build_realtime_sessions,
)
from labjarvis.infrastructure.restart import RestartController
from labjarvis.infrastructure.speech_output import KokoroSpeechSynthesis
from labjarvis.infrastructure.sqlite_backup import SQLiteBackupManager
from labjarvis.infrastructure.warmup import WarmupTracker
from labjarvis.observability import configure_logging
from labjarvis.saved_configuration import missing_hands_free_models
from labjarvis.schemas.api import ProblemDetail
from labjarvis.settings import ModelMode, Settings, get_settings


def _warm_quietly(adapter: KokoroSpeechSynthesis) -> None:
    try:
        adapter.warm_up()
    except Exception:
        return


def create_app(settings: Settings | None = None, *, create_schema: bool = False) -> FastAPI:
    """Compose the application against an **already migrated** database.

    Startup does not touch the schema. It used to: ``create_session_factory``
    defaulted to running ``metadata.create_all()``, so booting against a
    database stamped at an older revision created current tables ahead of the
    migrations that own them and left it unable to migrate at all. Alembic owns
    production schema, and a database behind head is now a loud startup failure
    rather than a silent mutation.

    ``create_schema`` is for isolated tests that build a throwaway database and
    never run Alembic. Production passes nothing, and no environment variable
    can turn it on.
    """

    configured = settings or get_settings()
    configure_logging(configured)
    configured.data_dir.mkdir(parents=True, exist_ok=True)
    if not create_schema:
        verify_schema_is_current(configured.database_url)
    session_factory = create_session_factory(
        configured.database_url, create_schema=create_schema
    )
    model_adapters = build_model_adapters(configured)
    # The hands-free profile refuses to start without its local models.
    hands_free_preflight(configured, model_adapters)
    realtime_sessions = build_realtime_sessions(configured)
    raw_media_store = (
        LocalRawMediaStore(configured.data_dir)
        if configured.retain_raw_media
        else DiscardRawMediaStore()
    )
    # Only the deterministic fake profile runs inline. Local and hosted both go
    # through the bounded runner so caller deadlines and the concurrency ceiling
    # apply to network-backed inference too.
    inference_runner = (
        DirectInferenceRunner()
        if configured.model_mode is ModelMode.FAKE
        else BoundedInferenceRunner(
            max_workers=configured.inference_concurrency,
            timeout_seconds=configured.inference_timeout_seconds,
        )
    )
    service = LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        document_extractor=DocumentExtractor(
            max_bytes=configured.max_document_bytes,
            max_pages=configured.max_document_pages,
            max_docx_uncompressed_bytes=configured.max_docx_uncompressed_bytes,
            max_docx_entries=configured.max_docx_entries,
            max_extracted_characters=configured.max_extracted_characters,
            document_ocr=model_adapters.documents,
            min_pdf_text_characters_per_page=(
                configured.document_min_text_characters_per_page
            ),
        ),
        speech=model_adapters.speech,
        verification_speech=model_adapters.verification_speech,
        text=model_adapters.text,
        labels=model_adapters.labels,
        inference_runner=inference_runner,
        clock=lambda: datetime.now(UTC),
        new_id=lambda: str(uuid4()),
        command_confidence_threshold=configured.command_confidence_threshold,
        reagent_match_threshold=configured.reagent_match_threshold,
        reagent_uncertain_threshold=configured.reagent_uncertain_threshold,
        raw_media_store=raw_media_store,
        retain_transcripts=configured.retain_transcripts,
        wake_phrase=configured.wake_phrase,
        voice_only_confirmation=configured.voice_only_confirmation_enabled,
        live_label_scan=configured.live_label_scan_enabled,
        voice_observations=configured.voice_observations_enabled,
        application_commit=configured.application_commit,
        document_ocr=model_adapters.documents,
    )
    data_management = DataManagementService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        receipt_store=LocalDeletionReceiptStore(configured.data_dir),
        raw_media_store=raw_media_store,
        clock=lambda: datetime.now(UTC),
        new_id=lambda: str(uuid4()),
    )
    backup_manager = (
        SQLiteBackupManager(
            database_url=configured.database_url,
            data_dir=configured.data_dir,
            backup_dir=configured.backup_dir or configured.data_dir / "backups",
            profile=configured.model_mode.value,
            application_commit=configured.application_commit,
        )
        if configured.database_url.casefold().startswith("sqlite")
        else UnavailableBackupManager()
    )
    backup_service = BackupService(
        manager=backup_manager,
        clock=lambda: datetime.now(UTC),
        new_id=lambda: str(uuid4()),
    )
    problem_responses: dict[int | str, dict[str, Any]] = {
        code: {"model": ProblemDetail, "description": "Problem Details"}
        for code in (400, 404, 409, 413, 415, 422, 428, 500, 502, 503)
    }

    # Built unconditionally but loaded lazily: the Settings page can preview a
    # voice before Kokoro is chosen, and nothing is read from disk until then.
    kokoro = KokoroSpeechSynthesis(
        cache_dir=configured.model_cache_dir / "kokoro",
        voice=configured.kokoro_voice,
        speed=configured.kokoro_speed,
    )

    warmup = WarmupTracker(
        {
            "speech": model_adapters.speech,
            "text": model_adapters.text,
            "vision": model_adapters.labels,
        }
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if configured.warm_models_on_startup:
            # Loads in a daemon thread: the API answers immediately and
            # readiness shows each stage as warming/ready/failed.
            warmup.start()
        if configured.speech_output == "kokoro" and kokoro.readiness().ready:
            # Always warmed (it is small), so the first spoken reply is quick.
            threading.Thread(target=_warm_quietly, args=(kokoro,), daemon=True).start()
        yield
        kokoro.close()
        inference_runner.close()
        model_adapters.close()
        if realtime_sessions is not None:
            realtime_sessions.close()

    app = FastAPI(
        title="LabJarvis API",
        version="1.0.0",
        description="Local-first multimodal sterile-lab protocol assistant",
        responses=problem_responses,
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.session_factory = session_factory
    app.state.service = service
    app.state.data_management = data_management
    app.state.backup_service = backup_service
    app.state.model_adapters = model_adapters
    app.state.realtime_sessions = realtime_sessions
    app.state.inference_runner = inference_runner
    app.state.raw_media_store = raw_media_store
    app.state.warmup = warmup
    app.state.kokoro = kokoro
    # Identifies this process so the Settings page can tell a restart finished.
    app.state.instance_id = str(uuid4())
    app.state.restart_controller = RestartController()
    app.state.local_model_check = missing_hands_free_models
    # Starlette inserts each new middleware at the outside of the stack, so the
    # guard is registered first and correlation second: a refused request is
    # still logged and still answers with a correlation ID, while never reaching
    # a route handler. CORS governs what a page may *read*; this governs what it
    # may *change*.
    app.middleware("http")(origin_guard_middleware)
    app.middleware("http")(correlation_middleware)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(configured.allowed_hosts),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "If-Match",
            "Idempotency-Key",
            "X-Correlation-ID",
        ],
        expose_headers=["Content-Disposition", "X-Correlation-ID"],
    )
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(data_management_routes.router)
    app.include_router(protocols.router)
    app.include_router(sessions.router)
    app.include_router(realtime.router)
    app.include_router(configuration.router)
    app.include_router(speech_output.router)
    return app
