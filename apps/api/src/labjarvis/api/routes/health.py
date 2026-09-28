"""Process liveness, staged model readiness, and content-free diagnostics."""

from collections import defaultdict
from math import ceil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from labjarvis.application.tools import TOOL_CONTRACT_VERSION
from labjarvis.infrastructure.db.schema import model_inferences_table
from labjarvis.infrastructure.hands_free import hands_free_status
from labjarvis.ports.models import AdapterReadiness
from labjarvis.schemas.api import ReadinessResponse
from labjarvis.settings import STAGES, ModelMode, Settings

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


def _stage_payload(
    value: bool | None,
    *,
    readiness: AdapterReadiness,
    deterministic_fixture: bool,
    model_only: bool,
) -> dict[str, str]:
    if deterministic_fixture and model_only:
        return {"status": "not_applicable", "detail": "deterministic fixture"}
    if value is None:
        return {
            "status": "not_applicable",
            "detail": "stage does not apply to this execution profile",
        }
    if value:
        return {"status": "ready", "detail": "satisfied"}
    if readiness.ready:
        return {
            "status": "not_applicable",
            "detail": "not required before the adapter's first use",
        }
    return {"status": "blocked", "detail": readiness.detail}


def _adapter_payload(readiness: AdapterReadiness) -> dict[str, object]:
    """Render one adapter's staged readiness for the operator UI.

    ``blocking_stage`` is included so the client can say which prerequisite is
    missing without re-deriving the ordering, and ``detail`` carries the
    actionable sentence. Neither ever contains a credential or provider
    response body.
    """

    deterministic = readiness.adapter.startswith("deterministic-")
    stages = {
        "configured": _stage_payload(
            readiness.configured,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=False,
        ),
        "installed": _stage_payload(
            readiness.dependency_installed,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=True,
        ),
        "cached": _stage_payload(
            readiness.model_cached,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=True,
        ),
        "loaded": _stage_payload(
            readiness.loaded,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=True,
        ),
        "reachable": _stage_payload(
            readiness.provider_reachable,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=True,
        ),
        "inference_verified": _stage_payload(
            readiness.inference_tested,
            readiness=readiness,
            deterministic_fixture=deterministic,
            model_only=True,
        ),
    }
    blocking = next(
        (name for name, stage in stages.items() if stage["status"] == "blocked"),
        None,
    )
    payload: dict[str, object] = {
        "ready": readiness.ready,
        "adapter": readiness.adapter,
        "model_name": readiness.model_name,
        "detail": readiness.detail,
        **stages,
        "first_blocking_stage": blocking,
    }
    return payload


