"""In-app configuration: presets, validation, persistence, active vs saved.

The Settings page saves a small set of *choices* (which provider and model runs
each stage, a few interaction switches, and whether hosted processing is
allowed) to ``<data dir>/settings.json``. They are applied only when LabJarvis
starts, through the ordinary :class:`~labjarvis.settings.Settings` validators,
so a saved choice is accepted or refused by exactly the rules the application
already relies on (ADR 0019). Nothing here reloads a model in a running
process, and nothing here reads, stores or returns a credential value.

Precedence, highest first: process environment variables, this saved file,
the ``.env`` file, built-in defaults. An environment variable exported in the
shell that starts LabJarvis therefore still wins for development and testing;
the Settings page reports every saved choice that such a variable overrides.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from labjarvis.settings import (
    LOOPBACK_HOSTS,
    STAGES,
    HostedProvider,
    ReasoningEffort,
    Settings,
    Stage,
    StageProvider,
    settings_with_saved_values,
)

SETTINGS_FILE_NAME = "settings.json"
#: Development and demo launches set this so a saved choice cannot turn the
#: deterministic fixture profile into real models.
IGNORE_SAVED_SETTINGS_ENV = "LABJARVIS_IGNORE_SAVED_SETTINGS"
#: The test suite switches this off so a developer's saved file never leaks in.
READ_SAVED_CONFIGURATION = True

Provider = Literal["local", "openai", "deepseek", "openai_compatible"]
ProfileName = Literal["local", "hosted", "hybrid", "custom"]
HOSTED_FAMILY = frozenset({"openai", "deepseek", "openai_compatible"})
#: Providers whose requests accept a reasoning-effort setting.
REASONING_PROVIDERS = frozenset({"openai", "openai_compatible"})
STAGE_LABELS: dict[Stage, str] = {"speech": "Speech", "text": "Text", "vision": "Vision"}
PROFILE_LABELS = {
    "local": "Local",
    "hosted": "Hosted",
    "hybrid": "Hybrid",
    "custom": "Custom",
    "development": "Development fixtures",
}
#: Model names are identifiers, not paths: no traversal, no drive letters.
_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
#: The hands-free profile's default cross-check model (ADR 0018).
HANDS_FREE_DEFAULT_VERIFIER = Settings.HANDS_FREE_DEFAULT_VERIFIER


class SavedConfigurationError(RuntimeError):
    """The saved configuration cannot be read or is invalid in this environment."""


def _check_model_name(value: str) -> str:
    if ".." in value or ":/" in value or "\\" in value:
        raise ValueError("a model name is an identifier, not a file path")
    return value


class StageChoice(BaseModel):
    """Which provider and model run one stage, and on what hardware."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Provider
    model: str = Field(min_length=1, max_length=200, pattern=_MODEL_PATTERN)
    #: CPU or GPU for a model run on this computer (speech and vision only).
    device: Literal["cpu", "gpu"] = "cpu"
    #: OpenAI-style hosted text models only; ``None`` sends the provider default.
    reasoning_effort: ReasoningEffort | None = None

    _model_name = field_validator("model")(_check_model_name)


class ModelConfiguration(BaseModel):
    """Everything the Settings page can save. No credentials, URLs or paths."""

    model_config = ConfigDict(extra="forbid")

    profile: ProfileName
    speech: StageChoice
    text: StageChoice
    vision: StageChoice
    allow_hosted_processing: bool = False
    hands_free: bool = False
    high_integrity_transcription: bool = False
    high_integrity_model: str = Field(
        default="", max_length=200, pattern=r"^([A-Za-z0-9][A-Za-z0-9._:/@+-]*)?$"
    )
    #: Kokoro (local neural voice) is the recommended voice output; the
    #: browser voice remains the fallback and an explicit choice.
    voice_output: Literal["kokoro", "browser"] = "kokoro"
    voice: str = Field(default="af_heart", pattern=r"^[a-z]{2}_[a-z]+$")

    _model_name = field_validator("high_integrity_model")(_check_model_name)

    def stage(self, stage: Stage) -> StageChoice:
        choice: StageChoice = getattr(self, stage)
        return choice


