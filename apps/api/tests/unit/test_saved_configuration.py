"""In-app saved configuration: presets, validation, persistence and precedence.

Validation must refuse (never quietly correct) a proposal, and the startup path
must apply saved choices through the ordinary ``Settings`` validators.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from labjarvis import saved_configuration as saved
from labjarvis.saved_configuration import (
    PRESETS,
    ModelConfiguration,
    SavedConfigurationError,
    StageChoice,
    environment_overrides,
    load_application_settings,
    next_start_settings,
    profile_of,
    project,
    read_saved_configuration,
    validate_configuration,
    write_saved_configuration,
)
from labjarvis.settings import Settings, StageProvider, get_settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"


def no_models(_: Settings) -> list[str]:
    return []


def preset(name: str, **overrides: object) -> ModelConfiguration:
    return ModelConfiguration(profile=name, **PRESETS[name], **overrides)  # type: ignore[arg-type]


def custom(**stages: StageChoice | bool | str) -> ModelConfiguration:
    values: dict[str, object] = {"profile": "custom", **PRESETS["local"]}
    values.update(stages)
    return ModelConfiguration.model_validate(values)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """The running environment: data in ``tmp_path``, no key, saved file honoured."""

    monkeypatch.setenv("LABJARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(saved, "READ_SAVED_CONFIGURATION", True)
    return Settings()


def with_key(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("LABJARVIS_HOSTED_API_KEY", PLACEHOLDER_KEY)
    return Settings()


# ------------------------------------------------------------------- presets


def test_local_preset_is_valid_without_hosted_permission_or_key(environment: Settings) -> None:
    candidate, problems = validate_configuration(
        preset("local"), environment, local_model_check=no_models
    )

    assert problems == []
    assert candidate is not None
    assert all(candidate.stage_locality(stage) == "local" for stage in ("speech", "text", "vision"))
    assert candidate.any_stage_remote is False
    assert profile_of(project(candidate)) == "local"


def test_hosted_preset_refused_while_hosted_processing_is_not_allowed(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)

    candidate, problems = validate_configuration(
        preset("hosted"), environment, local_model_check=no_models
    )

    assert candidate is None
    assert len(problems) == 1
    assert "Hosted processing is not allowed" in problems[0]
    assert "Speech (OpenAI), Text (OpenAI) and Vision (OpenAI)" in problems[0]


def test_hosted_preset_explains_missing_credentials(environment: Settings) -> None:
    _, problems = validate_configuration(
        preset("hosted", allow_hosted_processing=True), environment, local_model_check=no_models
    )

    assert len(problems) == 1
    assert "need an API key, but none is configured" in problems[0]
    assert "never in the browser" in problems[0]


def test_hosted_preset_with_permission_and_key(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)

    candidate, problems = validate_configuration(
        preset("hosted", allow_hosted_processing=True), environment, local_model_check=no_models
    )

    assert problems == []
    assert candidate is not None
    assert {candidate.stage_provider(s) for s in ("speech", "text", "vision")} == {
        StageProvider.OPENAI
    }
    assert candidate.hosted_text_model_name == "gpt-5.4-nano"
    assert candidate.hosted_text_reasoning_effort is not None
    assert profile_of(project(candidate)) == "hosted"


def test_hybrid_preset_keeps_speech_and_vision_local(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)

    candidate, problems = validate_configuration(
        preset("hybrid", allow_hosted_processing=True), environment, local_model_check=no_models
    )

    assert problems == []
    assert candidate is not None
    assert candidate.stage_locality("speech") == "local"
    assert candidate.stage_locality("text") == "hosted"
    assert candidate.stage_locality("vision") == "local"
    assert candidate.execution_profile() == "hybrid"
    assert profile_of(project(candidate)) == "hybrid"


def test_a_preset_with_edited_stages_is_refused_not_corrected(environment: Settings) -> None:
    edited = ModelConfiguration(
        profile="local",
        speech=StageChoice(provider="local", model="base.en"),
        text=PRESETS["local"]["text"],
        vision=PRESETS["local"]["vision"],
    )

    _, problems = validate_configuration(edited, environment, local_model_check=no_models)

    assert problems == [
        "The Local profile uses fixed models. Choose Custom to change an individual stage."
    ]


# -------------------------------------------------------------------- custom


def test_custom_gpu_choices_map_to_the_verified_gpu_profile(environment: Settings) -> None:
    configuration = custom(
        speech=StageChoice(provider="local", model="base.en", device="gpu"),
        vision=StageChoice(provider="local", model="english_g2", device="gpu"),
    )

    candidate, problems = validate_configuration(
        configuration, environment, local_model_check=no_models
    )

    assert problems == []
    assert candidate is not None
    assert (candidate.speech_device, candidate.speech_compute_type) == ("cuda", "float16")
    assert candidate.speech_model_name == "base.en"
    assert candidate.vision_gpu is True
    assert profile_of(project(candidate)) == "custom"


def test_custom_refuses_invalid_combinations(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)
    configuration = custom(
        text=StageChoice(provider="local", model="qwen2.5:3b", reasoning_effort="none"),
        vision=StageChoice(provider="openai", model="gpt-5.4-nano", device="gpu"),
        allow_hosted_processing=True,
    )

    _, problems = validate_configuration(configuration, environment, local_model_check=no_models)

    assert any(p.startswith("Text: reasoning effort applies only") for p in problems)
    assert any(problem.startswith("Vision: the CPU/GPU choice") for problem in problems)


def test_hands_free_hosted_vision_uses_timed_capture_and_still_needs_local_models(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)
    hosted_vision = custom(
        vision=StageChoice(provider="openai", model="gpt-5.4-nano"),
        allow_hosted_processing=True,
        hands_free=True,
    )
    candidate, problems = validate_configuration(
        hosted_vision, environment, local_model_check=no_models
    )
    assert problems == []
    assert candidate is not None
    assert candidate.hands_free_profile is True
    assert candidate.stage_locality("vision") == "hosted"
    assert candidate.live_label_scan_enabled is False

    _, problems = validate_configuration(
        custom(hands_free=True),
        environment,
        local_model_check=lambda _: ["speech model small.en: not cached"],
    )
    assert len(problems) == 1
    assert "refuses to start without its models" in problems[0]
    assert "small.en" in problems[0]


def test_openai_compatible_needs_an_environment_endpoint(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)
    configuration = custom(
        text=StageChoice(provider="openai_compatible", model="deepseek-flash"),
        allow_hosted_processing=True,
    )

    _, problems = validate_configuration(configuration, environment, local_model_check=no_models)
    assert any("no OpenAI-compatible endpoint is configured" in p for p in problems)

    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_PROVIDER_LABEL", "DeepSeek")
    environment = Settings()
    candidate, problems = validate_configuration(
        configuration, environment, local_model_check=no_models
    )
    assert problems == []
    assert candidate is not None
    assert candidate.stage_locality("text") == "hosted"
    options = saved.provider_options(environment)["text"]
    assert options[-1]["label"] == "Hosted: DeepSeek (OpenAI-compatible)"
    assert all("deepseek.com" not in option["label"] for option in options)


@pytest.mark.parametrize("model", ["../../etc/passwd", "C:/models/x", "a\\b", "rm -rf /", ""])
def test_model_names_are_identifiers_not_paths_or_commands(model: str) -> None:
    with pytest.raises(ValidationError):
        StageChoice(provider="local", model=model)


def test_configuration_rejects_unknown_fields_such_as_keys_or_urls() -> None:
    for extra in ({"hosted_api_key": "x"}, {"base_url": "https://evil.example"}):
        with pytest.raises(ValidationError):
            ModelConfiguration.model_validate(
                {
                    "profile": "local",
                    **{k: v.model_dump() for k, v in PRESETS["local"].items()},
                    **extra,
                }
            )


# ------------------------------------------------------ persistence and startup


def test_saved_configuration_round_trips_and_contains_no_secrets(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_key(monkeypatch)
    configuration = custom(
        text=StageChoice(provider="openai", model="gpt-5.4-nano"), allow_hosted_processing=True
    )

    path = write_saved_configuration(environment.data_dir, configuration)

    assert read_saved_configuration(environment.data_dir) == configuration
    assert PLACEHOLDER_KEY not in path.read_text(encoding="utf-8")
    assert not path.with_name(path.name + ".tmp").exists()


def test_startup_loads_the_saved_configuration(environment: Settings) -> None:
    write_saved_configuration(
        environment.data_dir,
        custom(speech=StageChoice(provider="local", model="base.en", device="gpu")),
    )
    get_settings.cache_clear()

    loaded = get_settings()

    assert loaded.stage_provider("speech") is StageProvider.LOCAL
    assert loaded.speech_model_name == "base.en"
    assert loaded.speech_device == "cuda"
    assert loaded.execution_profile() == "local"


def test_without_a_saved_file_startup_uses_the_environment(environment: Settings) -> None:
    assert load_application_settings().model_mode.value == "fake"


def test_process_environment_overrides_saved_choices_and_is_reported(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = custom()
    write_saved_configuration(environment.data_dir, configuration)
    monkeypatch.setenv("LABJARVIS_TEXT_MODEL_NAME", "qwen2.5:1.5b")

    next_settings, problem = next_start_settings(environment.data_dir)

    assert problem is None and next_settings is not None
    assert next_settings.text_model_name == "qwen2.5:1.5b"
    overrides = environment_overrides(configuration, next_settings)
    assert overrides == [
        {
            "field": "text.model",
            "saved": "qwen2.5:3b",
            "effective": "qwen2.5:1.5b",
            "variables": ["LABJARVIS_TEXT_MODEL_NAME"],
        }
    ]


def test_saved_choices_take_precedence_over_the_dotenv_file(
    environment: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("LABJARVIS_TEXT_MODEL_NAME=qwen3:4b\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", str(dotenv))
    write_saved_configuration(environment.data_dir, custom())

    assert load_application_settings().text_model_name == "qwen2.5:3b"
    assert Settings().text_model_name == "qwen3:4b"  # outside the startup path


def test_an_invalid_saved_configuration_stops_startup_with_the_reason(
    environment: Settings,
) -> None:
    # Valid when saved with a key; the key has since been removed.
    write_saved_configuration(environment.data_dir, preset("hosted", allow_hosted_processing=True))

    with pytest.raises(SavedConfigurationError, match="not valid in the current environment"):
        load_application_settings()


def test_an_unreadable_saved_file_is_reported_not_ignored(environment: Settings) -> None:
    (environment.data_dir / saved.SETTINGS_FILE_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(SavedConfigurationError, match="could not be read"):
        load_application_settings()


def test_demo_launch_can_ignore_the_saved_configuration(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_saved_configuration(environment.data_dir, custom())
    monkeypatch.setenv(saved.IGNORE_SAVED_SETTINGS_ENV, "true")

    assert load_application_settings().model_mode.value == "fake"


# ---------------------------------------------------------- active vs saved


def _state(active: Settings) -> dict[str, object]:
    return saved.configuration_state(
        active,
        restart={"available": False, "reason": "unsupervised"},
        active_sessions=0,
        instance_id="test",
    )


def test_restart_required_when_saved_differs_from_active(environment: Settings) -> None:
    write_saved_configuration(environment.data_dir, preset("local"))

    state = _state(environment)  # still running the fake fixtures

    assert state["restart_required"] is True
    assert state["active"]["profile"] == "development"  # type: ignore[index]
    assert state["saved"]["profile"] == "local"  # type: ignore[index]


def test_no_restart_required_when_saved_matches_active(environment: Settings) -> None:
    write_saved_configuration(environment.data_dir, custom())
    active = load_application_settings()  # what a restart would have started

    state = _state(active)

    assert state["restart_required"] is False
    assert state["active"]["profile"] == "custom"  # type: ignore[index]
    assert state["next_start"] == project(active)


def test_a_saved_configuration_invalid_now_is_shown_not_offered_for_restart(
    environment: Settings,
) -> None:
    write_saved_configuration(environment.data_dir, preset("hosted", allow_hosted_processing=True))

    state = _state(environment)

    assert state["restart_required"] is False
    assert "not valid in the current environment" in str(state["next_start_problem"])


def test_state_reports_credential_status_but_never_values(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_API_KEY", PLACEHOLDER_KEY + "-text")
    active = with_key(monkeypatch)

    state = _state(active)

    assert PLACEHOLDER_KEY not in json.dumps(state)
    credentials = {item["id"]: item["configured"] for item in state["credentials"]}  # type: ignore[union-attr, index]
    assert credentials == {"openai": True, "deepseek": False, "text": True}


# ------------------------------------------------- separate named providers


def test_deepseek_and_openai_keep_separate_endpoints_and_keys(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABJARVIS_HOSTED_API_KEY", PLACEHOLDER_KEY + "-openai")
    monkeypatch.setenv("LABJARVIS_DEEPSEEK_API_KEY", PLACEHOLDER_KEY + "-deepseek")
    # A leftover generic per-stage endpoint must not capture either provider.
    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_BASE_URL", "https://glm-like.example.test/v4")
    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_API_KEY", PLACEHOLDER_KEY + "-generic")
    environment = Settings()

    for provider, url, key in (
        (StageProvider.OPENAI, "https://api.openai.com/v1", "-openai"),
        (StageProvider.DEEPSEEK, "https://api.deepseek.com", "-deepseek"),
        (StageProvider.OPENAI_COMPATIBLE, "https://glm-like.example.test/v4", "-generic"),
    ):
        endpoint, secret = environment.connection_for("text", provider)
        assert endpoint == url
        assert secret is not None and secret.get_secret_value() == PLACEHOLDER_KEY + key


def test_deepseek_text_is_valid_and_carries_only_its_own_options(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABJARVIS_DEEPSEEK_API_KEY", PLACEHOLDER_KEY)
    monkeypatch.setenv("LABJARVIS_HOSTED_TEXT_REASONING_EFFORT", "none")
    environment = Settings()
    configuration = custom(
        text=StageChoice(provider="deepseek", model="deepseek-flash"),
        allow_hosted_processing=True,
    )

    candidate, problems = validate_configuration(
        configuration, environment, local_model_check=no_models
    )

    assert problems == []
    assert candidate is not None
    assert candidate.stage_locality("text") == "hosted"
    assert candidate.effective_text_structured_output().value == "json_object"
    assert project(candidate)["text"]["reasoning_effort"] is None
    from labjarvis.infrastructure.model_factory import build_model_adapters

    adapters = build_model_adapters(candidate)
    try:
        language_model = adapters.text.language_model  # type: ignore[attr-defined]
        assert language_model.model_name == "deepseek-flash"
    finally:
        adapters.close()


def test_deepseek_needs_its_own_key_and_is_text_only(
    environment: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = with_key(monkeypatch)  # an OpenAI key only
    _, problems = validate_configuration(
        custom(
            text=StageChoice(provider="deepseek", model="deepseek-flash"),
            allow_hosted_processing=True,
        ),
        environment,
        local_model_check=no_models,
    )
    assert problems == [
        "Text (DeepSeek) needs an API key, but none is configured. API keys are set in the "
        "LabJarvis environment (the .env file or the shell that starts it), never in the browser."
    ]

    _, problems = validate_configuration(
        custom(
            speech=StageChoice(provider="deepseek", model="deepseek-flash"),
            text=StageChoice(provider="deepseek", model="deepseek-flash", reasoning_effort="low"),
            allow_hosted_processing=True,
        ),
        environment,
        local_model_check=no_models,
    )
    assert "Speech: DeepSeek is available for the text stage only." in problems
    assert any(p.startswith("Text: reasoning effort applies only") for p in problems)
