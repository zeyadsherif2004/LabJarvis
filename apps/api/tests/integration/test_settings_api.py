"""Settings page API: save, active vs saved, and the restart request.

"Restarting" here means what the supervisor does after the API exits: build a
new application through the normal startup path against the same database.
The process-level loop itself is covered by the restart protocol unit tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from labjarvis import saved_configuration as saved
from labjarvis.api.app import create_app
from labjarvis.infrastructure import restart
from labjarvis.infrastructure.documents import DOCX_MIME
from labjarvis.infrastructure.restart import RestartController
from labjarvis.saved_configuration import PRESETS, load_application_settings
from labjarvis.settings import Settings, StageProvider

SECRET = "sk-integration-sentinel-never-returned"
THIS_COMPUTER = ("127.0.0.1", 50000)
SAMPLE_PROTOCOL = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)


def _import_and_start(client: TestClient) -> str:
    """Import, review, approve and start a protocol; returns the active session id."""

    imported = client.post(
        "/api/v1/protocols/upload",
        files={"file": ("development-only.docx", SAMPLE_PROTOCOL.read_bytes(), DOCX_MIME)},
    ).json()
    version_id = imported["version"]["id"]
    reviewed = client.put(
        f"/api/v1/protocol-versions/{version_id}",
        headers={"If-Match": '"0"'},
        json={"steps": imported["version"]["steps"]},
    ).json()
    created = client.post(
        f"/api/v1/protocol-versions/{version_id}/approve-and-start",
        headers={"If-Match": f'"{reviewed["revision"]}"'},
    )
    assert created.status_code == 201, created.text
    session = created.json()["session"]
    started = client.post(
        f"/api/v1/sessions/{session['id']}/start",
        headers={"If-Match": f'"{session["version"]}"'},
    )
    assert started.status_code == 200, started.text
    return str(session["id"])


def preset_body(name: str, **overrides: object) -> dict[str, object]:
    return {
        "profile": name,
        **{stage: choice.model_dump(mode="json") for stage, choice in PRESETS[name].items()},
        **overrides,
    }


def custom_body(**overrides: object) -> dict[str, object]:
    return {**preset_body("local"), "profile": "custom", **overrides}


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LABJARVIS_APP_ENV", "test")
    monkeypatch.setenv("LABJARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "LABJARVIS_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'settings.db').as_posix()}"
    )
    monkeypatch.setenv("LABJARVIS_HOSTED_API_KEY", SECRET)
    monkeypatch.setattr(saved, "READ_SAVED_CONFIGURATION", True)
    return tmp_path


class Supervised:
    """A stand-in for the launcher: records the request instead of exiting."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.shutdowns = 0
        directory.mkdir(parents=True, exist_ok=True)
        (directory / restart.SUPERVISOR_FILE).write_text(
            json.dumps({"token": "launch-token", "pid": 1}), encoding="utf-8"
        )
        self.controller = RestartController(
            {
                restart.SUPERVISOR_TOKEN_ENV: "launch-token",
                restart.SUPERVISOR_DIR_ENV: str(directory),
            },
            shutdown=self._shutdown,
        )

    def _shutdown(self) -> None:
        self.shutdowns += 1

    @property
    def requested(self) -> bool:
        return (self.directory / restart.RESTART_REQUEST_FILE).exists()


def start_labjarvis(*, supervised: Supervised | None = None) -> FastAPI:
    """The startup path the launcher runs, against an already-created schema."""

    app = create_app(load_application_settings(), create_schema=True)
    app.state.local_model_check = lambda _: []
    if supervised is not None:
        app.state.restart_controller = supervised.controller
    return app


@pytest.fixture
def client(environment: Path) -> Iterator[TestClient]:
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as test_client:
        yield test_client


def assert_no_secret(response_text: str) -> None:
    assert SECRET not in response_text
    assert "sk-" not in response_text


# ----------------------------------------------------------------- reading


def test_state_separates_active_and_saved_and_never_returns_secrets(client: TestClient) -> None:
    response = client.get("/api/v1/settings")

    assert response.status_code == 200
    assert_no_secret(response.text)
    body = response.json()
    assert body["active"]["profile"] == "development"
    assert body["saved"] is None
    assert body["restart_required"] is False
    assert body["credentials"] == [
        {
            "id": "openai",
            "label": "OpenAI API key",
            "configured": True,
            "source": "environment",
            "variable": "LABJARVIS_HOSTED_API_KEY",
        },
        {
            "id": "deepseek",
            "label": "DeepSeek API key",
            "configured": False,
            "source": None,
            "variable": None,
        },
    ]
    assert body["restart"] == {"available": False, "reason": restart.UNSUPERVISED_REASON}
    assert set(body["presets"]) == {"local", "hosted", "hybrid", "custom"}


