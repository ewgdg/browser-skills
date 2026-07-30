from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import SplitResult, urlsplit


class OwnedPageProtection(StrEnum):
    EXPLICITLY_RETAINED = "explicitly_retained"
    HUMAN_INTERVENTION = "human_intervention"


class OwnedPageScope(StrEnum):
    CHATGPT = "chatgpt"
    CHATGPT_PRE_SESSION = "chatgpt_pre_session"


class OwnedPageCapability(StrEnum):
    DEDICATED_PROFILE = "dedicated_profile"
    EXACT_URL_INVENTORY = "exact_url_inventory"
    NONACTIVATING_WINDOW_CREATION = "nonactivating_window_creation"
    OWNER_TAGGED_BINDINGS = "owner_tagged_bindings"
    LIVE_PROTECTION = "live_protection"
    ATOMIC_REBINDING = "atomic_rebinding"
    SERIALIZED_GUARDED_MUTATION = "serialized_guarded_mutation"


class OwnedPageInspectionState(StrEnum):
    SESSION = "session"
    PRE_SESSION = "pre_session"
    HUMAN_GATE = "human_gate"
    UNRECOGNIZED = "unrecognized"


class OwnedPageBridgeErrorCode(StrEnum):
    THREAD_NOT_FOUND = "thread_not_found"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    INSPECTION_FAILED = "inspection_failed"
    SUBMISSION_ALREADY_ATTEMPTED = "submission_already_attempted"
    AMBIGUOUS_SESSION_PAGE = "ambiguous_session_page"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    ABANDONMENT_FAILED = "abandonment_failed"


class OwnedPageSelectionDimension(StrEnum):
    MODEL = "model"
    THINKING = "thinking"


class OwnedPagePreparationState(StrEnum):
    READY = "ready"
    LOGIN_REQUIRED = "login_required"
    CHALLENGE = "challenge"
    MODEL_UNAVAILABLE = "model_unavailable"
    UI_CHANGED = "ui_changed"


class OwnedPageSubmissionState(StrEnum):
    SUBMITTED = "submitted"
    LOGIN_REQUIRED = "login_required"
    CHALLENGE = "challenge"
    MODEL_UNAVAILABLE = "model_unavailable"
    UI_CHANGED = "ui_changed"


class OwnedPageAssignmentState(StrEnum):
    SESSION = "session"
    NOT_READY = "not_ready"
    LOGIN_REQUIRED = "login_required"
    CHALLENGE = "challenge"
    UI_CHANGED = "ui_changed"


class OwnedPageAttemptState(StrEnum):
    GENERATING = "generating"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class OwnedPageRetentionReason(StrEnum):
    GENERATING = "generating"
    HUMAN_INTERVENTION = "human_intervention"
    INSPECTION_FAILED = "inspection_failed"
    EXPLICITLY_RETAINED = "explicitly_retained"


REQUIRED_OWNED_PAGE_CAPABILITIES = frozenset(OwnedPageCapability)
CHATGPT_HOSTNAME = "chatgpt.com"
CHATGPT_PRE_SESSION_PATHS = frozenset({"", "/", "/auth/login", "/auth/login/"})
CHATGPT_SESSION_PATH_PATTERN = re.compile(r"^/c/[A-Za-z0-9_-]+$")


def owned_page_canonical_session_url(session_id: str) -> str:
    return f"https://{CHATGPT_HOSTNAME}/c/{session_id}"


@dataclass(frozen=True)
class OwnedPageCapabilities:
    available: frozenset[OwnedPageCapability] = frozenset()

    @property
    def supported(self) -> bool:
        return self.available == REQUIRED_OWNED_PAGE_CAPABILITIES

    @classmethod
    def complete(cls) -> OwnedPageCapabilities:
        return cls(REQUIRED_OWNED_PAGE_CAPABILITIES)


@dataclass(frozen=True)
class OwnedPageAllocationPolicy:
    limit: int
    sweep_program: OwnedPageProgram

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or self.limit <= 0
        ):
            raise ValueError("Owned-page capacity limits must be positive integers.")


@dataclass(frozen=True)
class AllocateOwnedPage:
    owner: str
    thread: str
    url: str
    allowed_scope: OwnedPageScope
    policy: OwnedPageAllocationPolicy
    expected_protection: OwnedPageProtection | None = None
    protection: OwnedPageProtection | None = None


