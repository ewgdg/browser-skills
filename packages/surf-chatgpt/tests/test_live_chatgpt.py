from __future__ import annotations

import html
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from surf_chatgpt.dom.attempt import (
    ASSISTANT_MESSAGE_SELECTOR,
    COMPLETION_MARKER_SELECTORS,
    CONTINUE_GENERATING_SELECTORS,
    FAILURE_MARKER_SELECTORS,
    RETRY_MARKER_SELECTORS,
    STOP_GENERATING_SELECTORS,
    TURN_SELECTOR,
    classify_latest_attempt_source,
)
from surf_chatgpt.dom.submission import (
    observe_session_assignment_source,
    send_submission_source,
)
from surf_chatgpt.session_address import SessionAddress

from ._live_chatgpt_gate import GateCommandError, JsonCommandRunner

ASK_TIMEOUT_SECONDS = 90.0
RESULT_WAIT_SECONDS = 300.0
RESULT_COMMAND_TIMEOUT_SECONDS = RESULT_WAIT_SECONDS + 30.0
RECENT_DISCOVERY_TIMEOUT_SECONDS = 45.0
RECENT_DISCOVERY_POLL_SECONDS = 1.0
UNRELATED_PAGE_TIMEOUT_SECONDS = 15.0
SUBMISSION_EVIDENCE_TIMEOUT_SECONDS = 15.0
SUBMISSION_EVIDENCE_POLL_SECONDS = 0.25
ATTEMPT_STABILIZATION_TIMEOUT_SECONDS = 15.0
ATTEMPT_STABILIZATION_POLL_SECONDS = 0.25
ATTEMPT_COMPLETION_POLL_SECONDS = 0.25
HEARTBEAT_INTERVAL_MILLISECONDS = 250
LIVE_GATE_PROMPT_PREFIX = "Reply with exactly OK. Disposable live compatibility nonce:"
LOGIN_GATE_PROMPT_PREFIX = "This must not be sent. Logged-out compatibility nonce:"
UNRELATED_THREAD_PREFIX = "surf-chatgpt-live-gate-unrelated"


class UnrelatedPageProbe:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._document_ids: set[str] = set()
        self._heartbeat_count = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> UnrelatedPageProbe:
        probe = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                if parsed.path == "/heartbeat":
                    document_id = parse_qs(parsed.query).get("document", [""])[0]
                    if document_id:
                        with probe._condition:
                            probe._document_ids.add(document_id)
                            probe._heartbeat_count += 1
                            probe._condition.notify_all()
                    self.send_response(204)
                    self.end_headers()
                    return
                if parsed.path == "/unrelated":
                    body = _unrelated_page_html().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("unrelated-page probe is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}/unrelated"

    @property
    def document_count(self) -> int:
        with self._condition:
            return len(self._document_ids)

    @property
    def heartbeat_count(self) -> int:
        with self._condition:
            return self._heartbeat_count

    def wait_for_document_count(self, expected: int) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self._document_ids) >= expected,
                timeout=UNRELATED_PAGE_TIMEOUT_SECONDS,
            )

    def wait_for_heartbeat_after(self, previous_count: int) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._heartbeat_count > previous_count,
                timeout=UNRELATED_PAGE_TIMEOUT_SECONDS,
            )


def _unrelated_page_html() -> str:
    interval = html.escape(str(HEARTBEAT_INTERVAL_MILLISECONDS))
    return f"""<!doctype html>
<meta charset=\"utf-8\">
<title>Live gate unrelated page</title>
<script>
const documentId = crypto.randomUUID();
const heartbeat = () => fetch(
  `/heartbeat?document=${{encodeURIComponent(documentId)}}`,
  {{cache: "no-store"}}
).catch(() => undefined);
heartbeat();
setInterval(heartbeat, {interval});
</script>
"""


def _available_local_port() -> int:
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        return int(port_socket.getsockname()[1])


