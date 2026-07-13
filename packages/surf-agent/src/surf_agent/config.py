from __future__ import annotations

import ipaddress
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import tldextract

from .constants import CAMOUFOX_BACKEND, DEFAULT_BACKEND, PATCHRIGHT_BACKEND
from .errors import SurfAgentError

# Use tldextract's packaged Public Suffix List snapshot.  Disabling network fetches
# keeps command behavior deterministic and avoids exposing configured domains.
_PSL = tldextract.TLDExtract(suffix_list_urls=())
_BACKENDS = {DEFAULT_BACKEND, CAMOUFOX_BACKEND, PATCHRIGHT_BACKEND}


@dataclass(frozen=True)
class CookieScope:
    domains: tuple[str, ...] = ()
    all_domains: bool = False

    @classmethod
    def from_domains(cls, values: Iterable[str]) -> "CookieScope":
        domains = normalize_domains(values)
        if not domains:
            raise SurfAgentError("cookie source requires at least one --domain or --all-domains", exit_code=2)
        return cls(domains=domains)

    @classmethod
    def all(cls) -> "CookieScope":
        return cls(all_domains=True)

    def to_json(self) -> dict[str, Any]:
        return {"all_domains": self.all_domains, "domains": list(self.domains)}

    @classmethod
    def from_json(cls, value: Any) -> "CookieScope":
        if not isinstance(value, dict):
            raise SurfAgentError("cookie source scope must be an object")
        all_domains = value.get("all_domains") is True
        domains_value = value.get("domains", [])
        if not isinstance(domains_value, list) or not all(isinstance(item, str) for item in domains_value):
            raise SurfAgentError("cookie source domains must be a list of strings")
        domains = normalize_domains(domains_value)
        if all_domains == bool(domains):
            raise SurfAgentError("cookie source scope must contain exactly one of domains or all-domains")
        return cls(domains=domains, all_domains=all_domains)


@dataclass(frozen=True)
class CookieSourceConfig:
    root: Path
    profile: str
    family: str
    scope: CookieScope

    def to_json(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "profile": self.profile,
            "family": self.family,
            "scope": self.scope.to_json(),
        }

    @classmethod
    def from_json(cls, value: Any) -> "CookieSourceConfig":
        if not isinstance(value, dict):
            raise SurfAgentError("cookie source configuration must be an object")
        root = value.get("root")
        profile = value.get("profile")
        family = value.get("family")
        if not isinstance(root, str) or not isinstance(profile, str) or not isinstance(family, str):
            raise SurfAgentError("cookie source configuration is incomplete")
        return cls(root=Path(root), profile=validate_profile_name(profile), family=validate_browser_family(family), scope=CookieScope.from_json(value.get("scope")))


def validate_backend_name(value: str, *, source: str = "backend") -> str:
    backend = value.strip().lower()
    if backend not in _BACKENDS:
        raise SurfAgentError(f"{source} must be 'axi', 'camoufox', or 'patchright'", exit_code=2)
    return backend


def validate_browser_family(value: str) -> str:
    family = value.strip().lower()
    if family not in {"chrome", "chromium", "brave", "edge"}:
        raise SurfAgentError("could not prove the cookie source browser family")
    return family