@dataclass(frozen=True)
class InspectOwnedPage:
    owner: str
    thread: str
    allowed_scope: OwnedPageScope
    classifier: OwnedPageClassifier


@dataclass(frozen=True)
class ResolveOwnedPage:
    owner: str
    thread: str
    exact_url: str
    allowed_scope: OwnedPageScope


@dataclass(frozen=True)
class OwnedPageClassifier:
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Owned-page classifiers require browser-side source.")


@dataclass(frozen=True)
class OwnedPageProgram:
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Owned-page programs require browser-side source.")


@dataclass(frozen=True)
class OwnedPageSelection:
    dimension: OwnedPageSelectionDimension
    label: str


@dataclass(frozen=True)
class PrepareOwnedPageSubmission:
    owner: str
    thread: str
    expected_page_token: int
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None
    program: OwnedPageProgram
    requested_selection_dimensions: frozenset[OwnedPageSelectionDimension]


@dataclass(frozen=True)
class SubmitOwnedPagePrompt:
    owner: str
    thread: str
    expected_page_token: int
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None
    readiness_program: OwnedPageProgram
    submission_program: OwnedPageProgram


@dataclass(frozen=True)
class ObserveOwnedPageAssignment:
    owner: str
    thread: str
    expected_page_token: int
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None
    program: OwnedPageProgram
    completion_exact_url: str | None


@dataclass(frozen=True)
class GuardedOwnedPageProgram:
    owner: str
    thread: str
    expected_page_token: int
    expected_exact_url: str
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None
    program: OwnedPageProgram


@dataclass(frozen=True)
class ClassifyOwnedPageAttempt(GuardedOwnedPageProgram):
    pass


@dataclass(frozen=True)
class ExtractOwnedPageResult(GuardedOwnedPageProgram):
    pass


@dataclass(frozen=True)
class CloseTerminalOwnedPage(GuardedOwnedPageProgram):
    expected_state: OwnedPageAttemptState


@dataclass(frozen=True)
class AbandonOwnedPage:
    owner: str
    thread: str
    expected_exact_url: str | None
    allowed_scope: OwnedPageScope
    classify_program: OwnedPageProgram
    stop_program: OwnedPageProgram
    stop_confirmation_timeout_seconds: float
    stop_confirmation_poll_interval_seconds: float

    def __post_init__(self) -> None:
        intervals = (
            self.stop_confirmation_timeout_seconds,
            self.stop_confirmation_poll_interval_seconds,
        )
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
            for value in intervals
        ):
            raise ValueError("Owned-page stop confirmation intervals must be positive.")


@dataclass(frozen=True)
class RebindOwnedPage:
    owner: str
    source_thread: str
    destination_thread: str
    expected_page_token: int
    expected_exact_url: str
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None


@dataclass(frozen=True)
class ProtectOwnedPage:
    owner: str
    thread: str
    expected_page_token: int
    allowed_scope: OwnedPageScope
    expected_protection: OwnedPageProtection | None
    protection: OwnedPageProtection | None


@dataclass(frozen=True)
class OwnedPageRef:
    thread: str
    page_token: int
    exact_url: str


@dataclass(frozen=True)
class ResolvedOwnedPage:
    page: OwnedPageRef
    protection: OwnedPageProtection | None


@dataclass(frozen=True)
class OwnedPageInspection:
    page: OwnedPageRef
    state: OwnedPageInspectionState


@dataclass(frozen=True)
class OwnedPageSubmissionPreparation:
    page: OwnedPageRef
    state: OwnedPagePreparationState
    selection: tuple[OwnedPageSelection, ...] = ()


@dataclass(frozen=True)
class OwnedPagePromptSubmission:
    page: OwnedPageRef
    state: OwnedPageSubmissionState


@dataclass(frozen=True)
class OwnedPageAssignmentObservation:
    page: OwnedPageRef
    state: OwnedPageAssignmentState
    session_id: str | None = None


@dataclass(frozen=True)
class OwnedPageAttemptMetadata:
    page: OwnedPageRef
    state: OwnedPageAttemptState


@dataclass(frozen=True)
class OwnedPageAttemptResult:
    page: OwnedPageRef
    state: OwnedPageAttemptState
    text: str | None = None


