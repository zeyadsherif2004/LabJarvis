from pathlib import Path

from pydantic import SecretStr

from labjarvis.api.app import create_app
from labjarvis.infrastructure.cascade import CascadeTextUnderstanding
from labjarvis.infrastructure.document_ocr import (
    RenderedDocumentOcr,
    UnavailableDocumentOcr,
)
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.hosted_models import (
    OpenAILabelRecognition,
    OpenAISpeechToText,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.inference import BoundedInferenceRunner, DirectInferenceRunner
from labjarvis.infrastructure.model_factory import build_model_adapters
from labjarvis.infrastructure.real_models import (
    EasyOcrLabelRecognition,
    FasterWhisperSpeechToText,
    OllamaTextUnderstanding,
)
from labjarvis.settings import ModelMode, Settings

PLACEHOLDER_KEY = "unit-test-placeholder-not-a-real-key"


def hosted_settings(tmp_path: Path) -> Settings:
    return Settings(
        model_mode=ModelMode.HOSTED,
        allow_hosted_models=True,
        hosted_api_key=SecretStr(PLACEHOLDER_KEY),
        hosted_speech_model_name="gpt-4o-transcribe",
        hosted_text_model_name="gpt-5.6-terra",
        hosted_vision_model_name="gpt-5.6-terra",
        data_dir=tmp_path,
        model_cache_dir=tmp_path / "models",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'hosted.db'}",
    )


def test_fake_mode_builds_only_deterministic_adapters(tmp_path: Path) -> None:
    adapters = build_model_adapters(Settings(data_dir=tmp_path))

    assert isinstance(adapters.speech, FakeSpeechToText)
    assert isinstance(adapters.text, FakeTextUnderstanding)
    assert isinstance(adapters.labels, FakeLabelRecognition)
    assert isinstance(adapters.documents, UnavailableDocumentOcr)


def test_local_mode_builds_three_real_pretrained_model_adapters(tmp_path: Path) -> None:
    settings = Settings(
        model_mode=ModelMode.LOCAL,
        data_dir=tmp_path,
        model_cache_dir=tmp_path / "models",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local.db'}",
    )
    adapters = build_model_adapters(settings)

    assert isinstance(adapters.speech, FasterWhisperSpeechToText)
    # The default local interpreter is the deterministic-first cascade around
    # the local language model, not the bare model.
    assert isinstance(adapters.text, CascadeTextUnderstanding)
    assert isinstance(adapters.text.language_model, OllamaTextUnderstanding)
    assert isinstance(adapters.labels, EasyOcrLabelRecognition)
    assert isinstance(adapters.documents, RenderedDocumentOcr)

    app = create_app(settings, create_schema=True)
    assert isinstance(app.state.model_adapters.speech, FasterWhisperSpeechToText)
    assert isinstance(app.state.model_adapters.text, CascadeTextUnderstanding)
    assert isinstance(app.state.model_adapters.labels, EasyOcrLabelRecognition)
    assert isinstance(app.state.model_adapters.documents, RenderedDocumentOcr)


def test_hosted_mode_builds_three_hosted_adapters(tmp_path: Path) -> None:
    adapters = build_model_adapters(hosted_settings(tmp_path))
    try:
        assert isinstance(adapters.speech, OpenAISpeechToText)
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        assert isinstance(adapters.text.language_model, OpenAITextUnderstanding)
        assert isinstance(adapters.labels, OpenAILabelRecognition)
        assert isinstance(adapters.documents, UnavailableDocumentOcr)
    finally:
        adapters.close()


def test_hosted_adapters_use_the_configured_model_identifiers(tmp_path: Path) -> None:
    adapters = build_model_adapters(hosted_settings(tmp_path))
    try:
        assert adapters.speech.model_name == "gpt-4o-transcribe"
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        assert adapters.text.language_model.model_name == "gpt-5.6-terra"
        assert adapters.labels.model_name == "gpt-5.6-terra"
    finally:
        adapters.close()


def test_hosted_adapters_share_one_pooled_client(tmp_path: Path) -> None:
    adapters = build_model_adapters(hosted_settings(tmp_path))
    try:
        speech_client = adapters.speech._client  # type: ignore[attr-defined]
        assert isinstance(adapters.text, CascadeTextUnderstanding)
        text_client = adapters.text.language_model._client  # type: ignore[attr-defined]
        label_client = adapters.labels._client  # type: ignore[attr-defined]

        assert speech_client is text_client is label_client
    finally:
        adapters.close()


def test_hosted_close_releases_the_shared_client_exactly_once(tmp_path: Path) -> None:
    adapters = build_model_adapters(hosted_settings(tmp_path))
    client = adapters.speech._client  # type: ignore[attr-defined]
    closes = 0
    original = client._client.close

    def counting_close() -> None:
        nonlocal closes
        closes += 1
        original()

    client._client.close = counting_close

    adapters.close()
    adapters.close()

    assert closes == 1
    assert client.is_closed is True


def test_every_mode_closes_without_error(tmp_path: Path) -> None:
    fake = Settings(data_dir=tmp_path)
    local = Settings(
        model_mode=ModelMode.LOCAL,
        data_dir=tmp_path,
        model_cache_dir=tmp_path / "models",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local.db'}",
    )

    for settings in (fake, local, hosted_settings(tmp_path)):
        build_model_adapters(settings).close()


def test_hosted_mode_uses_the_bounded_inference_runner(tmp_path: Path) -> None:
    app = create_app(hosted_settings(tmp_path), create_schema=True)

    assert isinstance(app.state.inference_runner, BoundedInferenceRunner)


def test_fake_mode_uses_the_direct_inference_runner(tmp_path: Path) -> None:
    app = create_app(
        Settings(data_dir=tmp_path, database_url=f"sqlite+pysqlite:///{tmp_path / 'fake.db'}"),
        create_schema=True,
    )

    assert isinstance(app.state.inference_runner, DirectInferenceRunner)
