from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


SUBMISSION_THREAD = "surf-chatgpt-submit-subprocess"
SESSION_ID = "abc123"
SESSION_THREAD = f"surf-chatgpt-session-{SESSION_ID}"
HOME_URL = "https://chatgpt.com/"
SESSION_URL = f"https://chatgpt.com/c/{SESSION_ID}"
PAGE_TOKEN = 900
PROCESS_TIMEOUT_SECONDS = 10.0
WORKER = Path(__file__).with_name("_submission_subprocess_worker.py")


class SubmissionBarrier(StrEnum):
    BEFORE_SEND = "before_send"
    SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN = "send_may_have_occurred_id_unknown"
    ID_KNOWN_REBIND_PENDING = "id_known_rebind_pending"
    HANDSHAKE_COMPLETE = "handshake_complete"


@dataclass
class DurableBridgeState:
    bindings: dict[str, str] = field(default_factory=dict)
    send_count: int = 0
    operations: list[str] = field(default_factory=list)
    protection: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict[str, object]:
        decoded = json.loads(self.stdout)
        if not isinstance(decoded, dict):
            raise AssertionError("CLI output was not one JSON object.")
        return decoded


class _JsonLineChannel:
    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self._reader = connection.makefile("rb")

    def send(self, message: dict[str, object]) -> None:
        encoded = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        self.connection.sendall(encoded)

    def receive(self) -> dict[str, Any]:
        line = self._reader.readline()
        if not line:
            raise EOFError("subprocess control channel closed")
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise AssertionError("subprocess sent an invalid control message")
        return decoded


class _BridgeServer(threading.Thread):
    def __init__(
        self,
        connection: socket.socket,
        state: DurableBridgeState,
        barrier: SubmissionBarrier | None,
    ) -> None:
        super().__init__(daemon=True)
        self._channel = _JsonLineChannel(connection)
        self._state = state
        self._barrier = barrier
        self.send_marker_reached = threading.Event()
        self.release_send = threading.Event()
        self.failure: BaseException | None = None

    def run(self) -> None:
        try:
            while True:
                try:
                    request = self._channel.receive()
                except EOFError:
                    return
                response = self._handle_request(request)
                try:
                    self._channel.send(response)
                except (BrokenPipeError, ConnectionError, OSError):
                    return
        except BaseException as error:
            self.failure = error
        finally:
            self._channel.connection.close()

    def disconnect(self) -> None:
        try:
            self._channel.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _handle_request(self, request: dict[str, Any]) -> dict[str, object]:
        operation = request["operation"]
        assert isinstance(operation, str)
        self._state.operations.append(operation)

        if operation == "allocate":
            thread = request["thread"]
            assert thread == SUBMISSION_THREAD
            self._state.bindings[thread] = request["url"]
            self._state.protection = request["protection"]
            return self._page_response(thread)

        if operation == "inspect":
            thread = request["thread"]
            exact_url = self._state.bindings[thread]
            state = "session" if exact_url == SESSION_URL else "pre_session"
            return {**self._page_response(thread), "state": state}

        if operation == "prepare_submission":
            if self._state.send_count:
                return {"ok": False, "error": "submission_already_attempted"}
            return {**self._guarded_page_response(request), "state": "ready"}

        if operation == "submit_prompt":
            if self._state.send_count:
                return {"ok": False, "error": "submission_already_attempted"}
            response = {
                **self._guarded_page_response(request),
                "state": "submitted",
            }
            # Persist this marker before notifying the controller. The killed
            # caller never owns the evidence used by the assertions.
            self._state.send_count += 1
            if self._barrier is SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
                self.send_marker_reached.set()
                self.release_send.wait()
            return response

        if operation == "observe_assignment":
            thread = request["thread"]
            self._guard(request)
            self._state.bindings[thread] = SESSION_URL
            return {
                **self._page_response(thread),
                "state": "session",
                "session_id": SESSION_ID,
            }

        if operation == "rebind":
            source_thread = request["source_thread"]
            destination_thread = request["destination_thread"]
            assert destination_thread == SESSION_THREAD
            assert request["expected_exact_url"] == SESSION_URL
            self._guard(request, thread_field="source_thread")
            exact_url = self._state.bindings.pop(source_thread)
            self._state.bindings[destination_thread] = exact_url
            return self._page_response(destination_thread)

        if operation == "protect":
            self._guard(request)
            assert request["expected_protection"] == self._state.protection
            self._state.protection = request["protection"]
            return {"ok": True}

        raise AssertionError(f"unexpected bridge operation: {operation}")

    def _guarded_page_response(self, request: dict[str, Any]) -> dict[str, object]:
        self._guard(request)
        return self._page_response(request["thread"])

    def _guard(
        self,
        request: dict[str, Any],
        *,
        thread_field: str = "thread",
    ) -> None:
        assert request["page_token"] == PAGE_TOKEN
        assert request[thread_field] in self._state.bindings

    def _page_response(self, thread: str) -> dict[str, object]:
        return {
            "ok": True,
            "thread": thread,
            "page_token": PAGE_TOKEN,
            "url": self._state.bindings[thread],
        }


