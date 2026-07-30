from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from surf_agent.owned_pages import (
    AllocateOwnedPage,
    InspectOwnedPage,
    OwnedPageClassifier,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageScope,
    OwnedPageCapabilities,
    PatchrightOwnedPageBridge,
    ProtectOwnedPage,
    RebindOwnedPage,
    REQUIRED_OWNED_PAGE_CAPABILITIES,
    UnsupportedOwnedPageCapability,
    UnsupportedOwnedPageBridge,
    create_owned_page_bridge,
)


CLASSIFIER = OwnedPageClassifier("() => ({state: 'session'})")


class NoBrowserCallsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def call_tool(self, name: str, args: object = None) -> str:
        self.calls.append((name, args))
        raise AssertionError("capability checks must not start or inspect the browser")


def test_patchright_affirms_the_complete_owned_page_capability_contract_without_browser_work() -> (
    None
):
    client = NoBrowserCallsClient()
    bridge = PatchrightOwnedPageBridge(client)

    capabilities = bridge.capabilities()

    assert capabilities.available == REQUIRED_OWNED_PAGE_CAPABILITIES
    assert capabilities.supported is True
    assert client.calls == []


def test_unsupported_backend_affirms_no_owned_page_capability_without_browser_work() -> (
    None
):
    bridge = UnsupportedOwnedPageBridge()

    capabilities = bridge.capabilities()

    assert capabilities == OwnedPageCapabilities()


def test_axi_selection_returns_the_unsupported_bridge_without_touching_its_browser_client() -> (
    None
):
    client = NoBrowserCallsClient()
    bridge = create_owned_page_bridge(
        SimpleNamespace(backend="axi", patchright_client=client)
    )

    assert isinstance(bridge, UnsupportedOwnedPageBridge)
    assert bridge.capabilities() == OwnedPageCapabilities()
    assert client.calls == []


class ScriptedClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def call_tool(self, name: str, args: object = None) -> str:
        self.calls.append((name, args))
        return json.dumps(self.result)

    def call_tool_if_running(self, name: str, args: object = None) -> str:
        self.calls.append((name, args))
        return json.dumps(self.result)


def test_patchright_owned_allocation_uses_the_typed_bridge_transport() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-login",
                "page_token": 7,
                "url": "https://chatgpt.com/",
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
        expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
    )

    page = bridge.allocate(request)

    assert page == OwnedPageRef(
        thread="surf-chatgpt-login",
        page_token=7,
        exact_url="https://chatgpt.com/",
    )
    assert client.calls == [
        (
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
    ]


def test_unsupported_backend_rejects_allocation_without_browser_work() -> None:
    bridge = UnsupportedOwnedPageBridge()
    request = AllocateOwnedPage(
        owner="surf-chatgpt",
        thread="surf-chatgpt-login",
        url="https://chatgpt.com/",
        allowed_scope=OwnedPageScope.CHATGPT,
        expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
    )

    with pytest.raises(UnsupportedOwnedPageCapability):
        bridge.allocate(request)


def test_patchright_inspection_addresses_only_the_live_thread_without_starting_a_bridge() -> (
    None
):
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "preserved",
                "page_token": 9,
                "url": "https://chatgpt.com/c/abc123",
            },
            "metadata": {"state": "session"},
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    page = bridge.inspect(
        InspectOwnedPage(
            owner="surf-chatgpt",
            thread="preserved",
            allowed_scope=OwnedPageScope.CHATGPT,
            classifier=CLASSIFIER,
        )
    )

    assert page.page.exact_url == "https://chatgpt.com/c/abc123"
    assert page.state is OwnedPageInspectionState.SESSION
    assert client.calls == [
        (
            "owned-inspect",
            {
                "owner": "surf-chatgpt",
                "thread": "preserved",
                "allowed_scope": "chatgpt",
                "classifier": CLASSIFIER.source,
            },
        )
    ]


class StoppedBridgeClient(NoBrowserCallsClient):
    def call_tool_if_running(self, name: str, args: object = None) -> None:
        self.calls.append((name, args))
        return None


def test_patchright_inspection_of_a_stopped_bridge_is_thread_not_found_without_starting_it() -> (
    None
):
    client = StoppedBridgeClient()
    bridge = PatchrightOwnedPageBridge(client)

    with pytest.raises(OwnedPageNotFound):
        bridge.inspect(
            InspectOwnedPage(
                owner="surf-chatgpt",
                thread="missing",
                allowed_scope=OwnedPageScope.CHATGPT,
                classifier=CLASSIFIER,
            )
        )

    assert client.calls == [
        (
            "owned-inspect",
            {
                "owner": "surf-chatgpt",
                "thread": "missing",
                "allowed_scope": "chatgpt",
                "classifier": CLASSIFIER.source,
            },
        )
    ]


def test_patchright_rebind_sends_all_identity_guards_through_one_bridge_call() -> None:
    client = ScriptedClient(
        {
            "ok": True,
            "page": {
                "thread": "surf-chatgpt-session-abc123",
                "page_token": 5,
                "url": "https://chatgpt.com/c/abc123",
            },
        }
    )
    bridge = PatchrightOwnedPageBridge(client)

    page = bridge.rebind(
        RebindOwnedPage(
            owner="surf-chatgpt",
            source_thread="temporary",
            destination_thread="surf-chatgpt-session-abc123",
            expected_page_token=5,
            expected_exact_url="https://chatgpt.com/c/abc123",
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
        )
    )

    assert page.thread == "surf-chatgpt-session-abc123"
    assert client.calls == [
        (
            "owned-rebind",
            {
                "owner": "surf-chatgpt",
                "source_thread": "temporary",
                "destination_thread": "surf-chatgpt-session-abc123",
                "expected_page_token": 5,
                "expected_exact_url": "https://chatgpt.com/c/abc123",
                "allowed_scope": "chatgpt",
                "expected_protection": "human_intervention",
            },
        )
    ]


def test_patchright_protection_update_carries_live_identity_and_metadata_guards() -> (
    None
):
    client = ScriptedClient({"ok": True})
    bridge = PatchrightOwnedPageBridge(client)

    bridge.protect(
        ProtectOwnedPage(
            owner="surf-chatgpt",
            thread="preserved",
            expected_page_token=8,
            allowed_scope=OwnedPageScope.CHATGPT,
            expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
            protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        )
    )

    assert client.calls == [
        (
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
    ]