def _latency_payload(database: object) -> dict[str, object]:
    rows = database.execute(  # type: ignore[attr-defined]
        select(
            model_inferences_table.c.modality,
            model_inferences_table.c.status,
            model_inferences_table.c.latency_ms,
        )
        .order_by(model_inferences_table.c.created_at.desc())
        .limit(200)
    ).all()
    grouped: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for modality, status, latency_ms in rows:
        grouped[str(modality)].append((str(status), int(latency_ms)))
    modalities: dict[str, object] = {}
    for modality, samples in sorted(grouped.items()):
        latencies = sorted(latency for _, latency in samples)
        p95_index = max(0, ceil(len(latencies) * 0.95) - 1)
        modalities[modality] = {
            "sample_count": len(samples),
            "failed_count": sum(status == "failed" for status, _ in samples),
            "average_ms": round(sum(latencies) / len(latencies), 1),
            "p50_ms": latencies[(len(latencies) - 1) // 2],
            "p95_ms": latencies[p95_index],
        }
    return {
        "max_samples": 200,
        "sample_count": len(rows),
        "modalities": modalities,
    }


def _study_payload(settings: Settings) -> dict[str, str | bool]:
    """Announce study mode so the UI can show its permanent participant banner."""

    if not settings.study_mode_enabled or settings.study_mode_script is None:
        return {"enabled": False}
    from labjarvis.infrastructure.study_mode import load_study_script

    script = load_study_script(settings.study_mode_script)
    return {
        "enabled": True,
        "study_id": script.study_id,
        "participant": script.participant,
        "condition": script.condition,
        "block": str(script.block),
    }


def _stage_provenance(
    settings: Settings,
    models: object,
    warmup: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Per-stage provider, locality and model - never a credential or URL path."""

    adapters = {
        "speech": models.speech,  # type: ignore[attr-defined]
        "text": models.text,  # type: ignore[attr-defined]
        "vision": models.labels,  # type: ignore[attr-defined]
    }
    stages: dict[str, dict[str, str]] = {}
    for stage in STAGES:
        provider = settings.stage_provider(stage)
        hosted = settings.stage_is_hosted(stage)
        locality = settings.stage_locality(stage)
        adapter = adapters[stage]
        detail = ""
        if stage == "text":
            interpreter = settings.effective_text_interpreter().value
            language_model = getattr(adapter, "language_model", None)
            detail = f"interpreter: {interpreter}"
            if language_model is not None:
                detail += f"; language model {language_model.model_name}"
        elif stage == "speech" and provider.value == "local":
            detail = f"device {settings.speech_device}, {settings.speech_compute_type}"
        elif stage == "vision" and provider.value == "local":
            detail = f"device {'gpu' if settings.vision_gpu else 'cpu'}"
        stages[stage] = {
            "provider": provider.value,
            "provider_label": (
                settings.hosted_label_for(stage) if hosted else provider.value
            ),
            # A loopback OpenAI-compatible server is labelled local: its data
            # never leaves this machine even though it uses the hosted adapter.
            "locality": locality,
            "model": str(getattr(adapter, "model_name", "")),
            "detail": detail,
            "warmup": warmup.get(stage, "not_applicable"),
        }
    return stages


def _speech_output_payload(request: Request) -> dict[str, str | bool]:
    """Kokoro or browser voice; Kokoro not ready means the browser speaks instead."""

    settings = request.app.state.settings
    if settings.speech_output != "kokoro":
        return {"engine": "browser", "voice": "", "ready": True, "detail": ""}
    readiness = request.app.state.kokoro.readiness()
    return {
        "engine": "kokoro",
        "voice": settings.kokoro_voice,
        "ready": readiness.ready,
        "loaded": bool(readiness.loaded),
        "detail": readiness.detail,
    }


@router.get("/health/ready", response_model=ReadinessResponse)
def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    database_ready = True
    latency_window: dict[str, object] = {
        "max_samples": 200,
        "sample_count": 0,
        "modalities": {},
    }
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
            latency_window = _latency_payload(session)
    except Exception:
        database_ready = False

    models = request.app.state.model_adapters
    adapters = {
        "speech": _adapter_payload(models.speech.readiness()),
        "text": _adapter_payload(models.text.readiness()),
        "vision": _adapter_payload(models.labels.readiness()),
        "document_ocr": _adapter_payload(models.documents.readiness()),
    }
    is_ready = database_ready and all(
        bool(adapters[name]["ready"]) for name in ("speech", "text", "vision")
    )
    hosted = settings.any_stage_remote
    warmup_tracker = getattr(request.app.state, "warmup", None)
    warmup = warmup_tracker.snapshot() if warmup_tracker is not None else {}
    # Readiness reports whether hosted processing is active and which provider is
    # configured. It never carries the credential, provider error bodies, or user
    # content - the UI only needs enough to warn the operator accurately.
    payload: dict[str, object] = {
        "status": "ready" if is_ready else "not_ready",
        "mode": settings.model_mode.value,
        "tool_contract_version": TOOL_CONTRACT_VERSION,
        "interaction": {
            "wake_phrase": settings.wake_phrase,
            "realtime_enabled": settings.realtime_enabled,
            "realtime_transport": settings.realtime_transport.value,
            "realtime_voice": settings.realtime_voice,
            # The model identifier is reported so a recorded session can be tied
            # to the exact model that produced it, rather than to a moving alias.
            "realtime_model": settings.realtime_model_name if settings.realtime_enabled else "",
            # Published so the browser records a confirmation for exactly as
            # long as this deployment allows, rather than a figure compiled in.
            "realtime_confirmation_seconds": settings.realtime_confirmation_seconds,
            "hands_free_listening": settings.hands_free_listening_enabled,
            "voice_only_confirmation": settings.voice_only_confirmation_enabled,
            "live_label_scan": settings.live_label_scan_enabled,
            "live_scan_required_consecutive": settings.live_scan_required_consecutive,
            "live_scan_interval_ms": settings.live_scan_interval_ms,
            "live_scan_timeout_ms": settings.live_scan_timeout_seconds * 1000,
            "hands_free_profile": settings.hands_free_profile,
            "hands_free_autostart": settings.hands_free_autostart,
            "timer_alarms": settings.timer_alarms_enabled,
            "timer_alarm_repeat_seconds": settings.timer_alarm_repeat_seconds,
            "timer_warning_seconds": ",".join(str(s) for s in settings.timer_warning_seconds),
            "voice_observations": settings.voice_observations_enabled,
            "spoken_detail": settings.spoken_detail,
        },
        "hands_free": hands_free_status(settings, models),
        "privacy": {
            "raw_media_retention": settings.retain_raw_media,
            "transcript_retention": settings.retain_transcripts,
            "external_model_processing": hosted or settings.realtime_enabled,
        },
        "adapters": adapters,
        "database": {"ready": database_ready},
        "latency_window": latency_window,
        "profile": settings.execution_profile(),
        "study": _study_payload(settings),
        "stages": _stage_provenance(settings, models, warmup),
        "speech_output": _speech_output_payload(request),
    }
    if hosted:
        hosted_labels = sorted(
            {
                settings.hosted_label_for(stage)
                for stage in STAGES
                if settings.stage_is_remote(stage)
            }
        )
        payload["hosted_provider"] = (
            settings.hosted_provider.value
            if settings.model_mode is ModelMode.HOSTED
            and all(getattr(settings, f"{stage}_provider") is None for stage in STAGES)
            else ", ".join(hosted_labels) or settings.hosted_provider.value
        )
    # A 503 here means "this service answered, and something it depends on is not
    # usable yet". Clients must render the adapter detail rather than reporting
    # the API as unreachable: the two need very different operator responses.
    return JSONResponse(status_code=200 if is_ready else 503, content=payload)
