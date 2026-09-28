"""The narrow restart protocol between the API and the launcher's supervisor.

A full process-level restart is exercised manually (see the Settings section
of the README); these tests pin the protocol both sides rely on.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from labjarvis.infrastructure import restart
from labjarvis.infrastructure.restart import RestartController

SCRIPTS = Path(__file__).resolve().parents[5] / "scripts"


def _supervisor_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("labjarvis_supervisor", SCRIPTS / "supervisor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


supervisor = _supervisor_module()


def test_both_sides_share_the_protocol_constants() -> None:
    for name in (
        "SUPERVISOR_TOKEN_ENV",
        "SUPERVISOR_DIR_ENV",
        "SUPERVISOR_FILE",
        "RESTART_REQUEST_FILE",
    ):
        assert getattr(supervisor, name) == getattr(restart, name)


def test_supervised_launch_offers_restart_and_the_supervisor_honours_it(tmp_path: Path) -> None:
    launcher = supervisor.RestartSupervisor(tmp_path)
    environment = launcher.start()
    stopped: list[bool] = []
    controller = RestartController(environment, shutdown=lambda: stopped.append(True))

    assert controller.availability().available is True
    controller.request_restart()
    controller.shutdown()

    assert stopped == [True]
    assert launcher.consume_restart_request() is True
    assert launcher.consume_restart_request() is False  # exactly once


def test_unsupervised_launch_says_so_and_cannot_restart(tmp_path: Path) -> None:
    controller = RestartController({}, shutdown=lambda: pytest.fail("must not shut down"))

    availability = controller.availability()

    assert availability.available is False
    assert availability.reason == restart.UNSUPERVISED_REASON
    assert "restart it manually" in (availability.reason or "")
    with pytest.raises(RuntimeError):
        controller.request_restart()


def test_a_supervisor_that_has_gone_away_is_not_trusted(tmp_path: Path) -> None:
    launcher = supervisor.RestartSupervisor(tmp_path)
    environment = launcher.start()
    launcher.stop()

    availability = RestartController(environment).availability()

    assert availability.available is False
    assert availability.reason == restart.SUPERVISOR_GONE_REASON


def test_a_token_from_another_launch_is_refused(tmp_path: Path) -> None:
    launcher = supervisor.RestartSupervisor(tmp_path)
    environment = launcher.start()
    forged = {**environment, restart.SUPERVISOR_TOKEN_ENV: "not-the-token"}

    assert RestartController(forged).availability().available is False
    (tmp_path / restart.RESTART_REQUEST_FILE).write_text("not-the-token", encoding="utf-8")
    assert launcher.consume_restart_request() is False


def test_a_stale_request_from_an_earlier_launch_does_not_fire(tmp_path: Path) -> None:
    (tmp_path / restart.RESTART_REQUEST_FILE).write_text("old", encoding="utf-8")
    launcher = supervisor.RestartSupervisor(tmp_path)

    launcher.start()

    assert launcher.consume_restart_request() is False


def test_the_marker_holds_only_the_launch_token(tmp_path: Path) -> None:
    launcher = supervisor.RestartSupervisor(tmp_path, token="fixed-token")
    controller = RestartController(launcher.start(), shutdown=lambda: None)

    controller.request_restart()

    assert (tmp_path / restart.RESTART_REQUEST_FILE).read_text(encoding="utf-8") == "fixed-token"
    record = json.loads((tmp_path / restart.SUPERVISOR_FILE).read_text(encoding="utf-8"))
    assert set(record) == {"token", "pid"}


def _tasks_module() -> ModuleType:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("labjarvis_tasks", SCRIPTS / "tasks.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_the_launcher_warns_about_inherited_labjarvis_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tasks = _tasks_module()

    tasks.warn_about_inherited_configuration(
        {"PATH": "x", "LABJARVIS_OPENAI_API_KEY": "sk-secret-value", "LABJARVIS_EMPTY": " "}
    )
    err = capsys.readouterr().err
    assert "LABJARVIS_OPENAI_API_KEY" in err
    assert "sk-secret-value" not in err
    assert "LABJARVIS_EMPTY" not in err

    tasks.warn_about_inherited_configuration({"PATH": "x"})
    assert capsys.readouterr().err == ""


def test_the_launcher_restarts_only_its_fixed_api_command() -> None:
    source = (SCRIPTS / "tasks.py").read_text(encoding="utf-8")
    loop = source[source.index("def dev(") : source.index("KIOSK_URL")]

    # One command list, built before the loop and reused verbatim on restart.
    assert loop.count("api_command = [") == 1
    assert loop.count("subprocess.Popen(api_command, cwd=ROOT, env=environment)") == 2
    assert "consume_restart_request()" in loop