def _focused_niri_window_id(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    completed = run(
        ["niri", "msg", "--json", "focused-window"],
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    if completed.returncode != 0:
        raise GateCommandError("Niri focused-window inspection failed")
    try:
        focused = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise GateCommandError("Niri focused-window inspection returned invalid JSON") from None
    window_id = focused.get("id") if isinstance(focused, dict) else None
    is_focused = focused.get("is_focused") if isinstance(focused, dict) else None
    if not isinstance(window_id, int) or isinstance(window_id, bool) or is_focused is not True:
        raise GateCommandError("Niri focused-window inspection returned invalid metadata")
    return window_id


def _gate_environment(state_home: Path, profile: Path, port: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("SURF_AGENT_PATCHRIGHT_APP_ID", None)
    environment.pop("SURF_AGENT_PATCHRIGHT_CLASS", None)
    environment.update(
        {
            "SURF_AGENT_BACKEND": "patchright",
            "SURF_AGENT_HOME": str(state_home),
            "SURF_AGENT_PATCHRIGHT_PROFILE_DIR": str(profile),
            "SURF_AGENT_PATCHRIGHT_PORT": str(port),
        }
    )
    return environment


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GateCommandError(message)


def _require_success(payload: dict[str, object], operation: str) -> None:
    _require(payload.get("ok") is True, f"{operation} returned a non-success outcome")


def _session_from_ask(payload: dict[str, object]) -> SessionAddress:
    _require_success(payload, "retained ask")
    session = payload.get("session")
    _require(isinstance(session, dict), "retained ask omitted durable session identity")
    session_id = session.get("id") if isinstance(session, dict) else None
    _require(
        isinstance(session_id, str), "retained ask returned invalid session identity"
    )
    try:
        return SessionAddress.parse(session_id if isinstance(session_id, str) else "")
    except ValueError:
        raise GateCommandError(
            "retained ask returned invalid session identity"
        ) from None


def _session_page_state(
    runner: JsonCommandRunner, session: SessionAddress
) -> tuple[int, dict[str, object]]:
    __tracebackhide__ = True
    state = runner.agent_json(
        "inspect deterministic session binding", "--thread", session.thread, "state"
    )
    _require(state.get("open") is True, "deterministic session binding is not open")
    _require(
        state.get("thread") == session.thread, "deterministic session thread is missing"
    )
    _require(
        state.get("url") == session.canonical_url,
        "deterministic session URL is not canonical",
    )
    page_id = state.get("page_id")
    _require(isinstance(page_id, int), "deterministic session page identity is missing")
    probe = runner.agent_json(
        "inspect content-free live session metadata",
        "--thread",
        session.thread,
        "eval",
        "() => ({focused: document.hasFocus(), userTurns: document.querySelectorAll('[data-message-author-role=\"user\"]').length})",
    )
    return int(page_id), probe


def _require_single_submission(probe: dict[str, object]) -> None:
    # Trusted browser input focuses the document inside a background target; that is
    # not desktop-window activation. The no-input logged-out handoff is checked
    # separately and must remain document-unfocused.
    _require(
        probe.get("userTurns") == 1, "disposable conversation was not sent exactly once"
    )


def _wait_for_single_submission(
    runner: JsonCommandRunner,
    session: SessionAddress,
) -> tuple[int, dict[str, object]]:
    __tracebackhide__ = True
    deadline = time.monotonic() + SUBMISSION_EVIDENCE_TIMEOUT_SECONDS
    while True:
        page_id, probe = _session_page_state(runner, session)
        user_turns = probe.get("userTurns")
        if user_turns == 1:
            return page_id, probe
        _require(
            isinstance(user_turns, int) and user_turns < 1,
            "disposable conversation was sent more than once",
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GateCommandError("disposable conversation did not render its user turn")
        threading.Event().wait(min(SUBMISSION_EVIDENCE_POLL_SECONDS, remaining))


def _wait_for_classifiable_attempt(
    runner: JsonCommandRunner,
    session: SessionAddress,
) -> str:
    __tracebackhide__ = True
    deadline = time.monotonic() + ATTEMPT_STABILIZATION_TIMEOUT_SECONDS
    while True:
        metadata = runner.agent_json(
            "wait for content-free attempt metadata",
            "--thread",
            session.thread,
            "eval",
            classify_latest_attempt_source(),
        )
        state = metadata.get("state")
        if state in {"generating", "completed"}:
            return str(state)
        _require(
            state == "unrecognized",
            "disposable response reached an unexpected attempt state",
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            diagnostic = _attempt_structure_diagnostic(runner, session)
            raise GateCommandError(
                f"disposable response attempt did not stabilize; {diagnostic}"
            )
        threading.Event().wait(min(ATTEMPT_STABILIZATION_POLL_SECONDS, remaining))


def _attempt_structure_diagnostic(
    runner: JsonCommandRunner,
    session: SessionAddress,
) -> str:
    __tracebackhide__ = True
    metadata = runner.agent_json(
        "inspect content-free attempt structure",
        "--thread",
        session.thread,
        "eval",
        f"""() => {{
          const isVisible = (node) => {{
            if (!node || node.closest('[hidden], [aria-hidden="true"], [inert]')) {{
              return false;
            }}
            const rectangle = node.getBoundingClientRect?.();
            const style = window.getComputedStyle?.(node);
            return Boolean(
              rectangle && rectangle.width > 0 && rectangle.height > 0 &&
              style?.display !== 'none' && style?.visibility !== 'hidden' &&
              Number(style?.opacity ?? 1) > 0
            );
          }};
          const visibleCount = (root, selectors) => new Set(
            selectors.flatMap((selector) => Array.from(root.querySelectorAll(selector)))
              .filter(isVisible)
          ).size;
          const turns = Array.from(document.querySelectorAll({json.dumps(TURN_SELECTOR)}));
          const latest = turns.at(-1) || null;
          const latestRole = latest?.getAttribute('data-turn');
          return {{
            turnCount: turns.length,
            latestRole: ['user', 'assistant'].includes(latestRole) ? latestRole : 'other',
            assistantTurnCount: turns.filter(
              (turn) => turn.getAttribute('data-turn') === 'assistant'
            ).length,
            assistantMessageCount: document.querySelectorAll(
              {json.dumps(ASSISTANT_MESSAGE_SELECTOR)}
            ).length,
            latestAssistantMessageCount: latest ? latest.querySelectorAll(
              {json.dumps(ASSISTANT_MESSAGE_SELECTOR)}
            ).length : 0,
            stopCount: visibleCount(document, {json.dumps(STOP_GENERATING_SELECTORS)}),
            completionCount: visibleCount(
              latest || document, {json.dumps(COMPLETION_MARKER_SELECTORS)}
            ),
            continueCount: visibleCount(
              latest || document, {json.dumps(CONTINUE_GENERATING_SELECTORS)}
            ),
            failureCount: visibleCount(
              latest || document, {json.dumps(FAILURE_MARKER_SELECTORS)}
            ),
            retryCount: visibleCount(
              latest || document, {json.dumps(RETRY_MARKER_SELECTORS)}
            )
          }};
        }}""",
    )
    count_names = (
        "turnCount",
        "assistantTurnCount",
        "assistantMessageCount",
        "latestAssistantMessageCount",
        "stopCount",
        "completionCount",
        "continueCount",
        "failureCount",
        "retryCount",
    )
    counts = {
        name: value
        for name in count_names
        if isinstance((value := metadata.get(name)), int) and 0 <= value <= 100
    }
    role = metadata.get("latestRole")
    safe_role = role if role in {"user", "assistant", "other"} else "unknown"
    rendered_counts = ",".join(
        f"{name}={counts.get(name, 'unknown')}" for name in count_names
    )
    return f"latestRole={safe_role},{rendered_counts}"


def _wait_for_completed_attempt(
    runner: JsonCommandRunner,
    session: SessionAddress,
) -> None:
    __tracebackhide__ = True
    deadline = time.monotonic() + RESULT_WAIT_SECONDS
    while True:
        metadata = runner.agent_json(
            "wait for content-free attempt completion",
            "--thread",
            session.thread,
            "eval",
            classify_latest_attempt_source(),
        )
        state = metadata.get("state")
        if state == "completed":
            return
        _require(
            state in {"generating", "unrecognized"},
            "disposable response reached an unexpected terminal state",
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GateCommandError("disposable response did not complete")
        threading.Event().wait(min(ATTEMPT_COMPLETION_POLL_SECONDS, remaining))


def _wait_for_restart_recovery(
    runner: JsonCommandRunner,
    session: SessionAddress,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], object] | None = None,
) -> dict[str, object]:
    """Retry only fail-closed observation while a restored page hydrates."""
    wait_once = wait or (lambda duration: threading.Event().wait(duration))
    deadline = monotonic() + ATTEMPT_STABILIZATION_TIMEOUT_SECONDS
    while True:
        exit_code, payload = runner.chatgpt_outcome(
            "recover session after bridge restart",
            "session",
            "status",
            session.id,
            "--retain",
        )
        if exit_code == 0:
            _require_attempt(payload, {"completed"}, "restart recovery")
            return payload
        error = payload.get("error")
        error_type = error.get("type") if isinstance(error, dict) else None
        if error_type != "inspection_failed":
            _require_successful_exit(exit_code, payload, "restart recovery")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GateCommandError(
                "restart recovery did not stabilize after page restoration"
            )
        wait_once(min(ATTEMPT_STABILIZATION_POLL_SECONDS, remaining))


def _require_attempt(
    payload: dict[str, object], allowed_states: set[str], operation: str
) -> None:
    _require_success(payload, operation)
    attempt = payload.get("attempt")
    state = attempt.get("state") if isinstance(attempt, dict) else None
    _require(
        state in allowed_states, f"{operation} returned an unexpected attempt state"
    )


def _completed_result_text(payload: dict[str, object], operation: str) -> str:
    __tracebackhide__ = True
    _require_attempt(payload, {"completed"}, operation)
    result = payload.get("result")
    text = result.get("text") if isinstance(result, dict) else None
    partial = result.get("partial") if isinstance(result, dict) else None
    _require(isinstance(text, str) and bool(text), f"{operation} omitted response text")
    _require(partial is False, f"{operation} returned a partial response")
    return text if isinstance(text, str) else ""


def _wait_until_recent_contains(
    runner: JsonCommandRunner, session: SessionAddress
) -> None:
    __tracebackhide__ = True
    deadline = time.monotonic() + RECENT_DISCOVERY_TIMEOUT_SECONDS
    while True:
        recent = runner.chatgpt("discover recent sessions", "session", "recent")
        _require_success(recent, "recent session discovery")
        sessions = recent.get("sessions")
        _require(
            isinstance(sessions, list),
            "recent session discovery returned invalid candidates",
        )
        _require(
            len(sessions) <= 10, "recent session discovery exceeded its public bound"
        )
        if any(
            isinstance(candidate, dict) and candidate.get("id") == session.id
            for candidate in sessions
        ):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GateCommandError(
                "disposable session did not enter the first ten recent candidates"
            )
        threading.Event().wait(min(RECENT_DISCOVERY_POLL_SECONDS, remaining))


def _logged_out_handoff_thread(payload: dict[str, object]) -> str:
    handoff = payload.get("handoff")
    _require(isinstance(handoff, dict), "logged-out ask omitted its preserved handoff")
    action = handoff.get("action") if isinstance(handoff, dict) else None
    thread = handoff.get("thread") if isinstance(handoff, dict) else None
    _require(
        action == "complete_login", "logged-out ask returned the wrong handoff action"
    )
    _require(
        isinstance(thread, str) and bool(thread),
        "logged-out ask omitted its handoff thread",
    )
    _require("session" not in payload, "logged-out ask assigned a session before login")
    return thread if isinstance(thread, str) else ""


def _optional_session(payload: dict[str, object]) -> SessionAddress | None:
    session = payload.get("session")
    session_id = session.get("id") if isinstance(session, dict) else None
    if not isinstance(session_id, str):
        return None
    try:
        return SessionAddress.parse(session_id)
    except ValueError:
        return None


def _optional_recovery_thread(payload: dict[str, object]) -> str | None:
    thread = payload.get("thread")
    if isinstance(thread, str) and thread:
        return thread
    handoff = payload.get("handoff")
    thread = handoff.get("thread") if isinstance(handoff, dict) else None
    return thread if isinstance(thread, str) and thread else None


def _require_successful_exit(
    exit_code: int, payload: dict[str, object], operation: str
) -> None:
    if exit_code != 0:
        error = payload.get("error")
        error_type = error.get("type") if isinstance(error, dict) else None
        safe_error = (
            error_type
            if isinstance(error_type, str)
            and error_type
            and all(
                character in "abcdefghijklmnopqrstuvwxyz_" for character in error_type
            )
            else None
        )
        suffix = f", error {safe_error}" if safe_error is not None else ""
        raise GateCommandError(f"{operation} failed (exit {exit_code}{suffix})")
    _require_success(payload, operation)


def _route_category(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "chatgpt.com" or parsed.port:
        return "outside_chatgpt"
    if parsed.path in {"", "/"}:
        route = "root"
    elif len(parsed.path.split("/")) == 3 and parsed.path.startswith("/c/"):
        route = "canonical"
    else:
        route = "other_chatgpt"
    if route == "other_chatgpt":
        return route
    if parsed.query:
        return f"{route}_query"
    if parsed.fragment:
        return f"{route}_fragment"
    return f"{route}_clean"


def _submission_failure_diagnostic(
    runner: JsonCommandRunner,
    thread: str,
    prompt: str,
    expected_session: SessionAddress | None = None,
) -> str:
    __tracebackhide__ = True
    try:
        state = runner.agent_json(
            "inspect indeterminate submission route", "--thread", thread, "state"
        )
        url = state.get("url")
        route = _route_category(url) if isinstance(url, str) else "missing"
        route_matches_session = (
            isinstance(url, str)
            and expected_session is not None
            and url == expected_session.canonical_url
        )
        probe = runner.agent_json(
            "inspect indeterminate submission metadata",
            "--thread",
            thread,
            "eval",
            """() => {
              const expectedPrompt = __EXPECTED_PROMPT__;
              const prefix = 'Disposable live compatibility nonce:';
              const sessionSegment = location.pathname.startsWith('/c/')
                ? location.pathname.slice(3) : '';
              const userTurns = Array.from(
                document.querySelectorAll('[data-message-author-role="user"]')
              );
              const composers = Array.from(document.querySelectorAll(
                '#prompt-textarea, [data-testid="composer-textarea"], textarea[name="prompt-textarea"], .ProseMirror, [contenteditable="true"]'
              ));
              const visible = (node) => {
                if (!node || node.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
                const rectangle = node.getBoundingClientRect?.();
                const style = window.getComputedStyle?.(node);
                return Boolean(
                  rectangle && rectangle.width > 0 && rectangle.height > 0 &&
                  style?.display !== 'none' && style?.visibility !== 'hidden' &&
                  Number(style?.opacity ?? 1) > 0
                );
              };
              const send = Array.from(document.querySelectorAll(
                'button[data-testid="send-button"], button[data-testid*="composer-send"], form button[type="submit"]'
              )).find(visible);
              const accountVisible = Array.from(document.querySelectorAll(
                'button, a, [role="button"], [role="link"]'
              )).some((node) => {
                if (!visible(node)) return false;
                const evidence = [
                  node.getAttribute('aria-label'),
                  node.getAttribute('data-testid'),
                  node.textContent,
                  node.innerText,
                ].join(' ').toLowerCase();
                return /profile|account|settings|customize chatgpt|my plan/.test(evidence);
              });
              const text = (node) => String(
                'value' in node ? node.value : (node.innerText || node.textContent || '')
              );
              const visibleComposerTexts = composers.filter(visible).map(text);
              const nonceComposerText = visibleComposerTexts.find(
                (value) => value.includes(prefix)
              );
              const namedComposers = Array.from(
                document.querySelectorAll('textarea[name="prompt-textarea"]')
              );
              const allSendControls = Array.from(document.querySelectorAll(
                'button[data-testid="send-button"], button[data-testid*="composer-send"], form button[type="submit"]'
              ));
              const accountControls = Array.from(document.querySelectorAll(
                'button, a, [role="button"], [role="link"]'
              )).filter((node) => {
                const evidence = [
                  node.getAttribute('aria-label'),
                  node.getAttribute('data-testid'),
                  node.textContent,
                  node.innerText,
                ].join(' ').toLowerCase();
                return /profile|account|settings|customize chatgpt|my plan/.test(evidence);
              });
              const hiddenReasons = (nodes) => ({
                hiddenAncestor: nodes.filter((node) => Boolean(
                  node.closest('[hidden], [aria-hidden="true"], [inert]')
                )).length,
                zeroRectangle: nodes.filter((node) => {
                  const rectangle = node.getBoundingClientRect?.();
                  return !rectangle || rectangle.width <= 0 || rectangle.height <= 0;
                }).length,
                displayNone: nodes.filter(
                  (node) => window.getComputedStyle?.(node)?.display === 'none'
                ).length,
                visibilityHidden: nodes.filter(
                  (node) => window.getComputedStyle?.(node)?.visibility === 'hidden'
                ).length,
                opacityZero: nodes.filter(
                  (node) => Number(window.getComputedStyle?.(node)?.opacity ?? 1) <= 0
                ).length,
              });
              const extraIdCharacterCategories = Array.from(new Set(
                Array.from(sessionSegment)
                  .filter((character) => !/[A-Za-z0-9_-]/.test(character))
                  .map((character) => {
                    const categories = {
                      '.': 'dot', '~': 'tilde', '%': 'percent', ':': 'colon',
                      '@': 'at', '+': 'plus', '=': 'equals'
                    };
                    if (categories[character]) return categories[character];
                    return character.charCodeAt(0) < 128 ? 'other_ascii' : 'non_ascii';
                  })
              )).sort();
              return {
                promptVisible: [...userTurns, ...composers].some(
                  (node) => text(node).includes(prefix)
                ),
                promptExact: composers.some(
                  (node) => visible(node) && text(node) === expectedPrompt
                ),
                promptTrimmedExact: visibleComposerTexts.some(
                  (value) => value.trim() === expectedPrompt
                ),
                promptLengthDelta: typeof nonceComposerText === 'string'
                  ? nonceComposerText.length - expectedPrompt.length : null,
                userTurns: userTurns.length,
                sendVisible: Boolean(send),
                sendDisabled: send ? send.hasAttribute('disabled') ||
                  send.getAttribute('aria-disabled') === 'true' ||
                  send.getAttribute('data-disabled') === 'true' : null,
                accountVisible,
                namedComposerTotal: namedComposers.length,
                namedComposerVisible: namedComposers.filter(visible).length,
                namedComposerNonempty: namedComposers.filter(
                  (node) => node.value.length > 0
                ).length,
                namedComposerHiddenReasons: hiddenReasons(namedComposers),
                sendTotal: allSendControls.length,
                sendHiddenReasons: hiddenReasons(allSendControls),
                accountTotal: accountControls.length,
                accountHiddenReasons: hiddenReasons(accountControls),
                documentVisibility: document.visibilityState,
                documentReady: document.readyState,
                canonicalIdCharacters: /^[A-Za-z0-9_-]+$/.test(sessionSegment),
                canonicalIdLength: sessionSegment.length,
                extraIdCharacterCategories,
              };
            }""".replace("__EXPECTED_PROMPT__", json.dumps(prompt)),
        )
        prompt_visible = probe.get("promptVisible") is True
        prompt_exact = probe.get("promptExact") is True
        prompt_trimmed_exact = probe.get("promptTrimmedExact") is True
        prompt_length_delta = probe.get("promptLengthDelta")
        safe_prompt_length_delta = (
            prompt_length_delta
            if isinstance(prompt_length_delta, int)
            and -256 <= prompt_length_delta <= 256
            else "unknown"
        )
        user_turns = probe.get("userTurns")
        safe_user_turns = user_turns if isinstance(user_turns, int) else "unknown"
        send_visible = probe.get("sendVisible") is True
        send_disabled = probe.get("sendDisabled") is True
        account_visible = probe.get("accountVisible") is True
        safe_count = lambda value: (  # noqa: E731 - compact diagnostic allowlist
            value if isinstance(value, int) and 0 <= value <= 256 else "unknown"
        )
        hidden_reason_keys = {
            "hiddenAncestor",
            "zeroRectangle",
            "displayNone",
            "visibilityHidden",
            "opacityZero",
        }

        def safe_hidden_reasons(value: object) -> str:
            if not isinstance(value, dict) or set(value) != hidden_reason_keys:
                return "unknown"
            return "/".join(str(safe_count(value[key])) for key in sorted(value))

        safe_named_composer_total = safe_count(probe.get("namedComposerTotal"))
        safe_named_composer_visible = safe_count(probe.get("namedComposerVisible"))
        safe_named_composer_nonempty = safe_count(probe.get("namedComposerNonempty"))
        safe_named_composer_hidden = safe_hidden_reasons(
            probe.get("namedComposerHiddenReasons")
        )
        safe_send_total = safe_count(probe.get("sendTotal"))
        safe_send_hidden = safe_hidden_reasons(probe.get("sendHiddenReasons"))
        safe_account_total = safe_count(probe.get("accountTotal"))
        safe_account_hidden = safe_hidden_reasons(probe.get("accountHiddenReasons"))
        document_visibility = probe.get("documentVisibility")
        safe_document_visibility = (
            document_visibility
            if document_visibility in {"visible", "hidden", "prerender"}
            else "unknown"
        )
        document_ready = probe.get("documentReady")
        safe_document_ready = (
            document_ready
            if document_ready in {"loading", "interactive", "complete"}
            else "unknown"
        )
        canonical_id_characters = probe.get("canonicalIdCharacters") is True
        canonical_id_length = probe.get("canonicalIdLength")
        safe_canonical_id_length = (
            canonical_id_length
            if isinstance(canonical_id_length, int)
            and 0 <= canonical_id_length <= 256
            else "unknown"
        )
        extra_id_character_categories = probe.get("extraIdCharacterCategories")
        allowed_id_character_categories = {
            "dot",
            "tilde",
            "percent",
            "colon",
            "at",
            "plus",
            "equals",
            "other_ascii",
            "non_ascii",
        }
        safe_id_character_categories = (
            ",".join(extra_id_character_categories)
            if isinstance(extra_id_character_categories, list)
            and all(
                isinstance(category, str)
                and category in allowed_id_character_categories
                for category in extra_id_character_categories
            )
            else "unknown"
        )
        dispatch_recheck = runner.agent_json(
            "recheck content-free submission metadata",
            "--thread",
            thread,
            "eval",
            send_submission_source(
                prompt,
                allow_logged_out=False,
                pace="none",
            ),
        ).get("state")
        safe_dispatch_recheck = (
            dispatch_recheck
            if dispatch_recheck
            in {
                "submitted",
                "login_required",
                "challenge",
                "rate_limited",
                "model_unavailable",
                "ui_changed",
            }
            else "unknown"
        )
        assignment_recheck = runner.agent_json(
            "recheck content-free assignment metadata",
            "--thread",
            thread,
            "eval",
            observe_session_assignment_source(),
        ).get("state")
        safe_assignment_recheck = (
            assignment_recheck
            if assignment_recheck
            in {
                "session",
                "not_ready",
                "login_required",
                "challenge",
                "rate_limited",
                "ui_changed",
            }
            else "unknown"
        )
        attempt_recheck = runner.agent_json(
            "recheck content-free attempt metadata",
            "--thread",
            thread,
            "eval",
            classify_latest_attempt_source(),
        ).get("state")
        safe_attempt_recheck = (
            attempt_recheck
            if attempt_recheck
            in {
                "generating",
                "completed",
                "stopped",
                "failed",
                "rate_limited",
                "unrecognized",
            }
            else "unknown"
        )
        return (
            f"route={route}, prompt_visible={'yes' if prompt_visible else 'no'}, "
            "route_matches_session="
            f"{'yes' if route_matches_session else 'no'}, "
            f"prompt_exact={'yes' if prompt_exact else 'no'}, "
            f"prompt_trimmed_exact={'yes' if prompt_trimmed_exact else 'no'}, "
            f"prompt_length_delta={safe_prompt_length_delta}, "
            f"user_turns={safe_user_turns}, "
            f"send_visible={'yes' if send_visible else 'no'}, "
            f"send_disabled={'yes' if send_disabled else 'no'}, "
            f"account_visible={'yes' if account_visible else 'no'}, "
            f"named_composer={safe_named_composer_total}/"
            f"{safe_named_composer_visible}/{safe_named_composer_nonempty}, "
            f"named_composer_hidden={safe_named_composer_hidden}, "
            f"send_total={safe_send_total}, send_hidden={safe_send_hidden}, "
            f"account_total={safe_account_total}, account_hidden={safe_account_hidden}, "
            f"document={safe_document_visibility}/{safe_document_ready}, "
            "canonical_id_characters="
            f"{'yes' if canonical_id_characters else 'no'}, "
            f"canonical_id_length={safe_canonical_id_length}, "
            f"extra_id_character_categories={safe_id_character_categories}, "
            f"dispatch_recheck={safe_dispatch_recheck}, "
            f"assignment_recheck={safe_assignment_recheck}, "
            f"attempt_recheck={safe_attempt_recheck}"
        )
    except Exception:
        return "route=unavailable, prompt_visible=unknown, user_turns=unknown"


def _require_unsent_handoff(runner: JsonCommandRunner, thread: str) -> None:
    probe = runner.agent_json(
        "inspect content-free logged-out handoff metadata",
        "--thread",
        thread,
        "eval",
        "() => ({userTurns: document.querySelectorAll('[data-message-author-role=\"user\"]').length})",
    )
    _require(probe.get("userTurns") == 0, "logged-out ask sent before login")


def _run_logged_out_gate(bridge_port: int) -> None:
    __tracebackhide__ = True
    runner: JsonCommandRunner | None = None
    handoff_thread: str | None = None
    cleanup_failures: list[str] = []
    state_directory = tempfile.TemporaryDirectory(prefix="surf-chatgpt-logged-out-")
    try:
        state_path = Path(state_directory.name)
        runner = JsonCommandRunner(
            _gate_environment(state_path, state_path / "profile", bridge_port)
        )
        focused_window_before = _focused_niri_window_id()
        prompt = f"{LOGIN_GATE_PROMPT_PREFIX} {uuid.uuid4().hex}"
        logged_out = runner.chatgpt_error(
            "detect login before send",
            "human_intervention_required",
            "ask",
            "--retain",
            stdin=prompt,
            timeout=ASK_TIMEOUT_SECONDS,
        )
        focused_window_after = _focused_niri_window_id()
        _require(
            focused_window_after == focused_window_before,
            "logged-out handoff activated a different desktop window",
        )
        handoff_thread = _logged_out_handoff_thread(logged_out)
        _require_unsent_handoff(runner, handoff_thread)
        released = runner.chatgpt(
            "abandon logged-out handoff",
            "abandon",
            "--thread",
            handoff_thread,
        )
        _require_success(released, "logged-out handoff abandonment")
        handoff_thread = None
        runner.agent("stop logged-out gate bridge", "bridge", "stop")
    finally:
        active_exception = sys.exc_info()[0] is not None
        if runner is not None:
            if handoff_thread is not None:
                try:
                    runner.chatgpt(
                        "cleanup logged-out handoff",
                        "abandon",
                        "--thread",
                        handoff_thread,
                    )
                except Exception:
                    cleanup_failures.append("logged-out handoff")
            try:
                runner.agent("cleanup logged-out bridge", "bridge", "stop")
            except Exception:
                cleanup_failures.append("logged-out bridge")
        state_directory.cleanup()
        if cleanup_failures and not active_exception:
            raise GateCommandError(
                "logged-out gate cleanup failed for: " + ", ".join(cleanup_failures)
            )


@pytest.mark.live_chatgpt
def test_live_chatgpt_compatibility_gate(
    live_chatgpt_profile: Path,
) -> None:
    __tracebackhide__ = True
    bridge_port = _available_local_port()
    authenticated_session: SessionAddress | None = None
    authenticated_thread: str | None = None
    authenticated_runner: JsonCommandRunner | None = None
    cleanup_failures: list[str] = []
    state_directory = tempfile.TemporaryDirectory(prefix="surf-chatgpt-live-state-")

    try:
        with UnrelatedPageProbe() as unrelated_page:
            state_home = state_directory.name
            state_path = Path(state_home)
            authenticated_runner = JsonCommandRunner(
                _gate_environment(state_path, live_chatgpt_profile, bridge_port)
            )
            preflight_exit, preflight = authenticated_runner.chatgpt_outcome(
                "verify authenticated profile", "session", "recent"
            )
            if preflight_exit != 0:
                authenticated_thread = _optional_recovery_thread(preflight)
            _require_successful_exit(
                preflight_exit, preflight, "authenticated profile preflight"
            )
            unrelated_thread = f"{UNRELATED_THREAD_PREFIX}-{uuid.uuid4().hex}"
            authenticated_runner.agent(
                "open unrelated Surf page",
                "--thread",
                unrelated_thread,
                "open",
                unrelated_page.url,
            )
            _require(
                unrelated_page.wait_for_document_count(1),
                "unrelated Surf page did not become observable",
            )

            prompt = f"{LIVE_GATE_PROMPT_PREFIX} {uuid.uuid4().hex}"
            ask_exit, ask = authenticated_runner.chatgpt_outcome(
                "submit disposable prompt",
                "ask",
                "--retain",
                stdin=prompt,
                timeout=ASK_TIMEOUT_SECONDS,
            )
            if ask_exit != 0:
                authenticated_session = _optional_session(ask)
                authenticated_thread = _optional_recovery_thread(ask)
                diagnostic_thread = authenticated_thread or (
                    authenticated_session.thread
                    if authenticated_session is not None
                    else ""
                )
                diagnostic = _submission_failure_diagnostic(
                    authenticated_runner,
                    diagnostic_thread,
                    prompt,
                    authenticated_session,
                )
                try:
                    _require_successful_exit(ask_exit, ask, "retained ask")
                except GateCommandError as error:
                    raise GateCommandError(f"{error}; {diagnostic}") from None
            _require_successful_exit(ask_exit, ask, "retained ask")
            authenticated_session = _session_from_ask(ask)
            authenticated_thread = None
            live_page_id, live_probe = _wait_for_single_submission(
                authenticated_runner, authenticated_session
            )
            _require_single_submission(live_probe)
            _wait_for_classifiable_attempt(
                authenticated_runner,
                authenticated_session,
            )

            try:
                status = authenticated_runner.chatgpt(
                    "inspect status from a second command",
                    "session",
                    "status",
                    authenticated_session.id,
                )
            except GateCommandError as error:
                diagnostic = _submission_failure_diagnostic(
                    authenticated_runner,
                    authenticated_session.thread,
                    prompt,
                    authenticated_session,
                )
                raise GateCommandError(f"{error}; {diagnostic}") from None
            _require_attempt(
                status, {"generating", "completed"}, "second-command status"
            )
            repeated_page_id, repeated_probe = _session_page_state(
                authenticated_runner, authenticated_session
            )
            _require(
                repeated_page_id == live_page_id,
                "second command did not reuse the exact rebound page",
            )
            _require_single_submission(repeated_probe)
            _wait_for_completed_attempt(
                authenticated_runner,
                authenticated_session,
            )

            try:
                waited = authenticated_runner.chatgpt(
                    "wait for disposable result",
                    "session",
                    "result",
                    authenticated_session.id,
                    f"--wait={RESULT_WAIT_SECONDS:g}",
                    "--retain",
                    timeout=RESULT_COMMAND_TIMEOUT_SECONDS,
                )
            except GateCommandError as error:
                diagnostic = _submission_failure_diagnostic(
                    authenticated_runner,
                    authenticated_session.thread,
                    prompt,
                    authenticated_session,
                )
                raise GateCommandError(f"{error}; {diagnostic}") from None
            first_result = _completed_result_text(waited, "waiting result")
            repeated = authenticated_runner.chatgpt(
                "repeat disposable result",
                "session",
                "result",
                authenticated_session.id,
                "--retain",
            )
            second_result = _completed_result_text(repeated, "repeated result")
            _require(
                first_result == second_result,
                "repeated result was consuming or unstable",
            )
            _wait_until_recent_contains(authenticated_runner, authenticated_session)

            authenticated_runner.agent("restart dedicated bridge", "bridge", "stop")
            heartbeat_before_recovery = unrelated_page.heartbeat_count
            _wait_for_restart_recovery(
                authenticated_runner,
                authenticated_session,
            )
            _require(
                unrelated_page.wait_for_document_count(2),
                "unrelated Surf page was not restored across bridge restart",
            )
            _require(
                unrelated_page.document_count == 2,
                "restart recovery reloaded or replaced the unrelated Surf page",
            )
            _require(
                unrelated_page.wait_for_heartbeat_after(heartbeat_before_recovery),
                "restart recovery closed or stalled the unrelated Surf page",
            )
            _, recovered_probe = _session_page_state(
                authenticated_runner, authenticated_session
            )
            _require_single_submission(recovered_probe)

            abandoned = authenticated_runner.chatgpt(
                "abandon disposable session",
                "abandon",
                authenticated_session.id,
            )
            _require_success(abandoned, "disposable session abandonment")
            authenticated_session = None
            authenticated_runner.agent(
                "stop authenticated gate bridge", "bridge", "stop"
            )

            _run_logged_out_gate(bridge_port)
    finally:
        active_exception = sys.exc_info()[0] is not None
        if authenticated_runner is not None:
            if authenticated_session is not None:
                try:
                    authenticated_runner.chatgpt(
                        "cleanup disposable session",
                        "abandon",
                        authenticated_session.id,
                    )
                except Exception:
                    cleanup_failures.append("disposable session")
            elif authenticated_thread is not None:
                try:
                    authenticated_runner.chatgpt(
                        "cleanup authenticated pre-session page",
                        "abandon",
                        "--thread",
                        authenticated_thread,
                    )
                except Exception:
                    cleanup_failures.append("authenticated pre-session page")
            try:
                authenticated_runner.agent(
                    "cleanup authenticated bridge", "bridge", "stop"
                )
            except Exception:
                cleanup_failures.append("authenticated bridge")
        state_directory.cleanup()
        if cleanup_failures and not active_exception:
            raise GateCommandError(
                "live gate cleanup failed for: " + ", ".join(cleanup_failures)
            )
