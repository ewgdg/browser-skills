from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_backend_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep pytest runs independent of user surf-agent backend config."""
    import surf_agent.runtime as runtime

    monkeypatch.setattr(runtime, "backend_config_file", lambda: tmp_path / "config.json")
