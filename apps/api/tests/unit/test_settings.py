from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from labjarvis.settings import HostedProvider, ModelMode, Settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"


def hosted_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Build a minimally valid hosted configuration for validation tests."""

    base: dict[str, object] = {
        "model_mode": ModelMode.HOSTED,
        "allow_hosted_models": True,
        "hosted_api_key": SecretStr(PLACEHOLDER_KEY),
        "hosted_speech_model_name": "gpt-4o-transcribe",
        "hosted_text_model_name": "gpt-5.6-terra",
        "hosted_vision_model_name": "gpt-5.6-terra",
        "data_dir": tmp_path,
    }
    base.update(overrides)
    return Settings(**base)


def test_secure_fake_defaults(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)

    assert settings.model_mode is ModelMode.FAKE
    assert settings.host == "127.0.0.1"
    assert settings.retain_raw_media is False
    assert settings.retain_transcripts is True
    assert "*" not in settings.allowed_hosts
    assert settings.reagent_match_threshold > settings.reagent_uncertain_threshold


def test_threshold_order_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="match threshold must exceed"):
        Settings(
            data_dir=tmp_path,
            reagent_match_threshold=70,
            reagent_uncertain_threshold=80,
        )


def test_remote_model_service_requires_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="remote model services"):
        Settings(data_dir=tmp_path, ollama_base_url="https://models.example")

    configured = Settings(
        data_dir=tmp_path,
        ollama_base_url="https://models.example",
        allow_remote_model_service=True,
    )
    assert configured.allow_remote_model_service is True


def test_allowed_hosts_must_be_explicit(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="allowed hosts"):
        Settings(data_dir=tmp_path, allowed_hosts=("*",))


def test_fake_and_local_modes_need_no_hosted_configuration(tmp_path: Path) -> None:
    for mode in (ModelMode.FAKE, ModelMode.LOCAL):
        settings = Settings(model_mode=mode, data_dir=tmp_path)

        assert settings.hosted_api_key is None
        assert settings.allow_hosted_models is False


def test_hosted_mode_requires_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="explicit hosted processing"):
        hosted_settings(tmp_path, allow_hosted_models=False)


def test_hosted_mode_requires_a_non_blank_api_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="hosted API key"):
        hosted_settings(tmp_path, hosted_api_key=None)

    with pytest.raises(ValidationError, match="hosted API key"):
        hosted_settings(tmp_path, hosted_api_key=SecretStr("   "))


@pytest.mark.parametrize(
    "field",
    ["hosted_speech_model_name", "hosted_text_model_name", "hosted_vision_model_name"],
)
def test_hosted_mode_requires_every_model_identifier(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValidationError, match="model identifier"):
        hosted_settings(tmp_path, **{field: "   "})


def test_hosted_mode_accepts_a_complete_configuration(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)

    assert settings.model_mode is ModelMode.HOSTED
    assert settings.hosted_provider is HostedProvider.OPENAI
    assert settings.hosted_base_url == "https://api.openai.com/v1"


def test_hosted_base_url_must_be_absolute_and_credential_free(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="absolute HTTP"):
        hosted_settings(tmp_path, hosted_base_url="/v1")

    with pytest.raises(ValidationError, match="must not contain credentials"):
        hosted_settings(tmp_path, hosted_base_url="https://user:secret@api.openai.com/v1")


def test_hosted_base_url_requires_https_outside_loopback(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        hosted_settings(tmp_path, hosted_base_url="http://api.openai.com/v1")


def test_hosted_base_url_permits_loopback_http_for_tests(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path, hosted_base_url="http://127.0.0.1:9099/v1")

    assert settings.hosted_base_url == "http://127.0.0.1:9099/v1"


def test_hosted_retry_and_response_limits_are_bounded(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        hosted_settings(tmp_path, hosted_max_retries=9)

    with pytest.raises(ValidationError):
        hosted_settings(tmp_path, hosted_max_retries=-1)

    with pytest.raises(ValidationError):
        hosted_settings(tmp_path, hosted_max_response_bytes=16)


def test_hosted_api_key_never_appears_in_representations(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)

    assert PLACEHOLDER_KEY not in repr(settings)
    assert PLACEHOLDER_KEY not in str(settings)
    assert PLACEHOLDER_KEY not in str(settings.model_dump())


def test_hosted_validation_errors_never_echo_the_api_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        hosted_settings(tmp_path, hosted_text_model_name="  ")

    assert PLACEHOLDER_KEY not in str(caught.value)


def test_the_documented_env_example_is_a_valid_configuration(tmp_path: Path) -> None:
    example = Path(__file__).parents[4] / ".env.example"
    settings = Settings(_env_file=example, data_dir=tmp_path)  # type: ignore[call-arg]

    assert settings.model_mode is ModelMode.FAKE
    assert settings.text_provider is None
    assert settings.execution_profile() == "fake"


def test_blank_optional_settings_mean_unset(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, text_provider="", hosted_text_base_url="  ")

    assert settings.text_provider is None
    assert settings.hosted_text_base_url is None


def test_hands_free_hosted_vision_uses_one_shot_capture_instead_of_live_scan(
    tmp_path: Path,
) -> None:
    options = {
        "model_mode": ModelMode.LOCAL,
        "allow_hosted_models": True,
        "openai_api_key": SecretStr(PLACEHOLDER_KEY),
        "vision_provider": "openai",
        "hosted_vision_model_name": "gpt-5.4-nano",
        "hands_free_profile": True,
        "data_dir": tmp_path,
    }
    settings = Settings(_env_file=None, **options)  # type: ignore[call-arg]

    assert settings.hands_free_profile is True
    assert settings.hands_free_listening_enabled is True
    assert settings.voice_only_confirmation_enabled is True
    assert settings.live_label_scan_enabled is False

    with pytest.raises(ValidationError, match="local OCR"):
        Settings(_env_file=None, **(options | {"live_label_scan_enabled": True}))  # type: ignore[call-arg]
