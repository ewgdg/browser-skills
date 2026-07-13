from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from surf_agent.config import (
    CookieScope,
    load_config,
    normalize_domains,
    reset_backend,
    resolve_backend_preference,
    set_backend,
    write_config,
)
from surf_agent.errors import SurfAgentError


def test_backend_preference_is_environment_then_persisted_then_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    assert resolve_backend_preference(path=path) == ("axi", "default")
    write_config(path, {"backend": "patchright", "unknown": {"keep": True}})
    assert resolve_backend_preference(path=path) == ("patchright", "config")
    monkeypatch.setenv("SURF_AGENT_BACKEND", "camoufox")
    assert resolve_backend_preference(path=path) == ("camoufox", "env")


def test_backend_mutations_preserve_unknown_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_config(path, {"unknown": {"keep": True}})
    set_backend("patchright", path=path)
    assert load_config(path) == {"backend": "patchright", "unknown": {"keep": True}}
    reset_backend(path=path)
    assert load_config(path) == {"unknown": {"keep": True}}


def test_atomic_write_failure_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"backend":"axi"}\n')

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("surf_agent.config.os.replace", fail_replace)
    with pytest.raises(SurfAgentError, match="could not write surf-agent config"):
        write_config(path, {"backend": "patchright"})
    assert json.loads(path.read_text()) == {"backend": "axi"}


def test_written_config_is_user_only(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_config(path, {"backend": "axi"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_malformed_config_is_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("not-json")
    with pytest.raises(SurfAgentError, match="could not read surf-agent config"):
        load_config(path)


def test_scope_domains_are_normalized_and_reject_unsafe_values() -> None:
    assert normalize_domains([".Example.COM", "example.com", "www.example.com"]) == ("example.com", "www.example.com")
    for value in ("https://example.com", "example.com/a", "example.com:443", "*.example.com", "", "127.0.0.1", "com"):
        with pytest.raises(SurfAgentError):
            CookieScope.from_domains([value])


def test_cookie_source_must_match_a_provable_destination_family(tmp_path: Path) -> None:
    from surf_agent.chrome_lifecycle import destination_browser_family

    assert destination_browser_family(backend="axi", executable="google-chrome") == "chrome"
    assert destination_browser_family(backend="axi", executable="brave-browser") == "brave"


def test_cookie_source_rejects_symlink_root(tmp_path: Path) -> None:
    root = tmp_path / "google-chrome"
    (root / "Default").mkdir(parents=True)
    link = tmp_path / "linked-google-chrome"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(SurfAgentError, match="symlink"):
        from surf_agent.config import resolve_cookie_source

        resolve_cookie_source(source=link, profile="Default", scope=CookieScope.from_domains(["example.com"]))
