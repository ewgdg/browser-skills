from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

from surf_agent.owned_pages import (
    AllocateOwnedPage,
    InspectOwnedPage,
    OwnedPageCapabilities,
    OwnedPageAmbiguousSession,
    OwnedPageInspection,
    OwnedPageInspectionFailed,
    OwnedPageInspectionState,
    OwnedPageNotFound,
    OwnedPageOwnershipConflict,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageBridge,
    ProtectOwnedPage,
    RebindOwnedPage,
    ResolveOwnedPage,
    ResolvedOwnedPage,
    owned_page_url_is_in_scope,
)
from surf_agent.errors import BridgeIdentityUnproven, BridgeUnavailable
from surf_chatgpt import cli
from surf_chatgpt.session_lifecycle import OwnedPageSessionLifecycle


@dataclass
class MemoryPage:
    owner: str
    reference: OwnedPageRef
    protection: OwnedPageProtection | None
    inspection_state: OwnedPageInspectionState


class InMemoryOwnedPageBridge:
    def __init__(self) -> None:
        self.pages: dict[str, MemoryPage] = {}
        self.calls: list[tuple[str, object]] = []
        self.next_page_token = 700

    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities.complete()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        existing = self.pages.get(request.thread)
        if existing is not None:
            if (
                existing.owner != request.owner
                or existing.protection is not request.expected_protection
                or not owned_page_url_is_in_scope(
                    existing.reference.exact_url, request.allowed_scope
                )
            ):
                raise OwnedPageOwnershipConflict
            return existing.reference
        reference = OwnedPageRef(
            thread=request.thread,
            page_token=self.next_page_token,
            exact_url=request.url,
        )
        self.next_page_token += 1
        self.pages[request.thread] = MemoryPage(
            request.owner,
            reference,
            request.protection,
            OwnedPageInspectionState.PRE_SESSION,
        )
        return reference

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        self.calls.append(("inspect", request))
        try:
            page = self.pages[request.thread]
        except KeyError as error:
            raise OwnedPageNotFound from error
        if page.owner != request.owner or not owned_page_url_is_in_scope(
            page.reference.exact_url, request.allowed_scope
        ):
            raise OwnedPageOwnershipConflict
        return OwnedPageInspection(page.reference, page.inspection_state)

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        self.calls.append(("resolve", request))
        existing = self.pages.get(request.thread)
        if existing is not None:
            if (
                existing.owner != request.owner
                or existing.reference.exact_url != request.exact_url
            ):
                raise OwnedPageOwnershipConflict
            return ResolvedOwnedPage(existing.reference, existing.protection)
        reference = OwnedPageRef(
            request.thread,
            self.next_page_token,
            request.exact_url,
        )
        self.next_page_token += 1
        self.pages[request.thread] = MemoryPage(
            request.owner,
            reference,
            None,
            OwnedPageInspectionState.SESSION,
        )
        return ResolvedOwnedPage(reference, None)

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        raise AssertionError("rebind is outside the issue 14 lifecycle path")

    def protect(self, request: ProtectOwnedPage) -> None:
        self.calls.append(("protect", request))
        page = self.pages[request.thread]
        if (
            page.reference.page_token != request.expected_page_token
            or page.protection is not request.expected_protection
        ):
            raise OwnedPageOwnershipConflict
        page.protection = request.protection


def invoke(argv: list[str], bridge: OwnedPageBridge) -> tuple[int, dict[str, Any], str]:
    output = io.StringIO()
    errors = io.StringIO()
    code = cli.main(
        argv,
        stdout=output,
        stderr=errors,
        lifecycle=OwnedPageSessionLifecycle(bridge),
    )
    return code, json.loads(output.getvalue()), errors.getvalue()