@dataclass(frozen=True)
class OwnedPageAbandonment:
    attempt_state: OwnedPageAttemptState | None

    def __post_init__(self) -> None:
        if self.attempt_state is not None and not isinstance(
            self.attempt_state, OwnedPageAttemptState
        ):
            raise TypeError("Abandonment outcomes require an allow-listed attempt state.")
        if self.attempt_state is OwnedPageAttemptState.GENERATING:
            raise ValueError("Successful abandonment cannot remain generating.")


@dataclass(frozen=True)
class OwnedPageRetainedPage:
    session_id: str | None
    thread: str
    reason: OwnedPageRetentionReason

    def __post_init__(self) -> None:
        if self.session_id is not None and (
            not isinstance(self.session_id, str)
            or not CHATGPT_SESSION_PATH_PATTERN.fullmatch(f"/c/{self.session_id}")
        ):
            raise ValueError("Retained pages require a valid session identity.")
        if not isinstance(self.thread, str) or not self.thread:
            raise ValueError("Retained pages require a non-empty thread identity.")
        if not isinstance(self.reason, OwnedPageRetentionReason):
            raise TypeError("Retained pages require an allow-listed reason.")


@dataclass(frozen=True)
class OwnedPageCapacity:
    limit: int
    retained: tuple[OwnedPageRetainedPage, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or self.limit <= 0
        ):
            raise ValueError("Owned-page capacity limits must be positive integers.")
        if len(self.retained) != self.limit:
            raise ValueError(
                "Owned-page capacity failures require one entry per blocking page."
            )