def normalize_domains(values: Iterable[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise SurfAgentError("--domain must be a domain name", exit_code=2)
        value = raw.strip().lower().removeprefix(".")
        if not value or any(marker in value for marker in (":", "/", "*", "@")) or "://" in raw:
            raise SurfAgentError(f"invalid cookie domain: {raw!r}", exit_code=2)
        try:
            ascii_value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise SurfAgentError(f"invalid cookie domain: {raw!r}", exit_code=2) from exc
        try:
            ipaddress.ip_address(ascii_value)
        except ValueError:
            pass
        else:
            raise SurfAgentError(f"cookie domain must not be an IP address or public suffix: {raw!r}", exit_code=2)
        if any(not label or len(label) > 63 or not all(ch.isalnum() or ch == "-" for ch in label) or label.startswith("-") or label.endswith("-") for label in ascii_value.split(".")):
            raise SurfAgentError(f"invalid cookie domain: {raw!r}", exit_code=2)
        extracted = _PSL(ascii_value)
        if not extracted.domain or not extracted.suffix:
            raise SurfAgentError(f"cookie domain must not be an IP address or public suffix: {raw!r}", exit_code=2)
        normalized.add(ascii_value)
    return tuple(sorted(normalized))


def validate_profile_name(value: str) -> str:
    profile = value.strip()
    if not profile or profile in {".", ".."} or Path(profile).name != profile:
        raise SurfAgentError("--source-profile must be one profile directory name", exit_code=2)
    return profile


def detect_browser_family(root: Path) -> str:
    parts = {part.lower() for part in root.parts}
    name = root.name.lower()
    if "google-chrome" in parts or name in {"google-chrome", "google-chrome-beta", "google-chrome-unstable"}:
        return "chrome"
    if "bravesoftware" in parts or "brave-browser" in parts or "brave" in name:
        return "brave"
    if "microsoft-edge" in parts or "edge" in name:
        return "edge"
    if "chromium" in parts or name == "chromium":
        return "chromium"
    raise SurfAgentError(f"could not prove Chrome-family browser for cookie source {root}")


def resolve_cookie_source(*, source: str | Path, profile: str, scope: CookieScope) -> CookieSourceConfig:
    root_input = Path(source).expanduser()
    try:
        root_stat = root_input.lstat()
        if stat.S_ISLNK(root_stat.st_mode):
            raise SurfAgentError("cookie source root must not be a symlink")
        root = root_input.resolve(strict=True)
    except SurfAgentError:
        raise
    except OSError as exc:
        raise SurfAgentError(f"could not resolve cookie source {root_input}: {exc}") from exc
    if not root.is_dir():
        raise SurfAgentError("cookie source must be a browser user-data directory")
    profile_name = validate_profile_name(profile)
    profile_path = root / profile_name
    try:
        profile_stat = profile_path.lstat()
        resolved_profile = profile_path.resolve(strict=True)
    except OSError as exc:
        raise SurfAgentError(f"could not resolve cookie source profile {profile_name}: {exc}") from exc
    if stat.S_ISLNK(profile_stat.st_mode) or not resolved_profile.is_dir() or resolved_profile.parent != root:
        raise SurfAgentError("cookie source profile must be a non-symlink child of the source root")
    return CookieSourceConfig(root=root, profile=profile_name, family=detect_browser_family(root), scope=scope)


def load_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SurfAgentError(f"could not read surf-agent config {path}: {exc}", exit_code=2) from exc
    if not isinstance(data, dict):
        raise SurfAgentError(f"surf-agent config {path} must contain a JSON object", exit_code=2)
    return data


def write_config(path: Path, config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise SurfAgentError("surf-agent config must contain a JSON object", exit_code=2)
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise SurfAgentError(f"could not write surf-agent config {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def resolve_backend_preference(*, path: Path, environ: dict[str, str] | None = None) -> tuple[str, str]:
    environment = os.environ if environ is None else environ
    env_backend = environment.get("SURF_AGENT_BACKEND")
    if env_backend:
        return validate_backend_name(env_backend, source="SURF_AGENT_BACKEND"), "env"
    configured = load_config(path).get("backend")
    if isinstance(configured, str) and configured.strip():
        return validate_backend_name(configured, source=str(path)), "config"
    return DEFAULT_BACKEND, "default"


def set_backend(backend: str, *, path: Path) -> None:
    config = load_config(path)
    config["backend"] = validate_backend_name(backend)
    write_config(path, config)


def reset_backend(*, path: Path) -> None:
    config = load_config(path)
    config.pop("backend", None)
    if config:
        write_config(path, config)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def get_cookie_source(*, path: Path) -> CookieSourceConfig | None:
    value = load_config(path).get("cookie_source")
    return CookieSourceConfig.from_json(value) if value is not None else None


def set_cookie_source(config: CookieSourceConfig, *, path: Path) -> None:
    document = load_config(path)
    document["cookie_source"] = config.to_json()
    write_config(path, document)


def reset_cookie_source(*, path: Path) -> None:
    document = load_config(path)
    document.pop("cookie_source", None)
    if document:
        write_config(path, document)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
