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


def test_patchright_launch_uses_host_theme_and_keychain(monkeypatch, tmp_path: Path) -> None:
    from surf_agent.backends.patchright import bridge

    launch_options: dict[str, object] = {}

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
    runtime = PatchrightRuntime(
        profile_dir=tmp_path / "chrome",
        app_id="surf-agent",
        window_class="Surf Agent",
    )
    try:
        runtime.start()
    finally:
        runtime.stop()

    assert launch_options == {
        "user_data_dir": str(tmp_path / "chrome"),
        "channel": "chrome",
        "headless": False,
        "no_viewport": True,
        "color_scheme": "null",
        "chromium_sandbox": True,
        "args": ["--class=Surf Agent", "--name=surf-agent"],
        "ignore_default_args": ("--password-store=basic", "--use-mock-keychain"),
    }


def test_patchright_arms_idle_only_after_close_response_and_cancels_for_visible_page() -> None:
    now = [10.0]
    page = Page()
    context = Context([page])
    runtime = PatchrightRuntime(profile_dir=Path("/tmp/patchright-idle"), clock=lambda: now[0])
    runtime.browser_or_context = context
    runtime.pages["thread"] = __import__("surf_agent.backends.bridge_common", fromlist=["PageSlot"]).PageSlot(page=page, page_token=1)

    assert runtime.call("close", {"thread": "thread"}) == "closed\n"
    assert runtime.idle_shutdown_deadline is None
    runtime.after_response("close")
    assert runtime.idle_shutdown_deadline == 12.0

    context.pages.append(Page())
    now[0] = 13.0
    assert runtime.service_actions() is False
    assert context.closed is False


def test_patchright_service_action_stops_empty_context_at_deadline() -> None:
    now = [0.0]
    page = Page()
    context = Context([page])
    runtime = PatchrightRuntime(profile_dir=Path("/tmp/patchright-idle"), clock=lambda: now[0])
    runtime.browser_or_context = context
    runtime.pages["thread"] = __import__("surf_agent.backends.bridge_common", fromlist=["PageSlot"]).PageSlot(page=page, page_token=1)
    runtime.call("close", {"thread": "thread"})
    runtime.after_response("close")
    now[0] = 3.0
    assert runtime.service_actions() is True
    assert runtime.browser_or_context is None


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