class OwnedPageBridge(Protocol):
    def capabilities(self) -> OwnedPageCapabilities: ...

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef: ...

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage: ...

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection: ...

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef: ...

    def protect(self, request: ProtectOwnedPage) -> None: ...

    def prepare_submission(
        self, request: PrepareOwnedPageSubmission
    ) -> OwnedPageSubmissionPreparation: ...

    def submit_prompt(
        self,
        request: SubmitOwnedPagePrompt,
        *,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission: ...

    def observe_assignment(
        self, request: ObserveOwnedPageAssignment
    ) -> OwnedPageAssignmentObservation: ...

    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata: ...

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult: ...

    def close_terminal(self, request: CloseTerminalOwnedPage) -> None: ...

    def abandon(self, request: AbandonOwnedPage) -> OwnedPageAbandonment: ...


class OwnedPageBridgeClient(Protocol):
    def call_tool(self, name: str, args: dict[str, object] | None = None) -> str: ...

    def call_tool_if_running(
        self,
        name: str,
        args: dict[str, object] | None = None,
        *,
        on_request_may_have_been_dispatched: Callable[[], None] | None = None,
    ) -> str | None: ...


class OwnedPageAgent(Protocol):
    @property
    def backend(self) -> str: ...

    @property
    def patchright_client(self) -> OwnedPageBridgeClient: ...


class UnsupportedOwnedPageCapability(Exception):
    pass


class OwnedPageNotFound(Exception):
    pass


class OwnedPageOwnershipConflict(Exception):
    pass


class OwnedPageInspectionFailed(Exception):
    pass


class OwnedPageSubmissionAlreadyAttempted(Exception):
    pass


class OwnedPageAmbiguousSession(Exception):
    pass


class OwnedPageCapacityExceeded(Exception):
    def __init__(self, capacity: OwnedPageCapacity) -> None:
        self.capacity = capacity
        super().__init__("Owned-page capacity exceeded.")


class OwnedPageAbandonmentFailed(Exception):
    pass


class PatchrightOwnedPageBridge:
    def __init__(self, client: OwnedPageBridgeClient) -> None:
        self._client = client

    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities.complete()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        payload: dict[str, object] = {
            "owner": request.owner,
            "thread": request.thread,
            "url": request.url,
            "allowed_scope": request.allowed_scope.value,
            "expected_protection": _protection_wire_value(request.expected_protection),
            "protection": _protection_wire_value(request.protection),
        }
        payload.update(
            {
                "capacity_limit": request.policy.limit,
                "sweep_program": request.policy.sweep_program.source,
            }
        )
        try:
            return _decode_page_ref(self._client.call_tool("owned-allocate", payload))
        except OwnedPageCapacityExceeded as error:
            if error.capacity.limit != request.policy.limit:
                raise ValueError(
                    "Owned-page bridge returned a mismatched capacity limit."
                ) from None
            raise

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        payload: dict[str, object] = {
            "owner": request.owner,
            "thread": request.thread,
            "exact_url": request.exact_url,
            "allowed_scope": request.allowed_scope.value,
        }
        return _decode_resolved_page(self._client.call_tool("owned-resolve", payload))

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        payload: dict[str, object] = {
            "owner": request.owner,
            "thread": request.thread,
            "allowed_scope": request.allowed_scope.value,
            "classifier": request.classifier.source,
        }
        raw = self._client.call_tool_if_running("owned-inspect", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_inspection(raw)

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        payload: dict[str, object] = {
            "owner": request.owner,
            "source_thread": request.source_thread,
            "destination_thread": request.destination_thread,
            "expected_page_token": request.expected_page_token,
            "expected_exact_url": request.expected_exact_url,
            "allowed_scope": request.allowed_scope.value,
            "expected_protection": _protection_wire_value(request.expected_protection),
        }
        return _decode_page_ref(self._client.call_tool("owned-rebind", payload))

    def protect(self, request: ProtectOwnedPage) -> None:
        payload: dict[str, object] = {
            "owner": request.owner,
            "thread": request.thread,
            "expected_page_token": request.expected_page_token,
            "allowed_scope": request.allowed_scope.value,
            "expected_protection": _protection_wire_value(request.expected_protection),
            "protection": _protection_wire_value(request.protection),
        }
        _decode_operation_outcome(self._client.call_tool("owned-protect", payload))

    def prepare_submission(
        self, request: PrepareOwnedPageSubmission
    ) -> OwnedPageSubmissionPreparation:
        payload: dict[str, object] = {
            **_guard_payload(request),
            "program": request.program.source,
            "requested_selection_dimensions": sorted(
                dimension.value
                for dimension in request.requested_selection_dimensions
            ),
        }
        raw = self._client.call_tool_if_running("owned-prepare-submission", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_submission_preparation(
            raw, request.requested_selection_dimensions
        )

    def submit_prompt(
        self,
        request: SubmitOwnedPagePrompt,
        *,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission:
        payload: dict[str, object] = {
            **_guard_payload(request),
            "readiness_program": request.readiness_program.source,
            "submission_program": request.submission_program.source,
        }
        raw = self._client.call_tool_if_running(
            "owned-submit-prompt",
            payload,
            on_request_may_have_been_dispatched=on_send_may_have_occurred,
        )
        if raw is None:
            raise OwnedPageNotFound
        return _decode_prompt_submission(raw)

    def observe_assignment(
        self, request: ObserveOwnedPageAssignment
    ) -> OwnedPageAssignmentObservation:
        payload: dict[str, object] = {
            **_guard_payload(request),
            "program": request.program.source,
            "completion_exact_url": request.completion_exact_url,
        }
        raw = self._client.call_tool_if_running("owned-observe-assignment", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_assignment_observation(raw)

    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        payload: dict[str, object] = {
            **_observation_guard_payload(request),
            "program": request.program.source,
        }
        raw = self._client.call_tool_if_running("owned-classify-attempt", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_attempt_metadata(raw)

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        payload: dict[str, object] = {
            **_observation_guard_payload(request),
            "program": request.program.source,
        }
        raw = self._client.call_tool_if_running("owned-extract-result", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_attempt_result(raw)

    def close_terminal(self, request: CloseTerminalOwnedPage) -> None:
        payload: dict[str, object] = {
            **_observation_guard_payload(request),
            "program": request.program.source,
            "expected_state": request.expected_state.value,
        }
        raw = self._client.call_tool_if_running("owned-close-terminal", payload)
        if raw is None:
            raise OwnedPageNotFound
        _decode_operation_outcome(raw)

    def abandon(self, request: AbandonOwnedPage) -> OwnedPageAbandonment:
        payload: dict[str, object] = {
            "owner": request.owner,
            "thread": request.thread,
            "expected_exact_url": request.expected_exact_url,
            "allowed_scope": request.allowed_scope.value,
            "classify_program": request.classify_program.source,
            "stop_program": request.stop_program.source,
            "stop_confirmation_timeout_seconds": (
                request.stop_confirmation_timeout_seconds
            ),
            "stop_confirmation_poll_interval_seconds": (
                request.stop_confirmation_poll_interval_seconds
            ),
        }
        raw = self._client.call_tool_if_running("owned-abandon", payload)
        if raw is None:
            raise OwnedPageNotFound
        return _decode_abandonment(raw)


class UnsupportedOwnedPageBridge:
    def capabilities(self) -> OwnedPageCapabilities:
        return OwnedPageCapabilities()

    def allocate(self, request: AllocateOwnedPage) -> OwnedPageRef:
        _ = request
        raise UnsupportedOwnedPageCapability

    def resolve(self, request: ResolveOwnedPage) -> ResolvedOwnedPage:
        _ = request
        raise UnsupportedOwnedPageCapability

    def inspect(self, request: InspectOwnedPage) -> OwnedPageInspection:
        _ = request
        raise UnsupportedOwnedPageCapability

    def rebind(self, request: RebindOwnedPage) -> OwnedPageRef:
        _ = request
        raise UnsupportedOwnedPageCapability

    def protect(self, request: ProtectOwnedPage) -> None:
        _ = request
        raise UnsupportedOwnedPageCapability

    def prepare_submission(
        self, request: PrepareOwnedPageSubmission
    ) -> OwnedPageSubmissionPreparation:
        _ = request
        raise UnsupportedOwnedPageCapability

    def submit_prompt(
        self,
        request: SubmitOwnedPagePrompt,
        *,
        on_send_may_have_occurred: Callable[[], None],
    ) -> OwnedPagePromptSubmission:
        _ = request, on_send_may_have_occurred
        raise UnsupportedOwnedPageCapability

    def observe_assignment(
        self, request: ObserveOwnedPageAssignment
    ) -> OwnedPageAssignmentObservation:
        _ = request
        raise UnsupportedOwnedPageCapability

    def classify_attempt(
        self, request: ClassifyOwnedPageAttempt
    ) -> OwnedPageAttemptMetadata:
        _ = request
        raise UnsupportedOwnedPageCapability

    def extract_result(
        self, request: ExtractOwnedPageResult
    ) -> OwnedPageAttemptResult:
        _ = request
        raise UnsupportedOwnedPageCapability

    def close_terminal(self, request: CloseTerminalOwnedPage) -> None:
        _ = request
        raise UnsupportedOwnedPageCapability

    def abandon(self, request: AbandonOwnedPage) -> OwnedPageAbandonment:
        _ = request
        raise UnsupportedOwnedPageCapability


def _decode_page_ref(raw: str) -> OwnedPageRef:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Owned-page bridge returned an invalid allocation outcome.")
    if decoded.get("ok") is False:
        _raise_owned_page_error(decoded.get("error"), decoded)
    if decoded.get("ok") is not True:
        raise ValueError("Owned-page bridge returned an invalid allocation outcome.")
    page = decoded.get("page")
    if not isinstance(page, dict) or set(page) != {"thread", "page_token", "url"}:
        raise ValueError("Owned-page bridge returned an invalid page reference.")
    thread = page["thread"]
    page_token = page["page_token"]
    exact_url = page["url"]
    if (
        not isinstance(thread, str)
        or not thread
        or not isinstance(page_token, int)
        or isinstance(page_token, bool)
        or page_token < 1
        or not isinstance(exact_url, str)
    ):
        raise ValueError("Owned-page bridge returned an invalid page reference.")
    return OwnedPageRef(
        thread=thread,
        page_token=page_token,
        exact_url=exact_url,
    )


def _decode_resolved_page(raw: str) -> ResolvedOwnedPage:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Owned-page bridge returned an invalid resolution outcome.")
    if decoded.get("ok") is False:
        _raise_owned_page_error(decoded.get("error"))
    if set(decoded) != {
        "ok",
        "page",
        "protection",
    }:
        raise ValueError("Owned-page bridge returned an invalid resolution outcome.")
    if decoded.get("ok") is not True:
        raise ValueError("Owned-page bridge returned an invalid resolution outcome.")
    page_only = json.dumps(
        {"ok": True, "page": decoded["page"]}, separators=(",", ":")
    )
    try:
        protection = decode_owned_page_protection(decoded["protection"])
    except ValueError as error:
        raise ValueError(
            "Owned-page bridge returned an invalid resolution outcome."
        ) from error
    return ResolvedOwnedPage(_decode_page_ref(page_only), protection)


def _raise_owned_page_error(
    error: object,
    payload: dict[object, object] | None = None,
) -> None:
    if error == OwnedPageBridgeErrorCode.CAPACITY_EXCEEDED:
        raise OwnedPageCapacityExceeded(_decode_capacity_failure(payload))
    if error == OwnedPageBridgeErrorCode.ABANDONMENT_FAILED:
        raise OwnedPageAbandonmentFailed
    if error == OwnedPageBridgeErrorCode.THREAD_NOT_FOUND:
        raise OwnedPageNotFound
    if error == OwnedPageBridgeErrorCode.OWNERSHIP_CONFLICT:
        raise OwnedPageOwnershipConflict
    if error == OwnedPageBridgeErrorCode.INSPECTION_FAILED:
        raise OwnedPageInspectionFailed
    if error == OwnedPageBridgeErrorCode.SUBMISSION_ALREADY_ATTEMPTED:
        raise OwnedPageSubmissionAlreadyAttempted
    if error == OwnedPageBridgeErrorCode.AMBIGUOUS_SESSION_PAGE:
        raise OwnedPageAmbiguousSession
    raise ValueError("Owned-page bridge returned an invalid error outcome.")


def _decode_capacity_failure(
    payload: dict[object, object] | None,
) -> OwnedPageCapacity:
    if payload is None or set(payload) != {"ok", "error", "capacity"}:
        raise ValueError("Owned-page bridge returned an invalid capacity outcome.")
    capacity = payload.get("capacity")
    if not isinstance(capacity, dict) or set(capacity) != {"limit", "retained"}:
        raise ValueError("Owned-page bridge returned an invalid capacity outcome.")
    limit = capacity.get("limit")
    retained = capacity.get("retained")
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
        or not isinstance(retained, list)
    ):
        raise ValueError("Owned-page bridge returned an invalid capacity outcome.")
    decoded_retained: list[OwnedPageRetainedPage] = []
    for item in retained:
        metadata = _require_string_keyed_object(
            item,
            "Owned-page bridge returned invalid retained-page metadata.",
        )
        if set(metadata) not in (
            {"thread", "reason"},
            {"thread", "session_id", "reason"},
        ):
            raise ValueError("Owned-page bridge returned invalid retained-page metadata.")
        session_id = metadata.get("session_id")
        thread = metadata.get("thread")
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError("Owned-page bridge returned invalid retained-page metadata.")
        if not isinstance(thread, str):
            raise ValueError("Owned-page bridge returned invalid retained-page metadata.")
        try:
            reason = OwnedPageRetentionReason(metadata["reason"])
            decoded_retained.append(
                OwnedPageRetainedPage(
                    session_id=session_id,
                    thread=thread,
                    reason=reason,
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Owned-page bridge returned invalid retained-page metadata."
            ) from error
    return OwnedPageCapacity(limit=limit, retained=tuple(decoded_retained))


def _decode_abandonment(raw: str) -> OwnedPageAbandonment:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Owned-page bridge returned an invalid abandonment outcome.")
    if decoded.get("ok") is False:
        if set(decoded) != {"ok", "error"}:
            raise ValueError("Owned-page bridge returned an invalid abandonment outcome.")
        _raise_owned_page_error(decoded.get("error"), decoded)
    if set(decoded) != {"ok", "attempt_state"} or decoded.get("ok") is not True:
        raise ValueError("Owned-page bridge returned an invalid abandonment outcome.")
    raw_state = decoded["attempt_state"]
    try:
        state = None if raw_state is None else OwnedPageAttemptState(raw_state)
        return OwnedPageAbandonment(attempt_state=state)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Owned-page bridge returned an invalid abandonment outcome."
        ) from error


def _decode_operation_outcome(raw: str) -> None:
    decoded = json.loads(raw)
    if decoded == {"ok": True}:
        return
    if isinstance(decoded, dict) and decoded.get("ok") is False:
        _raise_owned_page_error(decoded.get("error"))
    raise ValueError("Owned-page bridge returned an invalid operation outcome.")


def _decode_inspection(raw: str) -> OwnedPageInspection:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Owned-page bridge returned an invalid inspection outcome.")
    if decoded.get("ok") is False:
        _raise_owned_page_error(decoded.get("error"))
    if set(decoded) != {"ok", "page", "metadata"} or decoded.get("ok") is not True:
        raise ValueError("Owned-page bridge returned an invalid inspection outcome.")
    metadata = decoded["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != {"state"}:
        raise ValueError("Owned-page bridge returned invalid inspection metadata.")
    try:
        state = OwnedPageInspectionState(metadata["state"])
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Owned-page bridge returned invalid inspection metadata."
        ) from error
    page_only = json.dumps({"ok": True, "page": decoded["page"]}, separators=(",", ":"))
    return OwnedPageInspection(page=_decode_page_ref(page_only), state=state)


def _guard_payload(
    request: PrepareOwnedPageSubmission
    | SubmitOwnedPagePrompt
    | ObserveOwnedPageAssignment
    | GuardedOwnedPageProgram,
) -> dict[str, object]:
    return {
        "owner": request.owner,
        "thread": request.thread,
        "expected_page_token": request.expected_page_token,
        "allowed_scope": request.allowed_scope.value,
        "expected_protection": _protection_wire_value(request.expected_protection),
    }


def _observation_guard_payload(
    request: GuardedOwnedPageProgram,
) -> dict[str, object]:
    return {
        **_guard_payload(request),
        "expected_exact_url": request.expected_exact_url,
    }


def _decode_guarded_metadata(raw: str) -> tuple[OwnedPageRef, dict[str, object]]:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Owned-page bridge returned an invalid guarded outcome.")
    if decoded.get("ok") is False:
        _raise_owned_page_error(decoded.get("error"))
    if set(decoded) != {"ok", "page", "metadata"} or decoded.get("ok") is not True:
        raise ValueError("Owned-page bridge returned an invalid guarded outcome.")
    metadata = _require_string_keyed_object(
        decoded["metadata"],
        "Owned-page bridge returned invalid guarded metadata.",
    )
    page_only = json.dumps({"ok": True, "page": decoded["page"]}, separators=(",", ":"))
    return _decode_page_ref(page_only), metadata


def _decode_submission_preparation(
    raw: str,
    requested_dimensions: frozenset[OwnedPageSelectionDimension],
) -> OwnedPageSubmissionPreparation:
    page, metadata = _decode_guarded_metadata(raw)
    try:
        state = OwnedPagePreparationState(metadata.get("state"))
    except (TypeError, ValueError) as error:
        raise ValueError("Owned-page bridge returned invalid preparation metadata.") from error
    if state is not OwnedPagePreparationState.READY:
        if set(metadata) != {"state"}:
            raise ValueError("Owned-page bridge returned invalid preparation metadata.")
        return OwnedPageSubmissionPreparation(page=page, state=state)
    if set(metadata) != {"state", "selection"}:
        raise ValueError("Owned-page bridge returned invalid preparation metadata.")
    selection = _require_string_keyed_object(
        metadata["selection"],
        "Owned-page bridge returned invalid preparation metadata.",
    )
    expected_keys = {dimension.value for dimension in requested_dimensions}
    if set(selection) != expected_keys:
        raise ValueError("Owned-page bridge returned invalid preparation metadata.")
    decoded_selection: list[OwnedPageSelection] = []
    for dimension in sorted(requested_dimensions, key=lambda item: item.value):
        label = selection[dimension.value]
        if not isinstance(label, str) or not label:
            raise ValueError("Owned-page bridge returned invalid preparation metadata.")
        decoded_selection.append(OwnedPageSelection(dimension=dimension, label=label))
    return OwnedPageSubmissionPreparation(
        page=page,
        state=state,
        selection=tuple(decoded_selection),
    )


def _require_string_keyed_object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError(message)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _decode_prompt_submission(raw: str) -> OwnedPagePromptSubmission:
    page, metadata = _decode_guarded_metadata(raw)
    if set(metadata) != {"state"}:
        raise ValueError("Owned-page bridge returned invalid submission metadata.")
    try:
        state = OwnedPageSubmissionState(metadata["state"])
    except (TypeError, ValueError) as error:
        raise ValueError("Owned-page bridge returned invalid submission metadata.") from error
    return OwnedPagePromptSubmission(page=page, state=state)


def _decode_assignment_observation(raw: str) -> OwnedPageAssignmentObservation:
    page, metadata = _decode_guarded_metadata(raw)
    try:
        state = OwnedPageAssignmentState(metadata.get("state"))
    except (TypeError, ValueError) as error:
        raise ValueError("Owned-page bridge returned invalid assignment metadata.") from error
    allows_session_identity = state in {
        OwnedPageAssignmentState.SESSION,
        OwnedPageAssignmentState.LOGIN_REQUIRED,
        OwnedPageAssignmentState.CHALLENGE,
    }
    has_session_identity = set(metadata) == {"state", "session_id"}
    if state is OwnedPageAssignmentState.SESSION and not has_session_identity:
        raise ValueError("Owned-page bridge returned invalid assignment metadata.")
    if not has_session_identity:
        if set(metadata) != {"state"}:
            raise ValueError("Owned-page bridge returned invalid assignment metadata.")
        return OwnedPageAssignmentObservation(page, state)
    session_id = metadata["session_id"]
    if (
        not allows_session_identity
        or not isinstance(session_id, str)
        or not CHATGPT_SESSION_PATH_PATTERN.fullmatch(f"/c/{session_id}")
    ):
        raise ValueError("Owned-page bridge returned invalid assignment metadata.")
    return OwnedPageAssignmentObservation(page, state, session_id)


def _decode_attempt_metadata(raw: str) -> OwnedPageAttemptMetadata:
    page, metadata = _decode_guarded_metadata(raw)
    if set(metadata) != {"state"}:
        raise ValueError("Owned-page bridge returned invalid attempt metadata.")
    try:
        state = OwnedPageAttemptState(metadata["state"])
    except (TypeError, ValueError) as error:
        raise ValueError("Owned-page bridge returned invalid attempt metadata.") from error
    return OwnedPageAttemptMetadata(page=page, state=state)


def _decode_attempt_result(raw: str) -> OwnedPageAttemptResult:
    page, metadata = _decode_guarded_metadata(raw)
    try:
        state = OwnedPageAttemptState(metadata.get("state"))
    except (TypeError, ValueError) as error:
        raise ValueError("Owned-page bridge returned invalid result metadata.") from error
    requires_text = state in {
        OwnedPageAttemptState.COMPLETED,
        OwnedPageAttemptState.STOPPED,
    }
    expected_keys = {"state", "text"} if requires_text else {"state"}
    if set(metadata) != expected_keys:
        raise ValueError("Owned-page bridge returned invalid result metadata.")
    text = metadata.get("text")
    if requires_text and not isinstance(text, str):
        raise ValueError("Owned-page bridge returned invalid result metadata.")
    return OwnedPageAttemptResult(
        page=page,
        state=state,
        text=text if isinstance(text, str) else None,
    )


def _protection_wire_value(
    protection: OwnedPageProtection | None,
) -> str | None:
    return protection.value if protection is not None else None


def decode_owned_page_protection(value: object) -> OwnedPageProtection | None:
    if value is None:
        return None
    try:
        return OwnedPageProtection(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid owned-page protection.") from error


def owned_page_url_is_in_scope(url: str, scope: OwnedPageScope) -> bool:
    parsed = _parse_chatgpt_url(url)
    if parsed is None:
        return False
    if scope is OwnedPageScope.CHATGPT:
        return True
    return parsed.path in CHATGPT_PRE_SESSION_PATHS


def owned_page_url_is_canonical_session(url: str) -> bool:
    parsed = _parse_chatgpt_url(url)
    return bool(
        parsed is not None
        and CHATGPT_SESSION_PATH_PATTERN.fullmatch(parsed.path)
        and url == f"https://{CHATGPT_HOSTNAME}{parsed.path}"
    )


def _parse_chatgpt_url(url: str) -> SplitResult | None:
    try:
        parsed = urlsplit(url)
        valid_origin = (
            parsed.scheme == "https"
            and parsed.hostname == CHATGPT_HOSTNAME
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return None
    if not valid_origin:
        return None
    return parsed


def create_owned_page_bridge(agent: OwnedPageAgent | None = None) -> OwnedPageBridge:
    if agent is None:
        # Keep the owned-page module independent from the CLI's backend setup.
        from .cli import SurfAgent

        active_agent: OwnedPageAgent = SurfAgent()
    else:
        active_agent = agent
    if active_agent.backend == "patchright":
        return PatchrightOwnedPageBridge(active_agent.patchright_client)
    return UnsupportedOwnedPageBridge()
