from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from surf_agent.owned_pages import (
    AbandonOwnedPage,
    AllocateOwnedPage,
    ClassifyOwnedPageAttempt,
    CloseTerminalOwnedPage,
    ExtractOwnedPageResult,
    InspectOwnedPage,
    ObserveOwnedPageAssignment,
    OwnedPageAssignmentObservation,
    OwnedPageAttemptMetadata,
    OwnedPageAttemptResult,
    OwnedPageAttemptState,
    OwnedPageAmbiguousSession,
    OwnedPageAllocationPolicy,
    OwnedPageAbandonment,
    OwnedPageAbandonmentFailed,
    OwnedPageBridge,
    OwnedPageInspection,
    OwnedPageInspectionFailed,
    OwnedPageCapacity,
    OwnedPageCapacityExceeded,
    OwnedPageNotFound,
    OwnedPageOwnershipConflict,
    OwnedPageProgram,
    OwnedPageProtection,
    OwnedPageRef,
    OwnedPageRetainedPage,
    OwnedPageSelectionDimension,
    OwnedPageScope,
    OwnedPageSubmissionAlreadyAttempted,
    OwnedPageSubmissionPreparation,
    OwnedPagePromptSubmission,
    PrepareOwnedPageSubmission,
    ProtectOwnedPage,
    RebindOwnedPage,
    ResolveOwnedPage,
    ResolvedOwnedPage,
    SubmitOwnedPagePrompt,
    UnsupportedOwnedPageCapability,
)
from surf_agent.errors import BridgeIdentityUnproven, BridgeUnavailable, SurfAgentError

from .contracts import JsonObject, JsonValue, Pace
from .dom.attempt import classify_latest_attempt_source, extract_latest_result_source
from .dom.cleanup import classify_retained_page_source, request_stop_source
from .dom.readiness import CURRENT_SESSION_CLASSIFIER
from .dom.submission import (
    observe_session_assignment_source,
    prepare_submission_source,
    send_submission_source,
)
from .errors import PublicError, PublicErrorType
from .session_address import SessionAddress


SURF_CHATGPT_OWNER = "surf-chatgpt"
LOGIN_THREAD = "surf-chatgpt-login"
CHATGPT_HOME_URL = "https://chatgpt.com/"
MAX_RETAINED_PAGES = 10
STOP_CONFIRMATION_TIMEOUT_SECONDS = 10.0
STOP_CONFIRMATION_POLL_INTERVAL_SECONDS = 0.1
_Result = TypeVar("_Result")


class RetainedPageCapacityExceeded(Exception):
    def __init__(self, capacity: OwnedPageCapacity) -> None:
        self.capacity = capacity
        super().__init__("Retained-page capacity exceeded.")

    def to_public_json(self) -> JsonObject:
        retained: list[JsonValue] = []
        for page in self.capacity.retained:
            item = _retained_page_public_json(page)
            item["reason"] = page.reason.value
            retained.append(item)
        return {"limit": self.capacity.limit, "retained": retained}


def _retained_page_public_json(page: OwnedPageRetainedPage) -> JsonObject:
    if (
        page.session_id is not None
        and page.thread == SessionAddress(page.session_id).thread
    ):
        return {"session": {"id": page.session_id}}
    return {"thread": page.thread}