def test_login_allocates_or_reuses_one_protected_owned_page_and_returns_only_the_handoff() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()

    first = invoke(["login"], bridge)
    second = invoke(["login"], bridge)

    expected = {
        "ok": True,
        "handoff": {
            "action": "complete_login",
            "thread": "surf-chatgpt-login",
        },
    }
    assert first == (0, expected, "")
    assert second == (0, expected, "")
    assert len(bridge.pages) == 1
    assert bridge.pages["surf-chatgpt-login"].owner == "surf-chatgpt"
    assert (
        bridge.pages["surf-chatgpt-login"].protection
        is OwnedPageProtection.HUMAN_INTERVENTION
    )
    assert [operation for operation, _ in bridge.calls] == ["allocate", "allocate"]
    rendered = json.dumps(expected)
    assert '"owner"' not in rendered
    assert '"protection"' not in rendered
    assert '"page_token"' not in rendered
    assert "human_intervention" not in rendered
    assert "700" not in rendered


def test_session_current_reads_only_the_exact_owned_thread_for_not_ready_or_durable_identity() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.PRE_SESSION,
    )

    not_ready = invoke(["session", "current", "--thread", "preserved"], bridge)
    bridge.pages["preserved"].inspection_state = OwnedPageInspectionState.HUMAN_GATE
    human_gate = invoke(["session", "current", "--thread", "preserved"], bridge)
    bridge.pages["preserved"].reference = OwnedPageRef(
        thread="preserved",
        page_token=701,
        exact_url="https://chatgpt.com/c/abc123",
    )
    bridge.pages["preserved"].inspection_state = OwnedPageInspectionState.SESSION
    assigned = invoke(["session", "current", "--thread", "preserved"], bridge)

    assert not_ready == (
        0,
        {
            "ok": True,
            "session": None,
            "observation": {"outcome": "not_ready"},
        },
        "",
    )
    assert human_gate == not_ready
    assert assigned == (0, {"ok": True, "session": {"id": "abc123"}}, "")
    assert [operation for operation, _ in bridge.calls] == [
        "inspect",
        "inspect",
        "inspect",
    ]


def test_session_handoff_resolves_and_retains_only_the_durable_session() -> None:
    bridge = InMemoryOwnedPageBridge()

    code, payload, stderr = invoke(["session", "handoff", "abc123"], bridge)

    assert (code, payload, stderr) == (
        0,
        {
            "ok": True,
            "session": {"id": "abc123"},
            "handoff": {
                "action": "inspect_browser",
                "thread": "surf-chatgpt-session-abc123",
            },
        },
        "",
    )
    page = bridge.pages["surf-chatgpt-session-abc123"]
    assert page.reference.exact_url == "https://chatgpt.com/c/abc123"
    assert page.protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert [operation for operation, _ in bridge.calls] == ["resolve", "protect"]
    rendered = json.dumps(payload)
    assert "page_token" not in rendered
    assert "protection" not in rendered
    assert "https://" not in rendered


def test_session_handoff_reuses_existing_retained_protection_idempotently() -> None:
    bridge = InMemoryOwnedPageBridge()
    thread = "surf-chatgpt-session-abc123"
    bridge.pages[thread] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread,
            701,
            "https://chatgpt.com/c/abc123",
        ),
        protection=OwnedPageProtection.EXPLICITLY_RETAINED,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, _ = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 0
    assert payload["session"] == {"id": "abc123"}
    assert bridge.pages[thread].reference.page_token == 701
    assert bridge.pages[thread].protection is OwnedPageProtection.EXPLICITLY_RETAINED
    assert [operation for operation, _ in bridge.calls] == ["resolve"]


class AmbiguousSessionBridge(InMemoryOwnedPageBridge):
    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        self.calls.append(("resolve", request))
        raise OwnedPageAmbiguousSession


def test_session_handoff_projects_ambiguous_recovery_without_protection() -> None:
    bridge = AmbiguousSessionBridge()

    code, payload, _ = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ambiguous_session_page"
    assert payload["session"] == {"id": "abc123"}
    assert "handoff" not in payload
    assert [operation for operation, _ in bridge.calls] == ["resolve"]


class DisconnectedProtectionBridge(InMemoryOwnedPageBridge):
    def protect(self, request: ProtectOwnedPage) -> None:
        self.calls.append(("protect", request))
        raise BridgeUnavailable("private bridge failure")


