"""Silent administrative operations for Surf's dedicated browser profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import config, runtime
from .chrome_lifecycle import (
    axi_destination_identity_unprovable,
    destination_browser_family,
)
from .config import CookieSourceConfig
from .cookie_import import UNSUPPORTED_PLATFORM_MESSAGE, CookieImportResult, supports_live_cookie_import
from .errors import SurfAgentError
from .runtime import SurfAgent


@dataclass(frozen=True)
class BackendInfo:
    backend: str
    source: str
    config_file: Path


@dataclass(frozen=True)
class ProfileInfo:
    backend: str
    profile_dir: Path
    browser_url: str
    chrome_class: str
    patchright_bridge_port: int
    patchright_app_id: str


@dataclass(frozen=True)
class ThreadInfo:
    name: str
    page_id: int | None
    url: str | None
    title: str | None


def _check_status(status: int | None, operation: str) -> None:
    if status not in (None, 0):
        raise SurfAgentError(f"{operation} failed: backend returned status {status}")


class Browser:
    """Configure Surf without creating a window; actions create runtime on demand."""

    def backend(self) -> BackendInfo:
        backend, source = runtime.resolve_backend_preference()
        return BackendInfo(backend, source, runtime.backend_config_file())

    def set_backend(self, name: str) -> None:
        name = config.validate_backend_name(name)
        path = runtime.backend_config_file()
        try:
            # Cleanup follows the persisted owner, not a one-script env override.
            previous, _ = config.resolve_backend_preference(path=path, environ={})
        except SurfAgentError:
            # A valid JSON config with an unsupported old value remains repairable.
            previous = None
        if previous is not None and previous != name:
            _check_status(
                SurfAgent(backend=previous).browser_backend.bridge_stop(),
                "previous backend cleanup",
            )
        config.set_backend(name, path=runtime.backend_config_file())

    def reset_backend(self) -> None:
        config.reset_backend(path=runtime.backend_config_file())

    def setup(self) -> None:
        """Validate prerequisites; installation remains an explicit human action."""
        if (
            self.backend().backend == "patchright"
            and not runtime.python_module_available("patchright")
        ):
            raise SurfAgentError("install surf-agent with the patchright extra")
        if not (os.environ.get("SURF_AGENT_CHROME_BIN") or runtime.find_chrome_bin()):
            raise SurfAgentError("install Google Chrome or set SURF_AGENT_CHROME_BIN")

    def profile(self) -> ProfileInfo:
        agent = SurfAgent()
        path = (
            agent.chrome_profile_dir
            if agent.backend == "axi"
            else agent.patchright_profile_dir
        )
        return ProfileInfo(
            agent.backend,
            path,
            agent.browser_url,
            agent.chrome_class,
            agent.patchright_port,
            agent.patchright_app_id,
        )

    def open_profile(self, url: str = "about:blank") -> None:
        _check_status(SurfAgent().profile_open(url), "profile open")

    def cookie_source(self) -> CookieSourceConfig | None:
        return config.get_cookie_source(path=runtime.backend_config_file())

    def set_cookie_source(
        self,
        source: str,
        profile: str,
        *,
        domains: Sequence[str] = (),
        all_domains: bool = False,
    ) -> CookieSourceConfig:
        if all_domains == bool(domains):
            raise SurfAgentError("provide domains or all_domains, exclusively")
        if not supports_live_cookie_import():
            raise SurfAgentError(UNSUPPORTED_PLATFORM_MESSAGE)
        scope = (
            config.CookieScope.all()
            if all_domains
            else config.CookieScope.from_domains(domains)
        )
        value = config.resolve_cookie_source(
            source=source, profile=profile, scope=scope
        )
        backend = self.backend().backend
        if backend == "axi" and axi_destination_identity_unprovable(os.environ):
            raise SurfAgentError(
                "cannot prove the AXI destination profile while an auto-connect or browser URL override is active"
            )
        family = destination_browser_family(
            backend=backend,
            executable=os.environ.get("SURF_AGENT_CHROME_BIN")
            or runtime.find_chrome_bin(),
        )
        if family is None or value.family != family:
            raise SurfAgentError(
                "cookie source browser family does not match the selected Surf destination browser"
            )
        config.set_cookie_source(value, path=runtime.backend_config_file())
        return value

    def reset_cookie_source(self) -> None:
        config.reset_cookie_source(path=runtime.backend_config_file())

    def import_cookies(self) -> CookieImportResult:
        if self.cookie_source() is None:
            raise SurfAgentError(
                "no cookie source is configured; call Browser.set_cookie_source first"
            )
        return SurfAgent().force_cookie_import()

    def import_cookies_for(self, domain: str) -> CookieImportResult:
        """Add one consented domain to the cookie scope, then restart the profile and import.

        Import needs an inactive profile, and stopping the bridge would close
        every open thread, so this refuses while any thread remains open.
        """
        source = self.cookie_source()
        if source is None:
            raise SurfAgentError(
                "no cookie source is configured; ask the user for their Chrome profile, then call Browser.set_cookie_source"
            )
        open_threads = sorted(thread.name for thread in self.threads())
        if open_threads:
            raise SurfAgentError(
                "close open threads before importing cookies; stopping the browser would close: "
                + ", ".join(open_threads)
            )
        if not source.scope.all_domains:
            self.set_cookie_source(
                str(source.root), source.profile, domains=[*source.scope.domains, domain]
            )
        self.stop_bridge()
        return self.import_cookies()

    def stop_bridge(self) -> None:
        _check_status(SurfAgent().browser_backend.bridge_stop(), "bridge stop")

    def close_matching(self, pattern: str) -> None:
        _check_status(
            SurfAgent().browser_backend.close_matching(pattern), "close matching"
        )

    def threads(self) -> list[ThreadInfo]:
        return [
            ThreadInfo(
                item["thread"], item.get("page_id"), item.get("url"), item.get("title")
            )
            for item in SurfAgent().browser_backend.list_threads()
        ]