class ChatGptOwnedPages:
    def __init__(self, bridge: OwnedPageBridge) -> None:
        self._bridge = bridge

    def prepare_login(self) -> OwnedPageRef:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.allocate(
                AllocateOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=LOGIN_THREAD,
                    url=CHATGPT_HOME_URL,
                    allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
                    expected_protection=OwnedPageProtection.HUMAN_INTERVENTION,
                    protection=OwnedPageProtection.HUMAN_INTERVENTION,
                    policy=self._allocation_policy(),
                )
            )
        )

    def inspect_thread(self, thread: str) -> OwnedPageInspection:
        self._require_capabilities()
        try:
            return self._bridge.inspect(
                InspectOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=thread,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    classifier=CURRENT_SESSION_CLASSIFIER,
                )
            )
        except UnsupportedOwnedPageCapability as error:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY) from error
        except OwnedPageNotFound as error:
            raise PublicError(PublicErrorType.THREAD_NOT_FOUND) from error
        except OwnedPageOwnershipConflict as error:
            raise PublicError(PublicErrorType.OWNERSHIP_CONFLICT) from error
        except OwnedPageInspectionFailed as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        except BridgeIdentityUnproven as error:
            raise PublicError(PublicErrorType.BROWSER_IDENTITY_UNPROVEN) from error
        except SurfAgentError as error:
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error

    def allocate_submission(
        self,
        thread: str,
        *,
        protection: OwnedPageProtection | None,
    ) -> OwnedPageRef:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.allocate(
                AllocateOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=thread,
                    url=CHATGPT_HOME_URL,
                    allowed_scope=OwnedPageScope.CHATGPT_PRE_SESSION,
                    expected_protection=protection,
                    protection=protection,
                    policy=self._allocation_policy(),
                )
            )
        )

    def resolve_session(self, session: SessionAddress) -> ResolvedOwnedPage:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.resolve(
                ResolveOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=session.thread,
                    exact_url=session.canonical_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                )
            )
        )

    def prepare_submission(
        self,
        page: OwnedPageRef,
        *,
        allowed_scope: OwnedPageScope,
        expected_protection: OwnedPageProtection | None,
        model_query: str | None,
        thinking_query: str | None,
        allow_logged_out: bool,
    ) -> OwnedPageSubmissionPreparation:
        self._require_capabilities()
        dimensions = frozenset(
            dimension
            for dimension, query in (
                (OwnedPageSelectionDimension.MODEL, model_query),
                (OwnedPageSelectionDimension.THINKING, thinking_query),
            )
            if query is not None
        )
        return self._run(
            lambda: self._bridge.prepare_submission(
                PrepareOwnedPageSubmission(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    allowed_scope=allowed_scope,
                    expected_protection=expected_protection,
                    program=OwnedPageProgram(
                        prepare_submission_source(
                            model_query=model_query,
                            thinking_query=thinking_query,
                            allow_logged_out=allow_logged_out,
                        )
                    ),
                    requested_selection_dimensions=dimensions,
                )
            )
        )

    def submit_prompt(
        self,
        page: OwnedPageRef,
        *,
        allowed_scope: OwnedPageScope,
        expected_protection: OwnedPageProtection | None,
        prompt: str,
        allow_logged_out: bool,
        pace: Pace,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.submit_prompt(
                SubmitOwnedPagePrompt(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    allowed_scope=allowed_scope,
                    expected_protection=expected_protection,
                    readiness_program=OwnedPageProgram(
                        prepare_submission_source(
                            model_query=None,
                            thinking_query=None,
                            allow_logged_out=allow_logged_out,
                        )
                    ),
                    submission_program=OwnedPageProgram(
                        send_submission_source(
                            prompt,
                            allow_logged_out=allow_logged_out,
                            pace=pace.value,
                        )
                    ),
                ),
                on_send_may_have_occurred=on_send_may_have_occurred,
            )
        )

    def observe_assignment(
        self,
        page: OwnedPageRef,
        *,
        allowed_scope: OwnedPageScope,
        expected_protection: OwnedPageProtection | None,
        completion_exact_url: str | None,
    ) -> OwnedPageAssignmentObservation:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.observe_assignment(
                ObserveOwnedPageAssignment(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    allowed_scope=allowed_scope,
                    expected_protection=expected_protection,
                    program=OwnedPageProgram(observe_session_assignment_source()),
                    completion_exact_url=completion_exact_url,
                )
            )
        )

    def protect_submission(
        self,
        page: OwnedPageRef,
        *,
        expected_protection: OwnedPageProtection | None,
        protection: OwnedPageProtection | None,
    ) -> None:
        self._require_capabilities()
        self._run(
            lambda: self._bridge.protect(
                ProtectOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    expected_protection=expected_protection,
                    protection=protection,
                )
            )
        )

    def classify_attempt(
        self,
        resolved: ResolvedOwnedPage,
    ) -> OwnedPageAttemptMetadata:
        self._require_capabilities()
        page = resolved.page
        return self._run(
            lambda: self._bridge.classify_attempt(
                ClassifyOwnedPageAttempt(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    expected_exact_url=page.exact_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    expected_protection=resolved.protection,
                    program=OwnedPageProgram(classify_latest_attempt_source()),
                )
            )
        )

    def extract_result(
        self,
        resolved: ResolvedOwnedPage,
    ) -> OwnedPageAttemptResult:
        self._require_capabilities()
        page = resolved.page
        return self._run(
            lambda: self._bridge.extract_result(
                ExtractOwnedPageResult(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    expected_exact_url=page.exact_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    expected_protection=resolved.protection,
                    program=OwnedPageProgram(extract_latest_result_source()),
                )
            )
        )

    def close_terminal(
        self,
        resolved: ResolvedOwnedPage,
        expected_state: OwnedPageAttemptState,
    ) -> None:
        self._require_capabilities()
        page = resolved.page
        self._run(
            lambda: self._bridge.close_terminal(
                CloseTerminalOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=page.thread,
                    expected_page_token=page.page_token,
                    expected_exact_url=page.exact_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    expected_protection=resolved.protection,
                    program=OwnedPageProgram(classify_latest_attempt_source()),
                    expected_state=expected_state,
                )
            )
        )

    def abandon(
        self,
        *,
        thread: str,
        expected_exact_url: str | None,
    ) -> OwnedPageAbandonment:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.abandon(
                AbandonOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    thread=thread,
                    expected_exact_url=expected_exact_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    classify_program=OwnedPageProgram(
                        classify_retained_page_source()
                    ),
                    stop_program=OwnedPageProgram(request_stop_source()),
                    stop_confirmation_timeout_seconds=(
                        STOP_CONFIRMATION_TIMEOUT_SECONDS
                    ),
                    stop_confirmation_poll_interval_seconds=(
                        STOP_CONFIRMATION_POLL_INTERVAL_SECONDS
                    ),
                )
            )
        )

    def rebind_submission(
        self,
        page: OwnedPageRef,
        session: SessionAddress,
        *,
        expected_protection: OwnedPageProtection | None,
    ) -> OwnedPageRef:
        self._require_capabilities()
        return self._run(
            lambda: self._bridge.rebind(
                RebindOwnedPage(
                    owner=SURF_CHATGPT_OWNER,
                    source_thread=page.thread,
                    destination_thread=session.thread,
                    expected_page_token=page.page_token,
                    expected_exact_url=session.canonical_url,
                    allowed_scope=OwnedPageScope.CHATGPT,
                    expected_protection=expected_protection,
                )
            )
        )

    def _run(self, operation: Callable[[], _Result]) -> _Result:
        try:
            return operation()
        except (BridgeUnavailable, OwnedPageSubmissionAlreadyAttempted):
            raise
        except UnsupportedOwnedPageCapability as error:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY) from error
        except OwnedPageNotFound as error:
            raise PublicError(PublicErrorType.THREAD_NOT_FOUND) from error
        except OwnedPageOwnershipConflict as error:
            raise PublicError(PublicErrorType.OWNERSHIP_CONFLICT) from error
        except OwnedPageInspectionFailed as error:
            raise PublicError(PublicErrorType.INSPECTION_FAILED) from error
        except OwnedPageAmbiguousSession as error:
            raise PublicError(PublicErrorType.AMBIGUOUS_SESSION_PAGE) from error
        except OwnedPageCapacityExceeded as error:
            raise RetainedPageCapacityExceeded(error.capacity) from error
        except OwnedPageAbandonmentFailed as error:
            raise PublicError(PublicErrorType.ABANDONMENT_FAILED) from error
        except BridgeIdentityUnproven as error:
            raise PublicError(PublicErrorType.BROWSER_IDENTITY_UNPROVEN) from error
        except SurfAgentError as error:
            raise PublicError(PublicErrorType.BROWSER_UNAVAILABLE) from error

    def _allocation_policy(self) -> OwnedPageAllocationPolicy:
        return OwnedPageAllocationPolicy(
            limit=MAX_RETAINED_PAGES,
            sweep_program=OwnedPageProgram(classify_retained_page_source()),
        )

    def _require_capabilities(self) -> None:
        if not self._bridge.capabilities().supported:
            raise PublicError(PublicErrorType.UNSUPPORTED_BROWSER_CAPABILITY)