def test_session_handoff_preserves_durable_identity_when_protection_fails() -> None:
    bridge = DisconnectedProtectionBridge()

    code, payload, stderr = invoke(["session", "handoff", "abc123"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "browser_unavailable"
    assert payload["session"] == {"id": "abc123"}
    assert "handoff" not in payload
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["resolve", "protect"]


def test_session_current_missing_thread_fails_without_allocating_or_recovering() -> (
    None
):
    bridge = InMemoryOwnedPageBridge()

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "missing"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "thread_not_found"
    assert stderr == ""
    assert bridge.pages == {}
    assert [operation for operation, _ in bridge.calls] == ["inspect"]


def test_session_current_fails_closed_for_an_unrecognized_in_scope_url() -> None:
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/c/abc123?private=canary",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert "canary" not in json.dumps(payload)
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["inspect"]


def test_session_current_requires_affirmative_gate_metadata() -> None:
    bridge = InMemoryOwnedPageBridge()
    bridge.pages["preserved"] = MemoryPage(
        owner="surf-chatgpt",
        reference=OwnedPageRef(
            thread="preserved",
            page_token=701,
            exact_url="https://chatgpt.com/",
        ),
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.UNRECOGNIZED,
    )

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert stderr == ""


class FailedInspectionBridge(InMemoryOwnedPageBridge):
    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        self.calls.append(("inspect", request))
        raise OwnedPageInspectionFailed


def test_session_current_projects_classifier_failure_without_browser_details() -> None:
    bridge = FailedInspectionBridge()

    code, payload, stderr = invoke(
        ["session", "current", "--thread", "preserved"], bridge
    )

    assert code == 1
    assert payload["error"]["type"] == "inspection_failed"
    assert stderr == ""


class UnsupportedBridge:
    def __init__(self) -> None:
        self.capability_checks = 0

    def capabilities(self) -> OwnedPageCapabilities:
        self.capability_checks += 1
        return OwnedPageCapabilities()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        raise AssertionError("unsupported capability must fail before allocation")

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        raise AssertionError("unsupported capability must fail before inspection")

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        raise AssertionError("unsupported capability must fail before rebinding")

    def protect(self, request: ProtectOwnedPage) -> None:
        raise AssertionError("unsupported capability must fail before protection")


def test_unsupported_backend_fails_login_and_current_before_any_browser_operation() -> (
    None
):
    bridge = UnsupportedBridge()

    login = invoke(["login"], bridge)
    current = invoke(["session", "current", "--thread", "preserved"], bridge)

    assert login[0] == 1
    assert login[1]["error"]["type"] == "unsupported_browser_capability"
    assert current[0] == 1
    assert current[1]["error"]["type"] == "unsupported_browser_capability"
    assert bridge.capability_checks == 2


class ConflictingLoginBridge(InMemoryOwnedPageBridge):
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        raise OwnedPageOwnershipConflict


def test_login_ownership_conflict_is_safe_and_preserves_the_existing_page() -> None:
    bridge = ConflictingLoginBridge()

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["allocate"]


class UnprovenIdentityBridge(InMemoryOwnedPageBridge):
    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        self.calls.append(("allocate", request))
        raise BridgeIdentityUnproven("Patchright bridge identity is unproven")


def test_login_fails_closed_when_the_bridge_profile_identity_is_unproven() -> None:
    bridge = UnprovenIdentityBridge()

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "browser_identity_unproven"
    assert stderr == ""
    assert [operation for operation, _ in bridge.calls] == ["allocate"]


def test_login_never_reuses_a_conversation_page_bound_to_the_login_thread() -> None:
    bridge = InMemoryOwnedPageBridge()
    reference = OwnedPageRef(
        thread="surf-chatgpt-login",
        page_token=702,
        exact_url="https://chatgpt.com/c/existing",
    )
    bridge.pages["surf-chatgpt-login"] = MemoryPage(
        owner="surf-chatgpt",
        reference=reference,
        protection=OwnedPageProtection.HUMAN_INTERVENTION,
        inspection_state=OwnedPageInspectionState.SESSION,
    )

    code, payload, stderr = invoke(["login"], bridge)

    assert code == 1
    assert payload["error"]["type"] == "ownership_conflict"
    assert stderr == ""
    assert bridge.pages["surf-chatgpt-login"].reference is reference
