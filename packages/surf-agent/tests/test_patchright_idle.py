from __future__ import annotations

from pathlib import Path

from surf_agent.backends.patchright.bridge import PatchrightRuntime


class Page:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self, pages: list[Page]) -> None:
        self.pages = pages
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_patchright_launch_is_windowless_with_patchrights_own_flags(monkeypatch, tmp_path: Path) -> None:
    from surf_agent.backends.patchright import bridge

    captured_with: dict[str, object] = {}
    launch_options: dict[str, object] = {}
    patchright_flags = [
        "--disable-field-trial-config",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--user-data-dir={tmp_path / 'chrome'}",
        "--remote-debugging-pipe",
        "about:blank",
    ]

    async def capture(chromium, **options):
        captured_with.update(options)
        return patchright_flags

    class Chromium:
        async def launch_persistent_context(self, **kwargs):
            launch_options.update(kwargs)
            return object()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(bridge, "async_playwright", Manager)
    monkeypatch.setattr(bridge, "capture_default_args", capture)
    runtime = PatchrightRuntime(
        profile_dir=tmp_path / "chrome",
        app_id="surf-agent",
        window_class="Surf Agent",
    )
    try:
        runtime.start()
    finally:
        runtime.stop()

    shared = {
        "user_data_dir": str(tmp_path / "chrome"),
        "headless": False,
        "no_viewport": True,
        "color_scheme": "null",
        "chromium_sandbox": True,
    }
    assert captured_with == shared
    assert launch_options == {
        **shared,
        "channel": "chrome",
        # Patchright skips its first-page wait only when it adds no args of its own.
        "ignore_default_args": True,
        "timeout": bridge.LAUNCH_TIMEOUT_MS,
        "args": [
            "--disable-field-trial-config",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--remote-debugging-pipe",
            "--class=Surf Agent",
            "--name=surf-agent",
            "--no-startup-window",
        ],
    }


def test_patchright_requests_shutdown_only_after_final_close_response() -> None:
    page = Page()
    context = Context([page])
    runtime = PatchrightRuntime(profile_dir=Path("/tmp/patchright-idle"))
    runtime.browser_or_context = context
    runtime.pages["thread"] = __import__("surf_agent.backends.bridge_common", fromlist=["PageSlot"]).PageSlot(page=page, page_id=1)

    assert runtime.call("close", {"thread": "thread"}) == "closed\n"
    assert runtime.shutdown_requested is False
    runtime.after_response("close")
    assert runtime.shutdown_requested is True
    assert runtime.service_actions() is True
    assert runtime.browser_or_context is None


def test_patchright_final_close_keeps_bridge_when_visible_page_remains() -> None:
    page = Page()
    context = Context([page])
    runtime = PatchrightRuntime(profile_dir=Path("/tmp/patchright-idle"))
    runtime.browser_or_context = context
    runtime.pages["thread"] = __import__("surf_agent.backends.bridge_common", fromlist=["PageSlot"]).PageSlot(page=page, page_id=1)

    runtime.call("close", {"thread": "thread"})
    context.pages.append(Page())
    runtime.after_response("close")

    assert runtime.shutdown_requested is False
    assert runtime.service_actions() is False
    assert context.closed is False


def test_closed_context_recovery_requests_fresh_bridge_restart_without_direct_relaunch() -> None:
    from surf_agent.backends.patchright.bridge import CONTEXT_RESTART_REQUIRED

    runtime = PatchrightRuntime(profile_dir=Path("/tmp/patchright-recovery"))
    events: list[str] = []

    async def stop() -> str:
        events.append("stopped")
        runtime.manager = None
        runtime.browser_or_context = None
        return "stopped\n"

    async def start() -> None:
        events.append("relaunched")

    runtime._stop_async = stop
    runtime._start_async = start
    with __import__("pytest").raises(RuntimeError, match=CONTEXT_RESTART_REQUIRED):
        runtime._run(runtime._restart_closed_context())

    assert events == ["stopped"]
    assert runtime.restart_requested is True