# ------------------------------------------------------------------ saving


def test_saving_a_valid_custom_configuration_requires_a_restart(
    client: TestClient, environment: Path
) -> None:
    body = custom_body(
        speech={"provider": "local", "model": "base.en", "device": "gpu", "reasoning_effort": None}
    )

    response = client.put("/api/v1/settings", json=body)

    assert response.status_code == 200, response.text
    assert_no_secret(response.text)
    state = response.json()
    assert state["saved"]["profile"] == "custom"
    assert state["saved"]["speech"]["device"] == "gpu"
    assert state["restart_required"] is True
    assert state["next_start"]["speech"] == {
        "provider": "local",
        "model": "base.en",
        "device": "gpu",
        "reasoning_effort": None,
    }
    # The running models are untouched until restart.
    assert state["active"]["speech"]["provider"] == "fake"
    stored = (environment / saved.SETTINGS_FILE_NAME).read_text(encoding="utf-8")
    assert SECRET not in stored


def test_saving_hands_free_with_hosted_vision_selects_timed_capture(
    client: TestClient,
) -> None:
    body = custom_body(
        vision={"provider": "openai", "model": "gpt-5.4-nano"},
        allow_hosted_processing=True,
        hands_free=True,
    )

    response = client.put("/api/v1/settings", json=body)

    assert response.status_code == 200, response.text
    assert_no_secret(response.text)
    assert response.json()["saved"]["vision"]["provider"] == "openai"
    next_settings = load_application_settings()
    assert next_settings.hands_free_profile is True
    assert next_settings.stage_is_remote("vision") is True
    assert next_settings.live_label_scan_enabled is False


def test_invalid_combinations_are_explained_and_not_saved(
    client: TestClient, environment: Path
) -> None:
    response = client.put("/api/v1/settings", json=preset_body("hosted"))

    assert response.status_code == 422
    assert_no_secret(response.text)
    problem = response.json()
    assert problem["code"] == "CONFIGURATION_INVALID"
    assert any("Hosted processing is not allowed" in item for item in problem["problems"])
    assert not (environment / saved.SETTINGS_FILE_NAME).exists()


