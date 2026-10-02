from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from surf_agent.config import (
    CookieScope,
    load_config,
    normalize_domains,
    resolve_cookie_source,
    write_config,
)
from surf_agent.errors import SurfAgentError


def test_atomic_write_failure_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"keep":true}\n')

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("surf_agent.config.os.replace", fail_replace)
    with pytest.raises(SurfAgentError, match="could not write surf-agent config"):
        write_config(path, {"keep": False})
    assert json.loads(path.read_text()) == {"keep": True}


def test_written_config_is_user_only(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_config(path, {"keep": True})
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


def test_cookie_source_family_is_proven_from_macos_user_data_roots(tmp_path: Path) -> None:
    support = tmp_path / "Library" / "Application Support"
    roots = {
        support / "Google" / "Chrome": "chrome",
        support / "Chromium": "chromium",
        support / "BraveSoftware" / "Brave-Browser": "brave",
        support / "Microsoft Edge": "edge",
    }
    for root, family in roots.items():
        (root / "Default").mkdir(parents=True)
        assert resolve_cookie_source(source=root, profile="Default", scope=CookieScope.from_domains(["example.com"])).family == family


def test_cookie_source_rejects_symlink_root(tmp_path: Path) -> None:
    root = tmp_path / "google-chrome"
    (root / "Default").mkdir(parents=True)
    link = tmp_path / "linked-google-chrome"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(SurfAgentError, match="symlink"):
        from surf_agent.config import resolve_cookie_source

        resolve_cookie_source(source=link, profile="Default", scope=CookieScope.from_domains(["example.com"]))
