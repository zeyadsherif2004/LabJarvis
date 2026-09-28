"""Hands-free profile preflight and the one-line readiness summary (ADR 0018).

The profile promises that a gloved researcher can run the whole bench
workflow by voice. Required local models must be cached before startup; a
hosted Vision stage uses one timed capture rather than repeated local probes.
"""

from __future__ import annotations

from typing import Any

from labjarvis.ports.models import AdapterReadiness
from labjarvis.settings import Settings, TextInterpreter


class HandsFreeProfileError(RuntimeError):
    """The hands-free profile was requested but cannot be honoured."""


def _local_requirements(settings: Settings, adapters: Any) -> list[tuple[str, Any]]:
    required: list[tuple[str, Any]] = []
    if settings.stage_locality("speech") == "local":
        required.append(("speech", adapters.speech))
        if adapters.verification_speech is not None:
            required.append(("speech cross-check", adapters.verification_speech))
    if settings.stage_locality("vision") == "local":
        required.append(("label OCR", adapters.labels))
    if (
        settings.stage_locality("text") == "local"
        and settings.effective_text_interpreter() is not TextInterpreter.RULES
    ):
        language_model = getattr(adapters.text, "language_model", None)
        if language_model is not None:
            required.append(("text fallback", language_model))
    return required


def missing_models(settings: Settings, adapters: Any) -> list[str]:
    """Every required local model that is not installed and cached, with the reason."""

    missing: list[str] = []
    for label, adapter in _local_requirements(settings, adapters):
        try:
            readiness: AdapterReadiness = adapter.readiness()
        except Exception as exc:  # pragma: no cover - adapters report, not raise
            missing.append(f"{label}: readiness check failed ({type(exc).__name__})")
            continue
        if not readiness.ready or readiness.model_cached is False:
            missing.append(f"{label} model {readiness.model_name}: {readiness.detail}")
    return missing


def preflight(settings: Settings, adapters: Any) -> None:
    """Refuse to start the hands-free profile with a required local model missing."""

    if not settings.hands_free_profile:
        return
    missing = missing_models(settings, adapters)
    if missing:
        raise HandsFreeProfileError(
            "LABJARVIS_HANDS_FREE_PROFILE is on but required local models are missing: "
            + "; ".join(missing)
            + ". Run `python scripts/tasks.py models` (or pull the Ollama model) and start "
            "again, or switch the profile off."
        )


def hands_free_status(settings: Settings, adapters: Any) -> dict[str, object]:
    """One line for Readiness and Diagnostics: ready, off, or exactly what is missing."""

    local_vision = not settings.stage_is_remote("vision")
    features = {
        "listening": settings.hands_free_listening_enabled,
        "autostart": settings.hands_free_autostart,
        "voice-only confirmation": settings.voice_only_confirmation_enabled,
        "speech cross-check": settings.high_integrity_transcription_enabled,
        "live label scan" if local_vision else "timed label capture": (
            settings.live_label_scan_enabled if local_vision else settings.hands_free_profile
        ),
        "timer alarms": settings.timer_alarms_enabled,
        "model warm-up": settings.warm_models_on_startup,
    }
    if not settings.hands_free_profile and not settings.hands_free_listening_enabled:
        return {
            "profile": False,
            "ready": False,
            "summary": "Hands-free: off (push-to-talk and the screen are available)",
            "missing": [],
        }
    missing = [f"{name} is off" for name, enabled in features.items() if not enabled]
    missing.extend(missing_models(settings, adapters))
    summary = (
        "Hands-free: ready"
        if not missing
        else "Hands-free: not ready - " + "; ".join(missing)
    )
    return {
        "profile": settings.hands_free_profile,
        "ready": not missing,
        "summary": summary,
        "missing": missing,
    }
