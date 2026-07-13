from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from surf_agent.backends.bridge_common import PageSlot
from surf_agent.backends.patchright import bridge
from surf_agent.backends.patchright.bridge import PatchrightRuntime


class FakePage:
    def __init__(self, url: str = "about:blank", *, target_id: str = "page-target") -> None:
        self.url = url
        self.target_id = target_id
        self.closed = False
        self.goto_calls: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append(url)
        self.url = "https://www.reddit.com/" if url == "https://reddit.com" else url


class FakeSession:
    def __init__(self, context: "FakeContext", page: FakePage) -> None:
        self.context = context
        self.page = page
        self.detached = False

    def send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        params = params or {}
        self.context.session_calls.append((self.page, method, params))
        if method == "Target.createTarget":
            self.context.cdp_calls.append((method, params))
            self.context.create_target_calls += 1
            if self.context.on_create_target is not None:
                self.context.on_create_target(self.context, params)
            if self.context.create_page_on_target:
                created_page = FakePage(
                    self.context.created_url or str(params["url"]),
                    target_id=self.context.created_target_id,
                )
                self.context.pages.append(created_page)
                self.context.created_page = created_page
            return {"targetId": self.context.created_target_id}
        if method == "Target.getTargetInfo":
            if self.page.closed:
                raise RuntimeError(bridge.CLOSED_TARGET_MESSAGE)
            return {"targetInfo": {"targetId": self.page.target_id}}
        if method == "Target.closeTarget":
            self.context.cdp_calls.append((method, params))
            self.context.closed_target_ids.append(params["targetId"])
            return {"success": True}
        raise AssertionError(f"unexpected CDP method: {method}")

    def detach(self) -> None:
        self.detached = True
        self.context.detached_sessions.append(self)


class FakeContext:
    def __init__(
        self,
        pages: list[FakePage],
        *,
        created_url: str | None = None,
        created_target_id: str = "target-1",
        create_page_on_target: bool = True,
        on_create_target=None,
    ) -> None:
        self.pages = pages
        self.created_url = created_url
        self.created_target_id = created_target_id
        self.create_page_on_target = create_page_on_target
        self.on_create_target = on_create_target
        self.created_page: FakePage | None = None
        self.create_target_calls = 0
        self.cdp_calls: list[tuple[str, dict[str, object]]] = []
        self.session_calls: list[tuple[FakePage, str, dict[str, object]]] = []
        self.detached_sessions: list[FakeSession] = []
        self.closed_target_ids: list[object] = []
        self.new_page_calls = 0

    def new_page(self) -> FakePage:
        self.new_page_calls += 1
        page = FakePage(target_id=f"anchor-{self.new_page_calls}")
        self.pages.append(page)
        return page

    def new_cdp_session(self, page: FakePage) -> FakeSession:
        return FakeSession(self, page)


