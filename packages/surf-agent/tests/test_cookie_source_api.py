from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from surf_agent import Browser, SurfAgentError


def make_source(root: Path) -> None:
    (root / "Default").mkdir(parents=True)


def test_cookie_source_commands_do_not_construct_browser_and_preserve_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    source = tmp_path / "google-chrome"
    make_source(source)
    config.write_text(json.dumps({"backend": "axi", "unknown": 1}))
    monkeypatch.setattr("surf_agent.runtime.backend_config_file", lambda: config)
    monkeypatch.setenv("SURF_AGENT_CHROME_BIN", "google-chrome")

    class MustNotConstruct:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("browser constructed")

    monkeypatch.setattr("surf_agent.browser.SurfAgent", MustNotConstruct)
    Browser().set_cookie_source(
        str(source), "Default", domains=["Example.com", "example.com"]
    )
    stored = json.loads(config.read_text())
    assert stored["backend"] == "axi"
    assert stored["unknown"] == 1
    assert stored["cookie_source"]["scope"]["domains"] == ["example.com"]

    out = io.StringIO()
    with redirect_stdout(out):
        assert Browser().cookie_source() is not None
    assert "secret" not in out.getvalue()
    Browser().reset_cookie_source()
    assert json.loads(config.read_text()) == {"backend": "axi", "unknown": 1}


@pytest.mark.parametrize("domains,all_domains", [((), False), (("example.com",), True)])
def test_cookie_source_set_requires_exactly_one_scope_form(domains, all_domains):
    with pytest.raises(SurfAgentError):
        Browser().set_cookie_source(
            "/tmp/x", "Default", domains=domains, all_domains=all_domains
        )


def test_import_requires_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    monkeypatch.setattr("surf_agent.runtime.backend_config_file", lambda: config)
    with pytest.raises(SurfAgentError, match="no cookie source"):
        Browser().import_cookies()


def test_cookie_source_set_rejects_source_family_that_cannot_match_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    source = tmp_path / "google-chrome"
    make_source(source)
    monkeypatch.setattr("surf_agent.runtime.backend_config_file", lambda: config)
    monkeypatch.setenv("SURF_AGENT_BACKEND", "axi")
    monkeypatch.setenv("SURF_AGENT_CHROME_BIN", "chromium")

    with pytest.raises(SurfAgentError, match="family"):
        Browser().set_cookie_source(str(source), "Default", domains=["example.com"])
    assert not config.exists()


def test_explicit_import_delegates_to_agent_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "cookie_source": {
                    "root": str(tmp_path / "google-chrome"),
                    "profile": "Default",
                    "family": "chrome",
                    "scope": {"all_domains": False, "domains": ["example.com"]},
                }
            }
        )
    )
    monkeypatch.setattr("surf_agent.runtime.backend_config_file", lambda: config)

    class Agent:
        backend = "axi"

        def __init__(self) -> None:
            self.force_calls = 0

        def force_cookie_import(self):
            from surf_agent.cookie_import import CookieImportResult

            self.force_calls += 1
            return CookieImportResult(
                imported_rows=3,
                destination=tmp_path / "destination" / "Default" / "Cookies",
            )

    agent = Agent()
    monkeypatch.setattr("surf_agent.browser.SurfAgent", lambda: agent)
    assert Browser().import_cookies().imported_rows == 3
    assert agent.force_calls == 1
