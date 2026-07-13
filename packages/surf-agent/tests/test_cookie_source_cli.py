from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from surf_agent.cli import main


def make_source(root: Path) -> None:
    (root / "Default").mkdir(parents=True)


def test_cookie_source_commands_do_not_construct_browser_and_preserve_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.json"
    source = tmp_path / "google-chrome"
    make_source(source)
    config.write_text(json.dumps({"backend": "axi", "unknown": 1}))
    monkeypatch.setattr("surf_agent.cli.backend_config_file", lambda: config)
    monkeypatch.setenv("SURF_AGENT_CHROME_BIN", "google-chrome")

    class MustNotConstruct:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("browser constructed")

    monkeypatch.setattr("surf_agent.cli.SurfAgent", MustNotConstruct)
    assert main(["profile", "cookie-source", "set", "--source", str(source), "--source-profile", "Default", "--domain", "Example.com", "--domain", "example.com"]) == 0
    stored = json.loads(config.read_text())
    assert stored["backend"] == "axi"
    assert stored["unknown"] == 1
    assert stored["cookie_source"]["scope"]["domains"] == ["example.com"]

    out = io.StringIO()
    with redirect_stdout(out):
        assert main(["profile", "cookie-source", "show"]) == 0
    assert "secret" not in out.getvalue()
    assert main(["profile", "cookie-source", "reset"]) == 0
    assert json.loads(config.read_text()) == {"backend": "axi", "unknown": 1}


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--source", "/tmp/x", "--source-profile", "Default"],
        ["--source", "/tmp/x", "--source-profile", "Default", "--domain", "example.com", "--all-domains"],
    ],
)
def test_cookie_source_set_requires_exactly_one_scope_form(args: list[str]) -> None:
    assert main(["profile", "cookie-source", "set", *args]) == 2


def test_import_requires_config_and_camoufox_rejects_cookie_features(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.json"
    monkeypatch.setattr("surf_agent.cli.backend_config_file", lambda: config)
    assert main(["profile", "import-cookies"]) == 1
    monkeypatch.setenv("SURF_AGENT_BACKEND", "camoufox")
    assert main(["profile", "cookie-source", "show"]) == 1


def test_cookie_source_set_rejects_source_family_that_cannot_match_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.json"
    source = tmp_path / "google-chrome"
    make_source(source)
    monkeypatch.setattr("surf_agent.cli.backend_config_file", lambda: config)
    monkeypatch.setenv("SURF_AGENT_CHROME_BIN", "chromium")

    assert main(["profile", "cookie-source", "set", "--source", str(source), "--source-profile", "Default", "--domain", "example.com"]) == 1
    assert not config.exists()


def test_explicit_import_delegates_to_agent_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"cookie_source": {"root": str(tmp_path / "google-chrome"), "profile": "Default", "family": "chrome", "scope": {"all_domains": False, "domains": ["example.com"]}}}))
    monkeypatch.setattr("surf_agent.cli.backend_config_file", lambda: config)

    class Agent:
        backend = "axi"

        def __init__(self) -> None:
            self.force_calls = 0

        def force_cookie_import(self):
            from surf_agent.cookie_import import CookieImportResult

            self.force_calls += 1
            return CookieImportResult(imported_rows=3, destination=tmp_path / "destination" / "Default" / "Cookies")

    agent = Agent()
    monkeypatch.setattr("surf_agent.cli.SurfAgent", lambda: agent)
    assert main(["profile", "import-cookies"]) == 0
    assert agent.force_calls == 1