@dataclass
class _ActiveChild:
    process: subprocess.Popen[str]
    control: _JsonLineChannel
    bridge_server: _BridgeServer


class SubmissionSubprocessHarness:
    def __init__(self, barrier: SubmissionBarrier) -> None:
        self.barrier = barrier
        self.state = DurableBridgeState()

    def interrupt(self, signal_number: signal.Signals) -> ProcessResult:
        active = self._start_child(
            ["ask", "--pace", "none", "subprocess prompt"],
            barrier=self.barrier,
        )
        self._wait_for_barrier(active)
        os.kill(active.process.pid, signal_number)
        return self._finish_interrupted_child(active)

    def disconnect_bridge(self) -> ProcessResult:
        active = self._start_child(
            ["ask", "--pace", "none", "subprocess prompt"],
            barrier=self.barrier,
        )
        self._wait_for_barrier(active)
        active.bridge_server.disconnect()
        self._release_barrier(active)
        return self._finish_child(active)

    def recover_current(self) -> ProcessResult:
        thread = (
            SESSION_THREAD
            if self.barrier is SubmissionBarrier.HANDSHAKE_COMPLETE
            else SUBMISSION_THREAD
        )
        active = self._start_child(
            ["session", "current", "--thread", thread],
            barrier=None,
        )
        return self._finish_child(active)

    def _start_child(
        self,
        cli_argv: list[str],
        *,
        barrier: SubmissionBarrier | None,
    ) -> _ActiveChild:
        bridge_parent, bridge_child = socket.socketpair()
        control_parent, control_child = socket.socketpair()
        bridge_parent.settimeout(PROCESS_TIMEOUT_SECONDS)
        control_parent.settimeout(PROCESS_TIMEOUT_SECONDS)
        server = _BridgeServer(bridge_parent, self.state, barrier)
        server.start()
        barrier_argument = barrier.value if barrier is not None else "none"
        process = subprocess.Popen(
            [
                sys.executable,
                str(WORKER),
                str(bridge_child.fileno()),
                str(control_child.fileno()),
                barrier_argument,
                json.dumps(cli_argv),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(bridge_child.fileno(), control_child.fileno()),
        )
        bridge_child.close()
        control_child.close()
        return _ActiveChild(process, _JsonLineChannel(control_parent), server)

    def _wait_for_barrier(self, active: _ActiveChild) -> None:
        if self.barrier is SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            if not active.bridge_server.send_marker_reached.wait(
                PROCESS_TIMEOUT_SECONDS
            ):
                self._raise_barrier_failure(active, "durable send marker")
            return
        try:
            message = active.control.receive()
        except (EOFError, OSError) as error:
            self._raise_barrier_failure(active, self.barrier.value, cause=error)
        assert message == {"phase": self.barrier.value}

    def _raise_barrier_failure(
        self,
        active: _ActiveChild,
        barrier: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        active.bridge_server.release_send.set()
        try:
            stdout, stderr = active.process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            active.process.kill()
            stdout, stderr = active.process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
        message = (
            f"child did not reach {barrier}; returncode={active.process.returncode}; "
            f"stdout={stdout!r}; stderr={stderr!r}; state={self.state!r}; "
            f"bridge_failure={active.bridge_server.failure!r}"
        )
        raise AssertionError(message) from cause

    def _release_barrier(self, active: _ActiveChild) -> None:
        if self.barrier is SubmissionBarrier.SEND_MAY_HAVE_OCCURRED_ID_UNKNOWN:
            active.bridge_server.release_send.set()
            return
        try:
            active.control.send({"continue": True})
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def _finish_interrupted_child(self, active: _ActiveChild) -> ProcessResult:
        try:
            active.process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            active.process.kill()
            active.process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
            active.bridge_server.release_send.set()
            self._collect(active)
            raise AssertionError("interrupted CLI child did not exit") from error
        finally:
            # A killed caller cannot acknowledge the bridge response. Release
            # the independent server only after process death is observed.
            active.bridge_server.release_send.set()
        return self._collect(active)

    def _finish_child(self, active: _ActiveChild) -> ProcessResult:
        try:
            active.process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            active.process.kill()
            active.process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
            self._collect(active)
            raise AssertionError("CLI child did not exit") from error
        return self._collect(active)

    def _collect(self, active: _ActiveChild) -> ProcessResult:
        stdout, stderr = active.process.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
        active.control.connection.close()
        active.bridge_server.release_send.set()
        active.bridge_server.join(timeout=PROCESS_TIMEOUT_SECONDS)
        if active.bridge_server.is_alive():
            raise AssertionError("scripted bridge server did not stop")
        if active.bridge_server.failure is not None:
            raise active.bridge_server.failure
        return ProcessResult(active.process.returncode, stdout, stderr)