def test_first_thread_reuses_launch_startup_window(tmp_path: Path) -> None:
    startup_page = FakePage()
    context = FakeContext([startup_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://reddit.com"))

    assert slot.page is startup_page
    assert slot.page.url == "https://www.reddit.com/"
    assert startup_page.goto_calls == ["https://reddit.com"]
    assert startup_page.closed is False
    assert context.pages == [startup_page]
    assert context.cdp_calls == []


def test_multiple_startup_pages_are_not_adopted(tmp_path: Path) -> None:
    first_startup = FakePage("about:blank", target_id="startup-one")
    second_startup = FakePage("chrome://newtab/", target_id="startup-two")
    context = FakeContext([first_startup, second_startup], created_target_id="created-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert slot.page is context.created_page
    assert slot.page not in {first_startup, second_startup}
    assert first_startup.closed is True
    assert second_startup.closed is True
    assert context.create_target_calls == 1
    assert context.cdp_calls == [
        ("Target.createTarget", {"url": "https://welcome.test/", "newWindow": True, "background": False})
    ]


def test_multiple_startup_pages_clean_restored_pages_and_keep_new_target(tmp_path: Path) -> None:
    first_startup = FakePage("about:blank", target_id="startup-one")
    second_startup = FakePage("chrome://newtab/", target_id="startup-two")
    restored = FakePage("https://restored.test/", target_id="restored-target")
    context = FakeContext([first_startup, second_startup, restored], created_target_id="created-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert slot.page is context.created_page
    assert slot.page.closed is False
    assert all(page.closed for page in (first_startup, second_startup, restored))
    assert context.created_page in context.pages
    assert context.create_target_calls == 1


def test_redirected_new_window_is_selected_by_exact_target_id_after_unrelated_race(monkeypatch, tmp_path: Path) -> None:
    owned_page = FakePage("https://owned.test/", target_id="owned-target")

    def add_unrelated_page(context: FakeContext, _params: dict[str, object]) -> None:
        context.pages.append(FakePage("https://user.test/", target_id="user-target"))

    context = FakeContext(
        [owned_page],
        created_url="https://www.reddit.com/",
        created_target_id="created-target",
        create_page_on_target=False,
        on_create_target=add_unrelated_page,
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context
    runtime.pages["owned"] = PageSlot(page=owned_page, page_token=1)

    sleep_calls = 0

    async def release_created_page(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            context.created_page = FakePage("https://www.reddit.com/", target_id="created-target")
            context.pages.append(context.created_page)

    monkeypatch.setattr(bridge.asyncio, "sleep", release_created_page)

    slot = runtime._run(runtime._new_page("thread", url="https://reddit.com"))

    unrelated = next(page for page in context.pages if page.target_id == "user-target")
    assert slot.page is context.created_page
    assert slot.page.url == "https://www.reddit.com/"
    assert unrelated.closed is False
    candidate_sessions = [
        session
        for session in context.detached_sessions
        if session.page.target_id in {"user-target", "created-target"}
    ]
    assert candidate_sessions
    assert all(session.detached for session in candidate_sessions)


def test_unrelated_url_matching_sole_candidate_is_not_claimed(monkeypatch, tmp_path: Path) -> None:
    requested_url = "https://requested.test/"
    unrelated = FakePage(requested_url, target_id="user-target")

    def add_unrelated_page(context: FakeContext, _params: dict[str, object]) -> None:
        context.pages.append(unrelated)

    context = FakeContext(
        [],
        created_target_id="created-target",
        create_page_on_target=False,
        on_create_target=add_unrelated_page,
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context
    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(bridge, "CDP_NEW_WINDOW_TIMEOUT_S", 1)
    monkeypatch.setattr(bridge, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values)))

    with pytest.raises(RuntimeError, match="created-target"):
        runtime._run(runtime._new_page("thread", url=requested_url))

    assert unrelated.closed is False
    assert context.closed_target_ids == ["created-target"]
    assert [session.page.target_id for session in context.detached_sessions] == ["user-target", "anchor-1"]
    assert all(session.detached for session in context.detached_sessions)


def test_failed_target_wait_closes_exact_target_and_detaches_anchor(monkeypatch, tmp_path: Path) -> None:
    context = FakeContext(
        [],
        created_target_id="created-target",
        create_page_on_target=False,
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context
    monkeypatch.setattr(bridge, "CDP_NEW_WINDOW_TIMEOUT_S", 0)

    with pytest.raises(RuntimeError, match="https://missing.test/"):
        runtime._run(runtime._new_page("thread", url="https://missing.test/"))

    assert context.cdp_calls == [
        ("Target.createTarget", {"url": "https://missing.test/", "newWindow": True, "background": False}),
        ("Target.closeTarget", {"targetId": "created-target"}),
    ]
    assert context.closed_target_ids == ["created-target"]
    assert len(context.detached_sessions) == 1
    assert context.detached_sessions[0].detached is True
    assert context.pages[0].closed is True


def test_candidate_target_probe_returns_none_for_page_closed_during_probe(monkeypatch, tmp_path: Path) -> None:
    page = FakePage("https://closing.test/", target_id="closing-target")
    context = FakeContext([page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    original_send = FakeSession.send

    def close_before_probe(session: FakeSession, method: str, params=None):
        if method == "Target.getTargetInfo":
            page.closed = True
        return original_send(session, method, params)

    monkeypatch.setattr(FakeSession, "send", close_before_probe)
    target_id = runtime._run(runtime._page_target_id(context, page))

    assert target_id is None
    assert len(context.detached_sessions) == 1
    assert context.detached_sessions[0].detached is True