# --------------------------------------------------------------------- presets
#
# Presets are ordinary stage choices, the same schema Custom edits. Each one is
# a configuration LabJarvis already documents: the local default, the fully
# hosted OpenAI profile verified on 2026-09-27, and local speech and label OCR
# beside a hosted text model (docs/evaluation/model-selection-record.md).

_LOCAL_SPEECH = StageChoice(provider="local", model="small.en")
_LOCAL_TEXT = StageChoice(provider="local", model="qwen2.5:3b")
_LOCAL_VISION = StageChoice(provider="local", model="english_g2")
_HOSTED_TEXT = StageChoice(
    provider="openai", model="gpt-5.4-nano", reasoning_effort=ReasoningEffort.NONE
)

PRESETS: dict[str, dict[Stage, StageChoice]] = {
    "local": {"speech": _LOCAL_SPEECH, "text": _LOCAL_TEXT, "vision": _LOCAL_VISION},
    "hosted": {
        "speech": StageChoice(provider="openai", model="gpt-4o-mini-transcribe"),
        "text": _HOSTED_TEXT,
        "vision": StageChoice(provider="openai", model="gpt-5.4-nano"),
    },
    "hybrid": {"speech": _LOCAL_SPEECH, "text": _HOSTED_TEXT, "vision": _LOCAL_VISION},
}
PRESET_DESCRIPTIONS = {
    "local": "Everything runs on this computer. Nothing is sent anywhere.",
    "hosted": "Speech, text and label images are processed by OpenAI.",
    "hybrid": "Speech and label reading on this computer; command text is processed by OpenAI.",
    "custom": "Choose the provider and model for each stage yourself.",
}
MODEL_SUGGESTIONS: dict[Stage, dict[str, list[str]]] = {
    "speech": {
        "local": ["small.en", "base.en", "tiny.en", "medium.en"],
        "openai": ["gpt-4o-mini-transcribe", "gpt-4o-transcribe"],
    },
    "text": {
        "local": ["qwen2.5:3b", "qwen2.5:1.5b", "qwen3:4b"],
        "openai": ["gpt-5.4-nano", "gpt-4.1-nano"],
        "deepseek": ["deepseek-flash"],
    },
    "vision": {"local": ["english_g2"], "openai": ["gpt-5.4-nano"]},
}


# ------------------------------------------------------------------ persistence


