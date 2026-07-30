from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from patchright.sync_api import Browser, sync_playwright

from surf_agent.backends.bridge_common import PageSlot
from surf_agent.backends.patchright import bridge
from surf_agent.backends.patchright.bridge import PatchrightRuntime
from surf_agent.backends.patchright.owned_pages import PatchrightOwnedPageOperations


@pytest.fixture
def headless_chrome() -> Browser:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield browser
        finally:
            browser.close()


class FakePage:
    def __init__(
        self,
        url: str = "about:blank",
        *,
        target_id: str = "page-target",
        inspection_state: str | None = None,
    ) -> None:
        self.url = url
        self.target_id = target_id
        self.inspection_state = inspection_state
        self.closed = False
        self.goto_calls: list[str] = []
        self.evaluate_calls: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append(url)
        self.url = "https://www.reddit.com/" if url == "https://reddit.com" else url

    def evaluate(self, source: str) -> dict[str, str]:
        self.evaluate_calls.append(source)
        state = self.inspection_state
        if state is None:
            state = "session" if "/c/" in self.url else "pre_session"
        return {"state": state}


class FakeSession:
    def __init__(self, context: "FakeContext", page: FakePage) -> None:
        self.context = context
        self.page = page
        self.detached = False

    def send(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
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


class ProgrammedPage(FakePage):
    def __init__(self, url: str, results: dict[str, object]) -> None:
        super().__init__(url)
        self.results = results
        self.before_evaluate = None

    def evaluate(self, source: str) -> object:
        self.evaluate_calls.append(source)
        if self.before_evaluate is not None:
            self.before_evaluate(source)
        result = self.results[source]
        if isinstance(result, Exception):
            raise result
        return result


def test_owned_allocation_creates_a_background_window_without_touching_unrelated_pages(
    tmp_path: Path,
) -> None:
    unrelated_page = FakePage("https://user.test/", target_id="user-target")
    context = FakeContext(
        [unrelated_page],
        created_url="https://chatgpt.com/",
        created_target_id="chatgpt-target",
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    raw = runtime.call(
        "owned-allocate",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-login",
            "url": "https://chatgpt.com/",
            "allowed_scope": "chatgpt_pre_session",
            "expected_protection": "human_intervention",
            "protection": "human_intervention",
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "surf-chatgpt-login",
            "page_token": 1,
            "url": "https://chatgpt.com/",
        },
    }
    assert context.cdp_calls == [
        (
            "Target.createTarget",
            {
                "url": "https://chatgpt.com/",
                "newWindow": True,
                "background": True,
            },
        )
    ]
    assert unrelated_page.closed is False
    assert unrelated_page.goto_calls == []
    slot = runtime.pages["surf-chatgpt-login"]
    assert slot.page is context.created_page
    assert slot.owner == "surf-chatgpt"
    assert slot.protection == "human_intervention"


def test_owned_allocation_rejects_a_created_page_that_redirected_outside_scope(
    tmp_path: Path,
) -> None:
    unrelated_page = FakePage("https://user.test/", target_id="user-target")
    context = FakeContext(
        [unrelated_page],
        created_url="https://chatgpt.com/c/unexpected-session",
        created_target_id="redirected-target",
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    with pytest.raises(RuntimeError, match="outside the allowed scope"):
        runtime.call(
            "owned-allocate",
            {
                "owner": "surf-chatgpt",
                "thread": "surf-chatgpt-login",
                "url": "https://chatgpt.com/",
                "allowed_scope": "chatgpt_pre_session",
                "expected_protection": "human_intervention",
                "protection": "human_intervention",
            },
        )

    assert "surf-chatgpt-login" not in runtime.pages
    assert context.created_page is not None
    assert context.created_page.closed is True
    assert unrelated_page.closed is False


def test_owned_allocation_reuses_the_exact_owned_thread_without_navigation(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/auth/login", target_id="login-target")
    context = FakeContext([page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context
    slot = PageSlot(
        page=page,
        page_token=3,
        owner="surf-chatgpt",
        protection="human_intervention",
    )
    runtime.pages["surf-chatgpt-login"] = slot

    raw = runtime.call(
        "owned-allocate",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-login",
            "url": "https://chatgpt.com/",
            "allowed_scope": "chatgpt_pre_session",
            "expected_protection": "human_intervention",
            "protection": "human_intervention",
        },
    )

    assert json.loads(raw)["page"]["page_token"] == 3
    assert runtime.pages["surf-chatgpt-login"] is slot
    assert slot.protection == "human_intervention"
    assert page.goto_calls == []
    assert context.cdp_calls == []


@pytest.mark.parametrize(
    ("owner", "url"),
    [
        ("different-owner", "https://chatgpt.com/"),
        ("surf-chatgpt", "https://example.com/private"),
    ],
)
def test_owned_allocation_conflict_preserves_the_existing_binding(
    owner: str,
    url: str,
    tmp_path: Path,
) -> None:
    page = FakePage(url, target_id="existing-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=3,
        owner=owner,
        protection="explicitly_retained",
    )
    runtime.pages["surf-chatgpt-login"] = slot

    raw = runtime.call(
        "owned-allocate",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-login",
            "url": "https://chatgpt.com/",
            "allowed_scope": "chatgpt_pre_session",
            "expected_protection": "human_intervention",
            "protection": "human_intervention",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages["surf-chatgpt-login"] is slot
    assert slot.protection == "explicitly_retained"
    assert page.closed is False
    assert page.goto_calls == []


def test_owned_allocation_cannot_overwrite_existing_protection_without_a_guard(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/", target_id="existing-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=3,
        owner="surf-chatgpt",
        protection="explicitly_retained",
    )
    runtime.pages["surf-chatgpt-login"] = slot

    raw = runtime.call(
        "owned-allocate",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-login",
            "url": "https://chatgpt.com/",
            "allowed_scope": "chatgpt_pre_session",
            "expected_protection": "human_intervention",
            "protection": "human_intervention",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert slot.protection == "explicitly_retained"
    assert page.closed is False
    assert page.goto_calls == []


def test_session_resolution_reuses_only_the_exact_live_deterministic_binding(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123", target_id="session-target")
    context = FakeContext([page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context
    slot = PageSlot(
        page=page,
        page_token=9,
        owner="surf-chatgpt",
        protection="explicitly_retained",
    )
    runtime.pages["surf-chatgpt-session-abc123"] = slot

    raw = runtime.call(
        "owned-resolve",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "surf-chatgpt-session-abc123",
            "page_token": 9,
            "url": "https://chatgpt.com/c/abc123",
        },
        "protection": "explicitly_retained",
    }
    assert runtime.pages == {"surf-chatgpt-session-abc123": slot}
    assert page.evaluate_calls == []
    assert page.goto_calls == []
    assert page.closed is False
    assert context.cdp_calls == []


@pytest.mark.parametrize(
    ("owner", "url"),
    [
        ("different-owner", "https://chatgpt.com/c/abc123"),
        ("surf-chatgpt", "https://chatgpt.com/c/different"),
    ],
)
def test_session_resolution_preserves_a_conflicting_deterministic_binding(
    owner: str,
    url: str,
    tmp_path: Path,
) -> None:
    page = FakePage(url, target_id="conflicting-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=9, owner=owner)
    runtime.pages["surf-chatgpt-session-abc123"] = slot

    raw = runtime.call(
        "owned-resolve",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages == {"surf-chatgpt-session-abc123": slot}
    assert runtime.browser_or_context is None
    assert page.evaluate_calls == []
    assert page.goto_calls == []
    assert page.closed is False


def test_session_resolution_adopts_one_unowned_restored_exact_url_only(
    tmp_path: Path,
) -> None:
    unrelated_page = FakePage("https://user.test/private", target_id="user-target")
    restored_page = FakePage(
        "https://chatgpt.com/c/abc123", target_id="restored-target"
    )
    context = FakeContext([unrelated_page, restored_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    raw = runtime.call(
        "owned-resolve",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "surf-chatgpt-session-abc123",
            "page_token": 1,
            "url": "https://chatgpt.com/c/abc123",
        },
        "protection": None,
    }
    adopted = runtime.pages["surf-chatgpt-session-abc123"]
    assert adopted.page is restored_page
    assert adopted.owner == "surf-chatgpt"
    assert restored_page.evaluate_calls == []
    assert restored_page.goto_calls == []
    assert restored_page.closed is False
    assert unrelated_page.evaluate_calls == []
    assert unrelated_page.goto_calls == []
    assert unrelated_page.closed is False
    assert context.cdp_calls == []


def test_session_resolution_creates_an_unfocused_owned_page_when_no_match_exists(
    tmp_path: Path,
) -> None:
    unrelated_page = FakePage("https://user.test/private", target_id="user-target")
    context = FakeContext(
        [unrelated_page],
        created_url="https://chatgpt.com/c/abc123",
        created_target_id="recovered-target",
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    raw = runtime.call(
        "owned-resolve",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
        },
    )

    assert json.loads(raw)["page"] == {
        "thread": "surf-chatgpt-session-abc123",
        "page_token": 1,
        "url": "https://chatgpt.com/c/abc123",
    }
    assert context.cdp_calls == [
        (
            "Target.createTarget",
            {
                "url": "https://chatgpt.com/c/abc123",
                "newWindow": True,
                "background": True,
            },
        )
    ]
    assert runtime.pages["surf-chatgpt-session-abc123"].page is context.created_page
    assert unrelated_page.evaluate_calls == []
    assert unrelated_page.goto_calls == []
    assert unrelated_page.closed is False


def test_session_resolution_rejects_multiple_restored_exact_matches_without_adoption(
    tmp_path: Path,
) -> None:
    first_match = FakePage(
        "https://chatgpt.com/c/abc123", target_id="first-restored-target"
    )
    second_match = FakePage(
        "https://chatgpt.com/c/abc123", target_id="second-restored-target"
    )
    unrelated_page = FakePage("https://user.test/private", target_id="user-target")
    context = FakeContext([first_match, unrelated_page, second_match])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    raw = runtime.call(
        "owned-resolve",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ambiguous_session_page"}
    assert runtime.pages == {}
    assert context.cdp_calls == []
    for page in (first_match, second_match, unrelated_page):
        assert page.evaluate_calls == []
        assert page.goto_calls == []
        assert page.closed is False


def test_owned_inspection_returns_only_exact_live_binding_metadata_without_page_mutation(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["preserved"] = PageSlot(
        page=page,
        page_token=4,
        owner="surf-chatgpt",
        protection="human_intervention",
    )

    raw = runtime.call(
        "owned-inspect",
        {
            "owner": "surf-chatgpt",
            "thread": "preserved",
            "allowed_scope": "chatgpt",
            "classifier": "() => ({state: 'session'})",
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "preserved",
            "page_token": 4,
            "url": "https://chatgpt.com/c/abc123",
        },
        "metadata": {"state": "session"},
    }
    assert page.closed is False
    assert page.goto_calls == []
    assert page.evaluate_calls == ["() => ({state: 'session'})"]
    assert runtime.browser_or_context is None


@pytest.mark.parametrize(
    ("owner", "url"),
    [
        ("different-owner", "https://chatgpt.com/c/abc123"),
        ("surf-chatgpt", "https://example.com/private"),
    ],
)
def test_owned_inspection_rejects_owner_or_scope_conflicts_without_page_mutation(
    owner: str,
    url: str,
    tmp_path: Path,
) -> None:
    page = FakePage(url, target_id="page-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=4, owner=owner)
    runtime.pages["preserved"] = slot

    raw = runtime.call(
        "owned-inspect",
        {
            "owner": "surf-chatgpt",
            "thread": "preserved",
            "allowed_scope": "chatgpt",
            "classifier": "() => ({state: 'session'})",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages["preserved"] is slot
    assert page.closed is False
    assert page.goto_calls == []


def test_owned_inspection_rejects_non_allow_listed_classifier_metadata(
    tmp_path: Path,
) -> None:
    page = FakePage(
        "https://chatgpt.com/",
        target_id="page-target",
        inspection_state="private-canary",
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["preserved"] = PageSlot(
        page=page,
        page_token=4,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        "owned-inspect",
        {
            "owner": "surf-chatgpt",
            "thread": "preserved",
            "allowed_scope": "chatgpt",
            "classifier": "() => ({state: 'private-canary'})",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "inspection_failed"}
    assert "private-canary" not in raw
    assert page.closed is False


def test_owned_attempt_classification_returns_only_allow_listed_state(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'generating'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "generating"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        "owned-classify-attempt",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "surf-chatgpt-session-abc123",
            "page_token": 8,
            "url": "https://chatgpt.com/c/abc123",
        },
        "metadata": {"state": "generating"},
    }
    assert page.evaluate_calls == [program]
    assert page.closed is False


def test_owned_explicit_result_extraction_allows_text_only_for_terminal_results(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'completed', text: 'Answer'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "completed", "text": "Answer"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        "owned-extract-result",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
        },
    )

    assert json.loads(raw)["metadata"] == {
        "state": "completed",
        "text": "Answer",
    }
    assert page.evaluate_calls == [program]


def test_owned_terminal_close_reclassifies_and_closes_one_exact_unprotected_page(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'completed'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "completed"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        "owned-close-terminal",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            "expected_state": "completed",
        },
    )

    assert json.loads(raw) == {"ok": True}
    assert page.evaluate_calls == [program]
    assert page.closed is True
    assert "surf-chatgpt-session-abc123" not in runtime.pages


@pytest.mark.parametrize(
    ("operation", "metadata", "extra_args"),
    [
        (
            "owned-classify-attempt",
            {"state": "generating", "text": "CANARY-response"},
            {},
        ),
        ("owned-classify-attempt", {"state": "unrecognized"}, {}),
        (
            "owned-extract-result",
            {"state": "generating", "text": "CANARY-unstable-response"},
            {},
        ),
        (
            "owned-close-terminal",
            {"state": "completed", "text": "CANARY-cleanup-content"},
            {"expected_state": "completed"},
        ),
    ],
)
def test_owned_observation_operations_reject_unexpected_or_content_bearing_metadata(
    operation: str,
    metadata: dict[str, str],
    extra_args: dict[str, str],
    tmp_path: Path,
) -> None:
    program = "private-canary-program"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: metadata},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        operation,
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            **extra_args,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "inspection_failed"}
    assert "CANARY" not in raw
    assert page.closed is False
    assert "surf-chatgpt-session-abc123" in runtime.pages


def test_owned_terminal_close_preserves_page_replaced_during_classification(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'completed'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "completed"}},
    )
    page.before_evaluate = lambda _: setattr(
        page, "url", "https://chatgpt.com/c/replaced"
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        "owned-close-terminal",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            "expected_state": "completed",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert page.closed is False
    assert "surf-chatgpt-session-abc123" in runtime.pages


def test_owned_terminal_close_never_closes_a_protected_page(tmp_path: Path) -> None:
    program = "() => ({state: 'completed'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "completed"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-session-abc123"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
        protection="explicitly_retained",
    )

    raw = runtime.call(
        "owned-close-terminal",
        {
            "owner": "surf-chatgpt",
            "thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 8,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": "explicitly_retained",
            "program": program,
            "expected_state": "completed",
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert page.evaluate_calls == []
    assert page.closed is False


def test_owned_rebind_atomically_moves_the_same_live_page_without_browser_mutation(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=6, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-rebind",
        {
            "owner": "surf-chatgpt",
            "source_thread": "temporary",
            "destination_thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 6,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
        },
    )

    assert json.loads(raw)["page"]["thread"] == "surf-chatgpt-session-abc123"
    assert "temporary" not in runtime.pages
    assert runtime.pages["surf-chatgpt-session-abc123"] is slot
    assert runtime.pages["surf-chatgpt-session-abc123"].page is page
    assert page.closed is False
    assert page.goto_calls == []
    assert runtime.browser_or_context is None


def test_owned_rebind_preserves_live_dom_and_in_flight_page_activity(
    headless_chrome: Browser,
) -> None:
    context = headless_chrome.new_context()
    page = context.new_page()
    page.route(
        "https://chatgpt.com/c/abc123",
        lambda route: route.fulfill(
            status=200,
            body='<main id="dom-canary">preserved</main>',
            content_type="text/html",
        ),
    )
    page.goto("https://chatgpt.com/c/abc123")
    page.evaluate(
        """() => {
          window.inFlightActivity = new Promise((resolve) => {
            window.completeActivity = resolve;
          }).then((result) => { window.activityResult = result; });
        }"""
    )
    # Construct only the synchronous registry host. Patchright's sync API already
    # owns this thread's event loop, so a second asyncio Runner would deadlock it.
    runtime = object.__new__(PatchrightRuntime)
    runtime.pages = {}
    runtime.owned_pages = PatchrightOwnedPageOperations(runtime)
    slot = PageSlot(page=page, page_token=6, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    try:
        runtime.owned_pages.rebind(
            {
                "owner": "surf-chatgpt",
                "source_thread": "temporary",
                "destination_thread": "surf-chatgpt-session-abc123",
                "expected_page_token": 6,
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "expected_protection": None,
            }
        )
        activity_result = page.evaluate(
            """async () => {
              window.completeActivity('continued');
              await window.inFlightActivity;
              return window.activityResult;
            }"""
        )

        rebound = runtime.pages["surf-chatgpt-session-abc123"]
        assert rebound is slot
        assert rebound.page is page
        assert page.url == "https://chatgpt.com/c/abc123"
        assert page.locator("#dom-canary").text_content() == "preserved"
        assert activity_result == "continued"
    finally:
        context.close()


def test_owned_rebind_replay_returns_the_same_destination_page_without_mutation(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=6,
        owner="surf-chatgpt",
        protection="explicitly_retained",
        send_may_have_occurred=True,
    )
    runtime.pages["temporary"] = slot
    request = {
        "owner": "surf-chatgpt",
        "source_thread": "temporary",
        "destination_thread": "surf-chatgpt-session-abc123",
        "expected_page_token": 6,
        "expected_exact_url": "https://chatgpt.com/c/abc123",
        "allowed_scope": "chatgpt",
        "expected_protection": "explicitly_retained",
    }

    first = runtime.call("owned-rebind", request)
    replay = runtime.call("owned-rebind", request)

    assert json.loads(replay) == json.loads(first)
    assert runtime.pages == {"surf-chatgpt-session-abc123": slot}
    assert slot.send_may_have_occurred is False
    assert page.closed is False
    assert page.goto_calls == []


def test_owned_submission_preparation_returns_only_requested_picker_labels(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'ready', selection: {model: 'GPT-5.6'}})"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {program: {"state": "ready", "selection": {"model": "GPT-5.6"}}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=12, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-prepare-submission",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            "requested_selection_dimensions": ["model"],
        },
    )

    assert json.loads(raw) == {
        "ok": True,
        "page": {
            "thread": "temporary",
            "page_token": 12,
            "url": "https://chatgpt.com/",
        },
        "metadata": {
            "state": "ready",
            "selection": {"model": "GPT-5.6"},
        },
    }
    assert slot.send_may_have_occurred is False
    assert page.evaluate_calls == [program]


def test_owned_prompt_submission_sets_irreversible_marker_before_prompt_mutation(
    tmp_path: Path,
) -> None:
    readiness_program = "() => ({state: 'ready', selection: {}})"
    submission_program = "() => ({state: 'submitted'})"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {
            readiness_program: {"state": "ready", "selection": {}},
            submission_program: {"state": "submitted"},
        },
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=12, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot
    marker_at_evaluation: dict[str, bool] = {}
    page.before_evaluate = lambda source: marker_at_evaluation.setdefault(
        source, slot.send_may_have_occurred
    )

    raw = runtime.call(
        "owned-submit-prompt",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "readiness_program": readiness_program,
            "submission_program": submission_program,
        },
    )

    assert json.loads(raw)["metadata"] == {"state": "submitted"}
    assert marker_at_evaluation == {
        readiness_program: False,
        submission_program: True,
    }
    assert slot.send_may_have_occurred is True


def test_owned_prompt_submission_does_not_mark_or_mutate_when_readiness_is_lost(
    tmp_path: Path,
) -> None:
    readiness_program = "() => ({state: 'challenge'})"
    submission_program = "() => ({state: 'submitted'})"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {
            readiness_program: {"state": "challenge"},
            submission_program: {"state": "submitted"},
        },
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=12, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-submit-prompt",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "readiness_program": readiness_program,
            "submission_program": submission_program,
        },
    )

    assert json.loads(raw)["metadata"] == {"state": "challenge"}
    assert slot.send_may_have_occurred is False
    assert page.evaluate_calls == [readiness_program]


def test_owned_submission_retry_is_rejected_without_running_browser_code(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'ready', selection: {}})"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {program: {"state": "ready", "selection": {}}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=12,
        owner="surf-chatgpt",
        send_may_have_occurred=True,
    )
    runtime.pages["temporary"] = slot
    common = {
        "owner": "surf-chatgpt",
        "thread": "temporary",
        "expected_page_token": 12,
        "allowed_scope": "chatgpt",
        "expected_protection": None,
    }

    preparation = runtime.call(
        "owned-prepare-submission",
        {
            **common,
            "program": program,
            "requested_selection_dimensions": [],
        },
    )
    submission = runtime.call(
        "owned-submit-prompt",
        {
            **common,
            "readiness_program": program,
            "submission_program": "() => ({state: 'submitted'})",
        },
    )

    assert json.loads(preparation) == {
        "ok": False,
        "error": "submission_already_attempted",
    }
    assert json.loads(submission) == {
        "ok": False,
        "error": "submission_already_attempted",
    }
    assert slot.send_may_have_occurred is True
    assert page.evaluate_calls == []


def test_owned_submission_failure_never_clears_marker_or_exposes_browser_details(
    tmp_path: Path,
) -> None:
    readiness_program = "() => ({state: 'ready', selection: {}})"
    submission_program = "private prompt program"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {
            readiness_program: {"state": "ready", "selection": {}},
            submission_program: RuntimeError("private DOM details"),
        },
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=12, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-submit-prompt",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "readiness_program": readiness_program,
            "submission_program": submission_program,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "inspection_failed"}
    assert "private" not in raw
    assert slot.send_may_have_occurred is True


def test_owned_post_marker_gate_is_returned_without_clearing_submission_barrier(
    tmp_path: Path,
) -> None:
    readiness_program = "() => ({state: 'ready', selection: {}})"
    submission_program = "() => ({state: 'ui_changed'})"
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {
            readiness_program: {"state": "ready", "selection": {}},
            submission_program: {"state": "ui_changed"},
        },
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=12, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-submit-prompt",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "readiness_program": readiness_program,
            "submission_program": submission_program,
        },
    )

    assert json.loads(raw)["metadata"] == {"state": "ui_changed"}
    assert slot.send_may_have_occurred is True


@pytest.mark.parametrize(
    ("operation", "args", "program"),
    [
        (
            "owned-prepare-submission",
            {"requested_selection_dimensions": []},
            "prepare",
        ),
        (
            "owned-observe-assignment",
            {"completion_exact_url": None},
            "observe",
        ),
    ],
)
def test_owned_submission_operations_reject_non_allow_listed_browser_metadata(
    operation: str,
    args: dict[str, object],
    program: str,
    tmp_path: Path,
) -> None:
    page = ProgrammedPage(
        "https://chatgpt.com/",
        {program: {"state": "ui_changed", "private_dom": "secret"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["temporary"] = PageSlot(
        page=page,
        page_token=12,
        owner="surf-chatgpt",
    )

    raw = runtime.call(
        operation,
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            **args,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "inspection_failed"}
    assert "private" not in raw


def test_owned_assignment_observation_can_complete_a_follow_up_submission_barrier(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'session', session_id: 'abc123'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "session", "session_id": "abc123"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=12,
        owner="surf-chatgpt",
        send_may_have_occurred=True,
    )
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-observe-assignment",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            "completion_exact_url": "https://chatgpt.com/c/abc123",
        },
    )

    assert json.loads(raw)["metadata"] == {
        "state": "session",
        "session_id": "abc123",
    }
    assert slot.send_may_have_occurred is False
    assert page.closed is False
    assert page.goto_calls == []


def test_owned_assignment_gate_preserves_only_allow_listed_session_identity(
    tmp_path: Path,
) -> None:
    program = "() => ({state: 'challenge', session_id: 'abc123'})"
    page = ProgrammedPage(
        "https://chatgpt.com/c/abc123",
        {program: {"state": "challenge", "session_id": "abc123"}},
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=12,
        owner="surf-chatgpt",
        send_may_have_occurred=True,
    )
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-observe-assignment",
        {
            "owner": "surf-chatgpt",
            "thread": "temporary",
            "expected_page_token": 12,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
            "program": program,
            "completion_exact_url": "https://chatgpt.com/c/abc123",
        },
    )

    assert json.loads(raw)["metadata"] == {
        "state": "challenge",
        "session_id": "abc123",
    }
    assert slot.send_may_have_occurred is True


@pytest.mark.parametrize(
    ("expected_page_token", "expected_exact_url"),
    [
        (5, "https://chatgpt.com/c/abc123"),
        (6, "https://chatgpt.com/c/different"),
    ],
)
def test_owned_rebind_rejects_stale_identity_guards_without_mutation(
    expected_page_token: int,
    expected_exact_url: str,
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=6, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-rebind",
        {
            "owner": "surf-chatgpt",
            "source_thread": "temporary",
            "destination_thread": "surf-chatgpt-session-abc123",
            "expected_page_token": expected_page_token,
            "expected_exact_url": expected_exact_url,
            "allowed_scope": "chatgpt",
            "expected_protection": None,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages == {"temporary": slot}
    assert page.closed is False
    assert page.goto_calls == []


@pytest.mark.parametrize(
    ("url", "protection", "expected_protection"),
    [
        ("https://example.com/c/abc123", None, None),
        ("https://chatgpt.com/c/abc123?private=canary", None, None),
        ("https://chatgpt.com/c/abc123?", None, None),
        ("https://chatgpt.com/c/abc123#", None, None),
        ("HTTPS://CHATGPT.COM/c/abc123", None, None),
        (
            "https://chatgpt.com/c/abc123",
            "explicitly_retained",
            "human_intervention",
        ),
    ],
)
def test_owned_rebind_rejects_out_of_scope_or_changed_protection_without_mutation(
    url: str,
    protection: str | None,
    expected_protection: str | None,
    tmp_path: Path,
) -> None:
    page = FakePage(url, target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=6,
        owner="surf-chatgpt",
        protection=protection,
    )
    runtime.pages["temporary"] = slot

    raw = runtime.call(
        "owned-rebind",
        {
            "owner": "surf-chatgpt",
            "source_thread": "temporary",
            "destination_thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 6,
            "expected_exact_url": url,
            "allowed_scope": "chatgpt",
            "expected_protection": expected_protection,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages["temporary"] is slot
    assert "surf-chatgpt-session-abc123" not in runtime.pages
    assert page.closed is False


def test_owned_rebind_conflict_leaves_both_bindings_unchanged(tmp_path: Path) -> None:
    source_page = FakePage("https://chatgpt.com/c/abc123", target_id="source-target")
    destination_page = FakePage(
        "https://chatgpt.com/c/different", target_id="destination-target"
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    source_slot = PageSlot(page=source_page, page_token=6, owner="surf-chatgpt")
    destination_slot = PageSlot(
        page=destination_page,
        page_token=7,
        owner="surf-chatgpt",
    )
    runtime.pages["temporary"] = source_slot
    runtime.pages["surf-chatgpt-session-abc123"] = destination_slot

    raw = runtime.call(
        "owned-rebind",
        {
            "owner": "surf-chatgpt",
            "source_thread": "temporary",
            "destination_thread": "surf-chatgpt-session-abc123",
            "expected_page_token": 6,
            "expected_exact_url": "https://chatgpt.com/c/abc123",
            "allowed_scope": "chatgpt",
            "expected_protection": None,
        },
    )

    assert json.loads(raw) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages["temporary"] is source_slot
    assert runtime.pages["surf-chatgpt-session-abc123"] is destination_slot
    assert source_page.closed is False
    assert destination_page.closed is False


def test_owned_rebind_competing_sources_never_duplicate_or_replace_destination(
    tmp_path: Path,
) -> None:
    winning_page = FakePage("https://chatgpt.com/c/abc123")
    losing_page = FakePage("https://chatgpt.com/c/abc123")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    winning_slot = PageSlot(page=winning_page, page_token=6, owner="surf-chatgpt")
    losing_slot = PageSlot(page=losing_page, page_token=7, owner="surf-chatgpt")
    runtime.pages.update({"first": winning_slot, "second": losing_slot})

    common = {
        "owner": "surf-chatgpt",
        "destination_thread": "surf-chatgpt-session-abc123",
        "expected_exact_url": "https://chatgpt.com/c/abc123",
        "allowed_scope": "chatgpt",
        "expected_protection": None,
    }
    winner = runtime.call(
        "owned-rebind",
        {**common, "source_thread": "first", "expected_page_token": 6},
    )
    loser = runtime.call(
        "owned-rebind",
        {**common, "source_thread": "second", "expected_page_token": 7},
    )

    assert json.loads(winner)["ok"] is True
    assert json.loads(loser) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages == {
        "second": losing_slot,
        "surf-chatgpt-session-abc123": winning_slot,
    }
    assert len({id(slot) for slot in runtime.pages.values()}) == 2


def test_owned_rebind_competing_destinations_move_source_exactly_once(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/c/abc123")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(page=page, page_token=6, owner="surf-chatgpt")
    runtime.pages["temporary"] = slot
    common = {
        "owner": "surf-chatgpt",
        "source_thread": "temporary",
        "expected_page_token": 6,
        "expected_exact_url": "https://chatgpt.com/c/abc123",
        "allowed_scope": "chatgpt",
        "expected_protection": None,
    }

    winner = runtime.call(
        "owned-rebind",
        {**common, "destination_thread": "surf-chatgpt-session-abc123"},
    )
    loser = runtime.call(
        "owned-rebind",
        {**common, "destination_thread": "surf-chatgpt-session-other"},
    )

    assert json.loads(winner)["ok"] is True
    assert json.loads(loser) == {"ok": False, "error": "ownership_conflict"}
    assert runtime.pages == {"surf-chatgpt-session-abc123": slot}
    assert sum(bound is slot for bound in runtime.pages.values()) == 1


def test_owned_protection_update_revalidates_live_identity_and_current_metadata(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    slot = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
        protection="human_intervention",
    )
    runtime.pages["preserved"] = slot

    raw = runtime.call(
        "owned-protect",
        {
            "owner": "surf-chatgpt",
            "thread": "preserved",
            "expected_page_token": 8,
            "allowed_scope": "chatgpt",
            "expected_protection": "human_intervention",
            "protection": "explicitly_retained",
        },
    )

    assert json.loads(raw) == {"ok": True}
    assert slot.protection == "explicitly_retained"
    assert page.closed is False
    assert page.goto_calls == []

    stale = runtime.call(
        "owned-protect",
        {
            "owner": "surf-chatgpt",
            "thread": "preserved",
            "expected_page_token": 8,
            "allowed_scope": "chatgpt",
            "expected_protection": "human_intervention",
            "protection": None,
        },
    )
    assert json.loads(stale) == {"ok": False, "error": "ownership_conflict"}
    assert slot.protection == "explicitly_retained"


def test_owner_and_protection_metadata_never_leave_bridge_memory(
    tmp_path: Path,
) -> None:
    page = FakePage("https://chatgpt.com/", target_id="chatgpt-target")
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.pages["surf-chatgpt-login"] = PageSlot(
        page=page,
        page_token=8,
        owner="surf-chatgpt",
        protection="human_intervention",
        send_may_have_occurred=True,
    )

    state = json.loads(runtime.call("state", {"thread": "surf-chatgpt-login"}))
    listing = json.loads(runtime.call("list", {}))

    assert "owner" not in state
    assert "protection" not in state
    assert all("owner" not in row for row in listing["pages"])
    assert all("protection" not in row for row in listing["pages"])
    assert "send_may_have_occurred" not in state
    assert all("send_may_have_occurred" not in row for row in listing["pages"])

    runtime._run(runtime._stop_async())
    assert runtime.pages == {}


def test_first_thread_adopts_clean_startup_page_and_closes_restored_page(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class EventPage(FakePage):
        def close(self) -> None:
            events.append(f"close:{self.target_id}")
            super().close()

        def goto(self, url: str, wait_until: str | None = None) -> None:
            events.append(f"goto:{self.target_id}:{url}")
            super().goto(url, wait_until)

    restored_page = EventPage("https://www.reddit.com/", target_id="restored")
    startup_page = EventPage("about:blank", target_id="startup")
    context = FakeContext([restored_page, startup_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://reddit.com"))

    assert slot.page is startup_page
    assert slot.page.url == "https://www.reddit.com/"
    assert startup_page.goto_calls == ["https://reddit.com"]
    assert restored_page.closed is True
    assert startup_page.closed is False
    assert events == ["close:restored", "goto:startup:https://reddit.com"]
    assert context.cdp_calls == []


def test_first_thread_adopts_first_of_multiple_startup_pages(tmp_path: Path) -> None:
    first_startup = FakePage("about:blank", target_id="startup-one")
    second_startup = FakePage("chrome://newtab/", target_id="startup-two")
    context = FakeContext(
        [first_startup, second_startup], created_target_id="created-target"
    )
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert slot.page is first_startup
    assert first_startup.goto_calls == ["https://welcome.test/"]
    assert first_startup.closed is False
    assert second_startup.closed is True
    assert context.create_target_calls == 0
    assert context.cdp_calls == []


def test_first_thread_adopts_first_restored_page_when_no_startup_page_exists(
    tmp_path: Path,
) -> None:
    first_restored = FakePage("https://first-restored.test/", target_id="restored-one")
    second_restored = FakePage(
        "https://second-restored.test/", target_id="restored-two"
    )
    context = FakeContext([first_restored, second_restored])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert slot.page is first_restored
    assert first_restored.goto_calls == ["https://welcome.test/"]
    assert first_restored.closed is False
    assert second_restored.closed is True
    assert context.cdp_calls == []


def test_first_thread_navigates_adopted_page_to_about_blank(tmp_path: Path) -> None:
    restored_page = FakePage("https://restored.test/", target_id="restored")
    context = FakeContext([restored_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    slot = runtime._run(runtime._new_page("thread", url="about:blank"))

    assert slot.page is restored_page
    assert restored_page.goto_calls == ["about:blank"]
    assert restored_page.url == "about:blank"
    assert context.cdp_calls == []


def test_closed_adopted_page_restarts_without_creating_replacement_window(
    monkeypatch, tmp_path: Path
) -> None:
    startup_page = FakePage("about:blank", target_id="startup")

    class ClosingRestoredPage(FakePage):
        def close(self) -> None:
            super().close()
            startup_page.closed = True

    restored_page = ClosingRestoredPage("https://restored.test/", target_id="restored")
    context = FakeContext([startup_page, restored_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    async def restart_closed_context() -> None:
        raise RuntimeError("restart requested")

    monkeypatch.setattr(runtime, "_restart_closed_context", restart_closed_context)

    with pytest.raises(RuntimeError, match="restart requested"):
        runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert restored_page.closed is True
    assert startup_page.goto_calls == []
    assert context.create_target_calls == 0
    assert context.cdp_calls == []


def test_closed_adopted_page_during_navigation_restarts_without_creating_replacement_window(
    monkeypatch, tmp_path: Path
) -> None:
    class ClosingStartupPage(FakePage):
        def goto(self, url: str, wait_until: str | None = None) -> None:
            super().goto(url, wait_until)
            self.closed = True
            raise RuntimeError("navigation interrupted")

    startup_page = ClosingStartupPage("about:blank", target_id="startup")
    context = FakeContext([startup_page])
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile")
    runtime.browser_or_context = context

    async def restart_closed_context() -> None:
        raise RuntimeError("restart requested")

    monkeypatch.setattr(runtime, "_restart_closed_context", restart_closed_context)

    with pytest.raises(RuntimeError, match="restart requested"):
        runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

    assert startup_page.goto_calls == ["https://welcome.test/"]
    assert context.create_target_calls == 0
    assert context.cdp_calls == []


def test_redirected_new_window_is_selected_by_exact_target_id_after_unrelated_race(
    monkeypatch, tmp_path: Path
) -> None:
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
            context.created_page = FakePage(
                "https://www.reddit.com/", target_id="created-target"
            )
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


def test_unrelated_url_matching_sole_candidate_is_not_claimed(
    monkeypatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(
        bridge, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values))
    )

    with pytest.raises(RuntimeError, match="created-target"):
        runtime._run(runtime._new_page("thread", url=requested_url))

    assert unrelated.closed is False
    assert context.closed_target_ids == ["created-target"]
    assert [session.page.target_id for session in context.detached_sessions] == [
        "user-target",
        "anchor-1",
    ]
    assert all(session.detached for session in context.detached_sessions)


def test_failed_target_wait_closes_exact_target_and_detaches_anchor(
    monkeypatch, tmp_path: Path
) -> None:
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
        (
            "Target.createTarget",
            {"url": "https://missing.test/", "newWindow": True, "background": False},
        ),
        ("Target.closeTarget", {"targetId": "created-target"}),
    ]
    assert context.closed_target_ids == ["created-target"]
    assert len(context.detached_sessions) == 1
    assert context.detached_sessions[0].detached is True
    assert context.pages[0].closed is True


def test_candidate_target_probe_returns_none_for_page_closed_during_probe(
    monkeypatch, tmp_path: Path
) -> None:
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
