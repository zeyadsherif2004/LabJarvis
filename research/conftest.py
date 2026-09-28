"""Suite-wide isolation from the developer's shell.

``Settings`` reads every ``LABJARVIS_*`` variable and a ``.env`` file. Without
isolation a developer who exports ``LABJARVIS_MODEL_MODE=hosted`` for their own
work turns ordinary tests into configuration failures, and in the worst case a
test could build a hosted adapter from a real key. Every test therefore starts
from an empty LabJarvis environment and no ``.env`` file; a test that needs a
setting passes it explicitly or sets it with ``monkeypatch``.

The opt-in smoke suites (``model`` and ``hosted_smoke``) are the only exception:
they exist precisely to exercise the operator's configured models and
credentials, and they are skipped unless their explicit run flag is set.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from labjarvis.settings import Settings, get_settings

_AMBIENT_MARKERS = ("model", "hosted_smoke")


@pytest.fixture(autouse=True)
def _isolate_labjarvis_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    if any(request.node.get_closest_marker(marker) for marker in _AMBIENT_MARKERS):
        yield
        return
    for name in list(os.environ):
        if name.upper().startswith("LABJARVIS_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