def settings_file(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE_NAME


def read_saved_configuration(data_dir: Path) -> ModelConfiguration | None:
    """The saved choices, or ``None`` when nothing has been saved."""

    path = settings_file(data_dir)
    if not path.is_file():
        return None
    try:
        return ModelConfiguration.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise SavedConfigurationError(
            f"the saved configuration in {path} could not be read; delete or rename it "
            "to start from the environment configuration"
        ) from exc


def write_saved_configuration(data_dir: Path, configuration: ModelConfiguration) -> Path:
    """Persist atomically: a crash mid-write leaves the previous file intact."""

    path = settings_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(configuration.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def saved_settings_ignored(environ: Mapping[str, str] | None = None) -> bool:
    value = (environ if environ is not None else os.environ).get(IGNORE_SAVED_SETTINGS_ENV, "")
    return value.strip().casefold() in {"1", "true", "yes"}


# ------------------------------------------------------- choices <-> settings


def to_settings_values(configuration: ModelConfiguration) -> dict[str, object]:
    """Translate saved choices into ``Settings`` fields (the saved source layer)."""

    providers = [configuration.stage(stage).provider for stage in STAGES]
    values: dict[str, object] = {
        # Explicit per-stage providers make every combination unambiguous;
        # model_mode only names the overall profile for display and backups.
        "model_mode": "hosted" if all(p in HOSTED_FAMILY for p in providers) else "local",
        "allow_hosted_models": configuration.allow_hosted_processing,
        "hands_free_profile": configuration.hands_free,
        "speech_output": configuration.voice_output,
        "kokoro_voice": configuration.voice,
    }
    for stage in STAGES:
        choice = configuration.stage(stage)
        values[f"{stage}_provider"] = choice.provider
        if choice.provider in HOSTED_FAMILY:
            values[f"hosted_{stage}_model_name"] = choice.model
            if stage == "text":
                values["hosted_text_reasoning_effort"] = choice.reasoning_effort
            continue
        values[f"{stage}_model_name"] = choice.model
        if stage == "speech":
            gpu = choice.device == "gpu"
            # The verified GPU profile (docs/models/gpu-setup.md).
            values["speech_device"] = "cuda" if gpu else "cpu"
            values["speech_compute_type"] = "float16" if gpu else "int8"
        elif stage == "vision":
            values["vision_gpu"] = choice.device == "gpu"
    if configuration.hands_free:
        # The profile switches the cross-check on itself; saying "off" here
        # would be refused, so only a chosen model name is passed through.
        if configuration.high_integrity_model:
            values["high_integrity_transcription_model_name"] = configuration.high_integrity_model
    else:
        values["high_integrity_transcription_enabled"] = configuration.high_integrity_transcription
        values["high_integrity_transcription_model_name"] = configuration.high_integrity_model
    return values


def project(settings: Settings) -> dict[str, Any]:
    """The choices a running (or about-to-run) configuration amounts to.

    Active and next-start configurations are both projected through this one
    function, so "restart required" compares like with like.
    """

    local_models = {
        "speech": settings.speech_model_name,
        "text": settings.text_model_name,
        "vision": settings.vision_model_name,
    }
    projection: dict[str, Any] = {}
    for stage in STAGES:
        provider = settings.stage_provider(stage).value
        hosted = provider in HOSTED_FAMILY
        device = "cpu"
        if provider == "local" and stage == "speech":
            device = "gpu" if settings.speech_device.casefold().startswith("cuda") else "cpu"
        elif provider == "local" and stage == "vision":
            device = "gpu" if settings.vision_gpu else "cpu"
        effort = (
            settings.hosted_text_reasoning_effort
            if stage == "text" and provider in REASONING_PROVIDERS
            else None
        )
        projection[stage] = {
            "provider": provider,
            "model": (
                "deterministic fixture"
                if provider == "fake"
                else settings.hosted_model_for(stage)
                if hosted
                else local_models[stage]
            ),
            "device": device,
            "reasoning_effort": effort.value if effort is not None else None,
        }
    projection["allow_hosted_processing"] = settings.allow_hosted_models
    projection["hands_free"] = settings.hands_free_profile
    projection["high_integrity_transcription"] = settings.high_integrity_transcription_enabled
    projection["high_integrity_model"] = (
        settings.high_integrity_transcription_model_name
        if settings.high_integrity_transcription_enabled
        else ""
    )
    projection["voice_output"] = settings.speech_output
    projection["voice"] = settings.kokoro_voice
    return projection


def normalise(configuration: ModelConfiguration) -> dict[str, Any]:
    """Saved choices in projection form (what they will mean once applied)."""

    projection: dict[str, Any] = {}
    for stage in STAGES:
        choice = configuration.stage(stage)
        hosted = choice.provider in HOSTED_FAMILY
        projection[stage] = {
            "provider": choice.provider,
            "model": choice.model,
            "device": "cpu" if hosted or stage == "text" else choice.device,
            "reasoning_effort": _effort_of(choice, stage),
        }
    projection["allow_hosted_processing"] = configuration.allow_hosted_processing
    projection["hands_free"] = configuration.hands_free
    high_integrity = configuration.hands_free or configuration.high_integrity_transcription
    projection["high_integrity_transcription"] = high_integrity
    projection["high_integrity_model"] = (
        (configuration.high_integrity_model or HANDS_FREE_DEFAULT_VERIFIER)
        if configuration.hands_free
        else configuration.high_integrity_model if high_integrity else ""
    )
    projection["voice_output"] = configuration.voice_output
    projection["voice"] = configuration.voice
    return projection


def _effort_of(choice: StageChoice, stage: Stage) -> str | None:
    if stage != "text" or choice.provider not in REASONING_PROVIDERS:
        return None
    return choice.reasoning_effort.value if choice.reasoning_effort is not None else None


def profile_of(projection: Mapping[str, Any]) -> str:
    """The preset a projection matches, ``custom``, or ``development`` (fake)."""

    if any(projection[stage]["provider"] == "fake" for stage in STAGES):
        return "development"
    for name, preset in PRESETS.items():
        if all(projection[stage] == _stage_projection(preset[stage], stage) for stage in STAGES):
            return name
    return "custom"


def _stage_projection(choice: StageChoice, stage: Stage) -> dict[str, Any]:
    hosted = choice.provider in HOSTED_FAMILY
    return {
        "provider": choice.provider,
        "model": choice.model,
        "device": "cpu" if hosted or stage == "text" else choice.device,
        "reasoning_effort": _effort_of(choice, stage),
    }


# ------------------------------------------------------------------ validation


def _present(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())


def compatible_endpoint(settings: Settings, stage: Stage) -> dict[str, str] | None:
    """The administrator-configured OpenAI-compatible endpoint for ``stage``.

    Endpoints come from the environment only; the browser can choose to use
    one but can never supply or see a URL. Only a display label and whether
    the endpoint is on this computer are exposed.
    """

    url: str | None = getattr(settings, f"hosted_{stage}_base_url")
    if url is None and settings.hosted_provider is HostedProvider.OPENAI_COMPATIBLE:
        url = settings.hosted_base_url
    if url is None:
        return None
    host = urlsplit(url).hostname or ""
    if host == "api.openai.com":
        return None
    label = str(getattr(settings, f"hosted_{stage}_provider_label")).strip() or host
    return {"label": label, "locality": "local" if host in LOOPBACK_HOSTS else "hosted"}


def _provider_label(settings: Settings, stage: Stage, provider: str) -> str:
    if provider == "openai":
        return "OpenAI"
    if provider == "deepseek":
        return "DeepSeek"
    if provider == "openai_compatible":
        endpoint = compatible_endpoint(settings, stage)
        return endpoint["label"] if endpoint else "an OpenAI-compatible provider"
    return "this computer"


def _connection(settings: Settings, stage: Stage, provider: str) -> tuple[str, SecretStr | None]:
    """Where a *proposed* provider would connect, not the running one."""

    return settings.connection_for(stage, StageProvider(provider))


def _choice_is_remote(settings: Settings, stage: Stage, provider: str) -> bool:
    if provider == "local":
        return False
    host = urlsplit(_connection(settings, stage, provider)[0]).hostname
    return host not in LOOPBACK_HOSTS


def explain_problems(configuration: ModelConfiguration, environment: Settings) -> list[str]:
    """Plain-language problems with a proposal, checked before ``Settings``.

    These restate the startup rules for the combinations people actually hit,
    naming the choice on the page rather than an environment variable. They
    never correct anything: every problem is reported and nothing is saved.
    ``environment`` supplies what only the environment configures (keys and
    endpoints).
    """

    problems: list[str] = []
    needs_permission: list[tuple[str, str, str | None]] = []
    needs_key: list[tuple[str, str]] = []
    if configuration.profile != "custom":
        preset = PRESETS[configuration.profile]
        if any(configuration.stage(stage) != preset[stage] for stage in STAGES):
            problems.append(
                f"The {PROFILE_LABELS[configuration.profile]} profile uses fixed models. "
                "Choose Custom to change an individual stage."
            )
    for stage in STAGES:
        choice = configuration.stage(stage)
        name = STAGE_LABELS[stage]
        if choice.reasoning_effort is not None and (
            stage != "text" or choice.provider not in REASONING_PROVIDERS
        ):
            problems.append(
                f"{name}: reasoning effort applies only to an OpenAI or OpenAI-compatible "
                "text model."
            )
        if choice.provider == "deepseek" and stage != "text":
            problems.append(f"{name}: DeepSeek is available for the text stage only.")
            continue
        if choice.device == "gpu" and (stage == "text" or choice.provider in HOSTED_FAMILY):
            problems.append(
                f"{name}: the CPU/GPU choice applies only to speech or vision models run on "
                "this computer."
            )
        if choice.provider == "local":
            continue
        if choice.provider == "openai_compatible":
            if compatible_endpoint(environment, stage) is None:
                problems.append(
                    f"{name}: no OpenAI-compatible endpoint is configured for this stage. "
                    "Endpoints are set by whoever administers LabJarvis, in its environment "
                    "configuration, not in the browser."
                )
                continue
            if stage == "speech" and not environment.hosted_speech_transcription:
                problems.append(
                    "Speech: the configured OpenAI-compatible endpoint has not been declared "
                    "to support transcription, so it cannot run the speech stage."
                )
            if stage == "vision" and not environment.hosted_vision_image_input:
                problems.append(
                    "Vision: the configured OpenAI-compatible endpoint has not been declared "
                    "to accept images, so it cannot run the vision stage."
                )
        label = _provider_label(environment, stage, choice.provider)
        if not configuration.allow_hosted_processing:
            sends = _DATA[stage] if _choice_is_remote(environment, stage, choice.provider) else None
            needs_permission.append((name, label, sends))
        if not _present(_connection(environment, stage, choice.provider)[1]):
            needs_key.append((name, label))
    if needs_permission:
        sent = [data for _, _, data in needs_permission if data is not None]
        problems.append(
            "Hosted processing is not allowed, but "
            + _join([f"{name} ({label})" for name, label, _ in needs_permission])
            + (
                " would run on a hosted provider"
                if len(needs_permission) == 1
                else " would run on hosted providers"
            )
            + (f", sending {_join(sent)} off this computer" if sent else "")
            + '. Turn on "Allow hosted processing", or choose "On this computer" '
            "for those stages."
        )
    if needs_key:
        problems.append(
            _join([f"{name} ({label})" for name, label in needs_key])
            + (" needs" if len(needs_key) == 1 else " need")
            + " an API key, but none is configured. API keys are set in the LabJarvis "
            "environment (the .env file or the shell that starts it), never in the browser."
        )
    speech_hosted = configuration.speech.provider in HOSTED_FAMILY
    if configuration.hands_free:
        if speech_hosted and not configuration.high_integrity_model:
            problems.append(
                "Hands-free mode checks every consequential utterance with a second "
                "transcription. With hosted speech, name the hosted model to use for that "
                "check (for example gpt-4o-transcribe)."
            )
    elif configuration.high_integrity_transcription and not configuration.high_integrity_model:
        problems.append(
            "High-integrity transcription needs a second speech model name "
            f"({'a hosted transcription model' if speech_hosted else 'for example base.en'})."
        )
    return problems


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


_DATA: dict[Stage, str] = {
    "speech": "recorded audio",
    "text": "what you say",
    "vision": "label photographs",
}


def _messages(error: ValidationError) -> list[str]:
    messages: list[str] = []
    for item in error.errors():
        message = str(item.get("msg", "invalid configuration"))
        messages.append(message.removeprefix("Value error, "))
    return messages


LocalModelCheck = Callable[[Settings], list[str]]


def missing_hands_free_models(settings: Settings) -> list[str]:
    """The hands-free startup preflight, run without starting the application."""

    if not settings.hands_free_profile:
        return []
    from labjarvis.infrastructure.hands_free import missing_models
    from labjarvis.infrastructure.model_factory import build_model_adapters

    adapters = build_model_adapters(settings)  # lazy: nothing is loaded here
    try:
        return missing_models(settings, adapters)
    finally:
        adapters.close()


def validate_configuration(
    configuration: ModelConfiguration,
    environment: Settings,
    *,
    local_model_check: LocalModelCheck = missing_hands_free_models,
) -> tuple[Settings | None, list[str]]:
    """Validate a proposal exactly as the next startup would, reporting every problem.

    Returns the settings the next start would build, or the problems that would
    stop it. The plain-language checks run first; if they pass, the real
    ``Settings`` validators (and the hands-free model preflight) decide.
    """

    problems = explain_problems(configuration, environment)
    if problems:
        return None, problems
    try:
        candidate = settings_with_saved_values(to_settings_values(configuration))
    except ValidationError as exc:
        return None, _messages(exc)
    missing = local_model_check(candidate)
    if missing:
        return None, [
            "Hands-free mode refuses to start without its models on this computer: "
            + "; ".join(missing)
            + ". Install them (python scripts/tasks.py models) or switch hands-free mode off."
        ]
    return candidate, []


# ----------------------------------------------------------- startup loading


class _DataDirectory(BaseSettings):
    """Resolve only the data directory, by the same environment rules as Settings."""

    model_config = SettingsConfigDict(env_prefix="LABJARVIS_", extra="ignore", case_sensitive=False)

    data_dir: Path = Settings.model_fields["data_dir"].default


def environment_data_dir() -> Path:
    return _DataDirectory(_env_file=Settings.model_config.get("env_file")).data_dir  # type: ignore[call-arg]


def current_environment(active: Settings) -> Settings:
    """Keys and endpoints as the environment and ``.env`` define them *now*.

    A key added to ``.env`` after startup must count when validating a choice
    that the next start will load, and credential status must show it. Falls
    back to the running settings if the environment alone does not validate.
    """

    try:
        return settings_with_saved_values(None)
    except ValidationError:
        return active


def next_start_settings(data_dir: Path) -> tuple[Settings | None, str | None]:
    """What the next normal startup would load, or why it would fail."""

    try:
        return load_application_settings(data_dir), None
    except SavedConfigurationError as exc:
        return None, str(exc)
    except ValidationError as exc:
        return None, "; ".join(_messages(exc))


def load_application_settings(data_dir: Path | None = None) -> Settings:
    """The startup path: environment, then the saved choices, then ``.env``.

    An invalid saved configuration stops startup with the reason rather than
    being ignored: silently starting on different models (or a different
    locality) than the operator chose is exactly what this must never do.
    """

    if not READ_SAVED_CONFIGURATION or saved_settings_ignored():
        return Settings()
    directory = data_dir if data_dir is not None else environment_data_dir()
    saved = read_saved_configuration(directory)
    if saved is None:
        return Settings()
    try:
        return settings_with_saved_values(to_settings_values(saved))
    except ValidationError as exc:
        raise SavedConfigurationError(
            "the configuration saved from the LabJarvis Settings page "
            f"({settings_file(directory)}) is not valid in the current environment: "
            + "; ".join(_messages(exc))
            + ". Fix the environment, or delete or rename that file to start without it "
            "and choose again in Settings."
        ) from exc


# --------------------------------------------------------------- page state

#: Settings fields each saved choice feeds, for naming environment overrides.
_FIELD_VARIABLES: dict[str, tuple[str, ...]] = {
    "speech.provider": ("speech_provider",),
    "speech.model": ("speech_model_name", "hosted_speech_model_name"),
    "speech.device": ("speech_device", "speech_compute_type"),
    "text.provider": ("text_provider",),
    "text.model": ("text_model_name", "hosted_text_model_name"),
    "text.reasoning_effort": ("hosted_text_reasoning_effort",),
    "vision.provider": ("vision_provider",),
    "vision.model": ("vision_model_name", "hosted_vision_model_name"),
    "vision.device": ("vision_gpu",),
    "allow_hosted_processing": ("allow_hosted_models",),
    "hands_free": ("hands_free_profile",),
    "high_integrity_transcription": ("high_integrity_transcription_enabled",),
    "high_integrity_model": ("high_integrity_transcription_model_name",),
    "voice_output": ("speech_output",),
    "voice": ("kokoro_voice",),
}


def _flatten(projection: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in projection.items():
        if isinstance(value, Mapping):
            for inner, inner_value in value.items():
                flat[f"{key}.{inner}"] = inner_value
        else:
            flat[key] = value
    return flat


def environment_overrides(
    saved: ModelConfiguration, next_start: Settings, environ: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    """Saved choices that will not apply because the environment sets them.

    Found by comparison, not guesswork: a choice is overridden exactly when the
    configuration the next start builds differs from what was saved.
    """

    present = {name.upper() for name in (environ if environ is not None else os.environ)}
    wanted = _flatten(normalise(saved))
    effective = _flatten(project(next_start))
    overrides: list[dict[str, object]] = []
    for field, value in wanted.items():
        if effective.get(field) == value:
            continue
        variables = [
            f"LABJARVIS_{name.upper()}"
            for name in _FIELD_VARIABLES.get(field, ())
            if f"LABJARVIS_{name.upper()}" in present
        ]
        overrides.append(
            {
                "field": field,
                "saved": value,
                "effective": effective.get(field),
                "variables": variables,
            }
        )
    return overrides


#: Variables that can supply each provider's key, in the order Settings uses
#: them. LABJARVIS_HOSTED_API_KEY is the older name for the OpenAI key.
KEY_VARIABLES: dict[str, tuple[str, ...]] = {
    "openai": ("LABJARVIS_OPENAI_API_KEY", "LABJARVIS_HOSTED_API_KEY"),
    "deepseek": ("LABJARVIS_DEEPSEEK_API_KEY",),
}


def _environment_names(environ: Mapping[str, str] | None = None) -> set[str]:
    source = os.environ if environ is None else environ
    return {name.upper() for name, value in source.items() if value.strip()}


def dotenv_names() -> set[str]:
    """Variables the ``.env`` file sets (names only; values are never read out)."""

    configured = Settings.model_config.get("env_file")
    if not configured:
        return set()
    path = Path(str(configured))
    if not path.is_file():
        return set()
    from dotenv import dotenv_values

    return {name.upper() for name, value in dotenv_values(path).items() if value}


def _source(
    names: tuple[str, ...], environment: set[str], dotenv: set[str]
) -> tuple[str | None, str | None]:
    """Where the value Settings uses comes from: the first name set anywhere wins
    (Settings prefers the first name), and for that name the process
    environment outranks ``.env``."""

    for name in names:
        if name in environment:
            return "environment", name
        if name in dotenv:
            return ".env", name
    return None, None


def credential_status(
    settings: Settings, environ: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    """Whether each key is configured and where it comes from - never the key."""

    environment, dotenv = _environment_names(environ), dotenv_names()
    rows: list[tuple[str, str, SecretStr | None, tuple[str, ...]]] = [
        ("openai", "OpenAI API key", settings.openai_key(), KEY_VARIABLES["openai"]),
        ("deepseek", "DeepSeek API key", settings.deepseek_api_key, KEY_VARIABLES["deepseek"]),
    ]
    for stage in STAGES:
        key: SecretStr | None = getattr(settings, f"hosted_{stage}_api_key")
        if key is not None:
            rows.append(
                (
                    stage,
                    f"{STAGE_LABELS[stage]}-stage API key",
                    key,
                    (f"LABJARVIS_HOSTED_{stage.upper()}_API_KEY",),
                )
            )
    items: list[dict[str, object]] = []
    for identifier, label, key, names in rows:
        source, variable = _source(names, environment, dotenv) if _present(key) else (None, None)
        items.append(
            {
                "id": identifier,
                "label": label,
                "configured": _present(key),
                "source": source,
                "variable": variable,
            }
        )
    return items


def configuration_warnings(
    settings: Settings,
    saved: ModelConfiguration | None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Anything that would make configuration behave differently than it looks."""

    environment, dotenv = _environment_names(environ), dotenv_names()
    warnings: list[str] = []
    for item in credential_status(settings, environ):
        if item["source"] == "environment":
            warnings.append(
                f"The {item['label']} comes from the {item['variable']} environment variable "
                "of the process that started LabJarvis, which takes precedence over .env. "
                "Editing .env will not change it, even after Restart; remove the variable and "
                "start LabJarvis from a new terminal."
            )
    openai_names = [name for name in KEY_VARIABLES["openai"] if name in environment | dotenv]
    if len(openai_names) > 1:
        warnings.append(
            "Both LABJARVIS_OPENAI_API_KEY and LABJARVIS_HOSTED_API_KEY are set. "
            "LABJARVIS_OPENAI_API_KEY is the one used for OpenAI; remove the other so there "
            "is only one OpenAI key to maintain."
        )
    if saved is not None and not saved_settings_ignored(environ):
        managed = {
            f"LABJARVIS_{name.upper()}" for names in _FIELD_VARIABLES.values() for name in names
        } | {"LABJARVIS_MODEL_MODE"}
        dead = sorted(managed & dotenv)
        if dead:
            warnings.append(
                "These .env lines have no effect because the Settings page manages them: "
                + ", ".join(dead)
                + ". Remove them from .env and choose in Settings instead."
            )
    return warnings


def provider_options(settings: Settings) -> dict[str, list[dict[str, object]]]:
    """Choices per stage, each with whether its key is configured (never the key)."""

    def option(stage: Stage, value: str, label: str, locality: str) -> dict[str, object]:
        configured = value == "local" or _present(_connection(settings, stage, value)[1])
        return {"value": value, "label": label, "locality": locality, "configured": configured}

    options: dict[str, list[dict[str, object]]] = {}
    for stage in STAGES:
        stage_options = [
            option(stage, "local", "On this computer", "local"),
            option(stage, "openai", "Hosted: OpenAI", "hosted"),
        ]
        if stage == "text":
            stage_options.append(option(stage, "deepseek", "Hosted: DeepSeek", "hosted"))
        endpoint = compatible_endpoint(settings, stage)
        if endpoint is not None:
            prefix = "Hosted" if endpoint["locality"] == "hosted" else "Local server"
            stage_options.append(
                option(
                    stage,
                    "openai_compatible",
                    f"{prefix}: {endpoint['label']} (OpenAI-compatible)",
                    endpoint["locality"],
                )
            )
        options[stage] = stage_options
    return options


def presets_payload() -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in ("local", "hosted", "hybrid", "custom"):
        entry: dict[str, object] = {
            "label": PROFILE_LABELS[name],
            "description": PRESET_DESCRIPTIONS[name],
        }
        for stage, choice in PRESETS.get(name, {}).items():
            entry[stage] = choice.model_dump(mode="json")
        payload[name] = entry
    return payload


def configuration_state(
    active: Settings,
    *,
    restart: Mapping[str, object],
    voice_output: Mapping[str, object] | None = None,
    active_sessions: int,
    instance_id: str,
) -> dict[str, object]:
    """Everything the Settings page renders: active, saved, and what a restart does."""

    active_projection = project(active)
    environment = current_environment(active)
    saved: ModelConfiguration | None = None
    saved_problem: str | None = None
    try:
        saved = read_saved_configuration(active.data_dir)
    except SavedConfigurationError as exc:
        saved_problem = str(exc)
    ignored = saved_settings_ignored()
    next_settings, next_problem = (
        (None, saved_problem) if saved_problem is not None else next_start_settings(active.data_dir)
    )
    next_projection = project(next_settings) if next_settings is not None else None
    restart_required = next_projection is not None and next_projection != active_projection
    active_profile = profile_of(active_projection)
    chosen = saved is not None and not ignored and not restart_required
    if chosen and saved is not None and active_profile != "development":
        # Show the profile the operator chose, e.g. Custom that happens to
        # equal a preset's models.
        active_profile = saved.profile
    return {
        "instance_id": instance_id,
        "active": {**active_projection, "profile": active_profile},
        "saved": saved.model_dump(mode="json") if saved is not None else None,
        "saved_ignored": ignored,
        "next_start": next_projection,
        "next_start_problem": next_problem,
        "restart_required": restart_required,
        "overrides": (
            environment_overrides(saved, next_settings)
            if saved is not None and next_settings is not None and not ignored
            else []
        ),
        "restart": dict(restart),
        "active_sessions": active_sessions,
        "credentials": credential_status(environment),
        "warnings": configuration_warnings(environment, saved),
        "options": {
            "providers": provider_options(environment),
            "model_suggestions": MODEL_SUGGESTIONS,
            "reasoning_efforts": [effort.value for effort in ReasoningEffort],
        },
        #: Kokoro's installation status and installed voices (no secrets).
        "voice_output": dict(voice_output or {}),
        "presets": presets_payload(),
        "settings_file": str(settings_file(active.data_dir)),
    }