def test_missing_hosted_credentials_are_explained(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LABJARVIS_HOSTED_API_KEY")
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as client:
        response = client.put(
            "/api/v1/settings", json=preset_body("hybrid", allow_hosted_processing=True)
        )
        assert response.status_code == 422
        assert any("needs an API key" in item for item in response.json()["problems"])
        credentials = client.get("/api/v1/settings").json()["credentials"]
        assert credentials == [
            {
                "id": "openai",
                "label": "OpenAI API key",
                "configured": False,
                "source": None,
                "variable": None,
            },
            {
                "id": "deepseek",
                "label": "DeepSeek API key",
                "configured": False,
                "source": None,
                "variable": None,
            },
        ]


def test_a_key_or_url_cannot_be_smuggled_into_the_saved_configuration(
    client: TestClient, environment: Path
) -> None:
    for extra in ({"hosted_api_key": "sk-other"}, {"hosted_base_url": "https://evil.example"}):
        response = client.put("/api/v1/settings", json={**custom_body(), **extra})
        assert response.status_code == 422
    assert not (environment / saved.SETTINGS_FILE_NAME).exists()


def test_configuration_can_only_be_changed_from_this_computer(environment: Path) -> None:
    with TestClient(start_labjarvis(), client=("192.168.1.20", 50000)) as remote:
        assert remote.put("/api/v1/settings", json=custom_body()).status_code == 403
        assert (
            remote.post("/api/v1/settings/restart", json={}).json()["code"] == "SETTINGS_LOCAL_ONLY"
        )
    assert not (environment / saved.SETTINGS_FILE_NAME).exists()


# --------------------------------------------------------------- restarting


def test_restart_is_refused_when_launched_without_supervision(client: TestClient) -> None:
    client.put("/api/v1/settings", json=custom_body())

    response = client.post("/api/v1/settings/restart", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "RESTART_UNAVAILABLE"
    assert "restart it manually" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"command": "calc.exe"},
        {"args": ["--host", "0.0.0.0"]},
        {"path": "C:/Windows/System32/cmd.exe"},
        {"acknowledge_active_session": "rm -rf /"},
    ],
)
def test_restart_requests_cannot_carry_commands_paths_or_arguments(
    environment: Path, body: dict[str, object]
) -> None:
    supervised = Supervised(environment / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        response = client.post("/api/v1/settings/restart", json=body)

    assert response.status_code == 422
    assert not supervised.requested
    assert supervised.shutdowns == 0


def test_supervised_restart_applies_the_saved_configuration_and_keeps_records(
    environment: Path,
) -> None:
    supervised = Supervised(environment / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        session_id = _import_and_start(client)
        snapshot_before = client.get(f"/api/v1/sessions/{session_id}/snapshot").json()
        notebook_before = client.get(f"/api/v1/sessions/{session_id}/notebook").json()
        first = client.put("/api/v1/settings", json=custom_body()).json()
        assert first["restart"]["available"] is True
        assert first["restart_required"] is True
        assert first["active_sessions"] == 1

        # An active session needs explicit consent before the interface stops.
        refused = client.post("/api/v1/settings/restart", json={})
        assert refused.status_code == 409
        assert refused.json()["code"] == "RESTART_CONFIRMATION_REQUIRED"
        assert "temporarily interrupt the interface" in refused.json()["detail"]
        assert not supervised.requested

        accepted = client.post(
            "/api/v1/settings/restart", json={"acknowledge_active_session": True}
        )
        assert accepted.status_code == 202
        assert accepted.json()["instance_id"] == first["instance_id"]
    assert supervised.requested
    assert supervised.shutdowns == 1

    # The supervisor starts LabJarvis again through the normal startup path.
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as restarted:
        state = restarted.get("/api/v1/settings").json()
        assert state["instance_id"] != first["instance_id"]
        assert state["restart_required"] is False
        assert state["active"]["profile"] == "custom"
        assert state["active"]["speech"]["provider"] == "local"
        assert restarted.app.state.settings.stage_locality("text") == "local"
        assert restarted.get(f"/api/v1/sessions/{session_id}/snapshot").json() == snapshot_before
        assert restarted.get(f"/api/v1/sessions/{session_id}/notebook").json() == notebook_before


def test_restart_is_refused_when_the_saved_configuration_would_not_start(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervised = Supervised(environment / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        saved_state = client.put(
            "/api/v1/settings", json=preset_body("hybrid", allow_hosted_processing=True)
        )
        assert saved_state.status_code == 200, saved_state.text
        monkeypatch.delenv("LABJARVIS_HOSTED_API_KEY")  # the key disappears afterwards

        response = client.post("/api/v1/settings/restart", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "SAVED_CONFIGURATION_INVALID"
    assert not supervised.requested


def test_a_hosted_choice_never_appears_after_restart_unless_it_was_saved(
    environment: Path,
) -> None:
    supervised = Supervised(environment / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        client.put("/api/v1/settings", json=preset_body("local"))
        assert client.post("/api/v1/settings/restart", json={}).status_code == 202

    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as restarted:
        settings: Settings = restarted.app.state.settings
        assert settings.any_stage_remote is False
        assert restarted.get("/api/v1/settings").json()["active"]["profile"] == "local"


def test_switching_text_between_openai_and_deepseek_from_settings(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deepseek_secret = "sk-deepseek-sentinel-never-returned"
    monkeypatch.setenv("LABJARVIS_DEEPSEEK_API_KEY", deepseek_secret)
    supervised = Supervised(environment / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        state = client.get("/api/v1/settings").json()
        text_options = {o["value"]: o["configured"] for o in state["options"]["providers"]["text"]}
        assert text_options == {"local": True, "openai": True, "deepseek": True}
        assert "deepseek" not in {o["value"] for o in state["options"]["providers"]["speech"]}
        deepseek = {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "device": "cpu",
            "reasoning_effort": None,
        }
        saved = client.put(
            "/api/v1/settings", json=custom_body(text=deepseek, allow_hosted_processing=True)
        )
        assert saved.status_code == 200, saved.text
        assert deepseek_secret not in saved.text and SECRET not in saved.text
        assert client.post("/api/v1/settings/restart", json={}).status_code == 202

    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as restarted:
        settings: Settings = restarted.app.state.settings
        assert settings.hosted_base_url_for("text") == "https://api.deepseek.com"
        assert settings.hosted_key_for("text").get_secret_value() == deepseek_secret  # type: ignore[union-attr]
        assert settings.hosted_label_for("text") == "DeepSeek"
        body = restarted.get("/api/v1/settings").text
        assert deepseek_secret not in body and SECRET not in body
        openai = {
            "provider": "openai",
            "model": "gpt-5.4-nano",
            "device": "cpu",
            "reasoning_effort": "none",
        }
        assert (
            restarted.put(
                "/api/v1/settings", json=custom_body(text=openai, allow_hosted_processing=True)
            ).status_code
            == 200
        )

    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as again:
        settings = again.app.state.settings
        assert settings.hosted_base_url_for("text") == "https://api.openai.com/v1"
        assert settings.hosted_key_for("text").get_secret_value() == SECRET  # type: ignore[union-attr]
        assert settings.hosted_label_for("text") == "OpenAI"


def test_a_key_added_to_dotenv_after_startup_counts_without_a_restart(
    environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = environment / ".env"
    dotenv.write_text("", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", str(dotenv))
    deepseek = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "device": "cpu",
        "reasoning_effort": None,
    }
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as client:
        refused = client.put(
            "/api/v1/settings", json=custom_body(text=deepseek, allow_hosted_processing=True)
        )
        assert refused.status_code == 422

        dotenv.write_text("LABJARVIS_DEEPSEEK_API_KEY=sk-added-later\n", encoding="utf-8")

        state = client.get("/api/v1/settings")
        assert "sk-added-later" not in state.text
        assert {
            "id": "deepseek",
            "label": "DeepSeek API key",
            "configured": True,
            "source": ".env",
            "variable": "LABJARVIS_DEEPSEEK_API_KEY",
        } in state.json()["credentials"]
        saved = client.put(
            "/api/v1/settings", json=custom_body(text=deepseek, allow_hosted_processing=True)
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["restart_required"] is True


# ------------------------------------------------ .env as the source of keys


@pytest.fixture
def dotenv(environment: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keys only in .env, as recommended: nothing inherited from the shell."""

    monkeypatch.delenv("LABJARVIS_HOSTED_API_KEY")
    path = environment / ".env"
    monkeypatch.setitem(Settings.model_config, "env_file", str(path))
    return path


def _openai_key(app: FastAPI) -> str:
    key = app.state.settings.connection_for("text", StageProvider.OPENAI)[1]
    return key.get_secret_value()


def test_editing_a_key_in_dotenv_and_restarting_uses_the_new_value(dotenv: Path) -> None:
    dotenv.write_text("LABJARVIS_OPENAI_API_KEY=sk-first\n", encoding="utf-8")
    supervised = Supervised(dotenv.parent / "supervisor")
    with TestClient(start_labjarvis(supervised=supervised), client=THIS_COMPUTER) as client:
        assert _openai_key(client.app) == "sk-first"
        client.put("/api/v1/settings", json=preset_body("hybrid", allow_hosted_processing=True))
        dotenv.write_text("LABJARVIS_OPENAI_API_KEY=sk-rotated\n", encoding="utf-8")
        state = client.get("/api/v1/settings").json()
        assert state["warnings"] == []
        assert client.post("/api/v1/settings/restart", json={}).status_code == 202

    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as restarted:
        assert _openai_key(restarted.app) == "sk-rotated"
        settings: Settings = restarted.app.state.settings
        assert settings.hosted_key_for("text").get_secret_value() == "sk-rotated"  # type: ignore[union-attr]


def test_precedence_is_per_variable_and_a_shadowing_variable_is_reported(
    dotenv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv.write_text("LABJARVIS_OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    # A different variable in the environment does not outrank .env's value ...
    monkeypatch.setenv("LABJARVIS_HOSTED_API_KEY", "sk-inherited-old-name")
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as client:
        assert _openai_key(client.app) == "sk-from-dotenv"
        warnings = client.get("/api/v1/settings").json()["warnings"]
        assert any(
            "Both LABJARVIS_OPENAI_API_KEY and LABJARVIS_HOSTED_API_KEY" in w for w in warnings
        )

    # ... but the same variable does, and the page says so rather than hiding it.
    monkeypatch.setenv("LABJARVIS_OPENAI_API_KEY", "sk-from-shell")
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as client:
        assert _openai_key(client.app) == "sk-from-shell"
        body = client.get("/api/v1/settings")
        assert "sk-from" not in body.text
        warnings = body.json()["warnings"]
        assert any(
            "LABJARVIS_OPENAI_API_KEY environment variable" in w
            and "takes precedence over .env" in w
            for w in warnings
        )


def test_dotenv_lines_that_settings_manages_are_reported_as_dead(dotenv: Path) -> None:
    dotenv.write_text(
        "LABJARVIS_OPENAI_API_KEY=sk-x\nLABJARVIS_TEXT_MODEL_NAME=qwen3:4b\nLABJARVIS_MODEL_MODE=local\n",
        encoding="utf-8",
    )
    with TestClient(start_labjarvis(), client=THIS_COMPUTER) as client:
        assert client.get("/api/v1/settings").json()["warnings"] == []  # nothing saved yet
        client.put("/api/v1/settings", json=custom_body())
        warnings = client.get("/api/v1/settings").json()["warnings"]
    assert warnings == [
        "These .env lines have no effect because the Settings page manages them: "
        "LABJARVIS_MODEL_MODE, LABJARVIS_TEXT_MODEL_NAME. Remove them from .env and choose "
        "in Settings instead."
    ]
