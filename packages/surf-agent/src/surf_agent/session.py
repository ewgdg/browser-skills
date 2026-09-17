"""Persistent per-session Python interpreter for Surf.

One isolated interpreter per agent session, sequential cells, over a unix
socket. The worker is a detached process: it never inherits the caller's
stdout pipe, its own fd 1 and 2 point at a per-session log, and cell output
travels back over the control socket.

See `plans/active/persistent-interpreter.md` for the settled contracts.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import SurfAgentError
from .runtime import surf_agent_state_dir

DEFAULT_CELL_TIMEOUT_S = 300.0
# Long enough to cover reply transfer after a cell that finished inside its own deadline.
REPLY_GRACE_S = 10.0
HELLO_TIMEOUT_S = 5.0
WORKER_START_TIMEOUT_S = 10.0
OWNER_WATCH_INTERVAL_S = 2.0
# The launcher resolves its dependency manager into this handoff. Direct imports
# of this module (tests, other harnesses) start the worker with sys.executable.
WORKER_COMMAND_ENV = "SURF_SESSION_WORKER_COMMAND"
SOCKET_BACKLOG = 8
# Linux allows 107 bytes for sun_path plus the terminating NUL.
SOCKET_PATH_LIMIT = 107
# The durable harness process owns a session. Anything between it and the
# caller is transient: the launcher itself and one shell per tool call.
HARNESS_COMMS = frozenset({"pi"})
SESSION_MANAGER_COMMS = frozenset({"systemd", "init"})
DEAD_STATES = frozenset({"Z", "X", "x"})
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


class SessionError(SurfAgentError):
    """The session interpreter could not be started or spoken to."""


class _InterpreterGone(Exception):
    """The interpreter's control channel is closed; its bindings are gone."""


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    comm: str
    state: str
    ppid: int
    start_time: int


@dataclass(frozen=True)
class OwnerRef:
    """Identity of the process that must outlive the interpreter."""

    pid: int
    start_time: int


@dataclass(frozen=True)
class CellResult:
    status: str  # "ok", "error", "busy" or "replaced"
    interpreter_pid: int
    cell_number: int
    created: bool
    detail: str | None
    stdout: bytes
    stderr: bytes
    duration_s: float


@dataclass(frozen=True)
class ResetResult:
    status: str  # "ok", "busy" or "absent"
    interpreter_pid: int | None
    cell_number: int | None


def read_process(pid: int) -> ProcessInfo | None:
    """Read the process facts an owner reference needs; None when it is gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    try:
        # The comm field may contain spaces and parentheses; split from the right.
        before, after = raw.rsplit(b") ", 1)
        fields = after.split()
        return ProcessInfo(
            pid=int(before.split(b"(", 1)[0]),
            comm=before.split(b"(", 1)[1].decode(errors="replace"),
            state=fields[0].decode(errors="replace"),
            ppid=int(fields[1]),
            start_time=int(fields[19]),
        )
    except (IndexError, ValueError):
        return None


def process_alive(pid: int, start_time: int) -> bool:
    """Prove the process is still the recorded one: same start, not a zombie."""
    info = read_process(pid)
    return (
        info is not None
        and info.state not in DEAD_STATES
        and info.start_time == start_time
    )


def ancestor_chain(pid: int | None = None) -> list[ProcessInfo]:
    """The caller's process at index 0, then each ancestor up to pid 1."""
    chain: list[ProcessInfo] = []
    current = os.getpid() if pid is None else pid
    while current > 1:
        info = read_process(current)
        if info is None or any(entry.pid == info.pid for entry in chain):
            break
        chain.append(info)
        current = info.ppid
    return chain


def resolve_owner() -> OwnerRef:
    """Record the session process that must outlive the interpreter.

    The immediate parent is a per-tool-call shell, so it is never the owner.
    When no known harness process is in the chain, fall back to the outermost
    non-init ancestor, which is the terminal or service that started the call.
    """
    chain = ancestor_chain()
    if not chain:
        raise SessionError("could not read the process ancestor chain to find the session owner")
    for info in chain[1:]:
        if info.comm in HARNESS_COMMS:
            return OwnerRef(info.pid, info.start_time)
    for info in reversed(chain):
        if info.comm not in SESSION_MANAGER_COMMS:
            return OwnerRef(info.pid, info.start_time)
    return OwnerRef(chain[0].pid, chain[0].start_time)


def session_identity() -> str | None:
    value = os.environ.get("PI_SESSION_ID")
    if value:
        return value
    session_file = os.environ.get("PI_SESSION_FILE")
    return Path(session_file).stem if session_file else None


def session_key(name: str) -> str:
    """Namespace one session name under the harness session that asked for it."""
    identity = session_identity()
    return f"{identity}:{name}" if identity else name


def session_socket_dir() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime_dir) if runtime_dir else surf_agent_state_dir()
    return root / "surf-agent"


def session_socket_path(name: str) -> Path:
    directory = session_socket_dir()
    digest = hashlib.sha256(session_key(name).encode()).hexdigest()[:16]
    slug = _SLUG.sub("-", name).strip("-.")[:32] or "session"
    stem = f"{slug}-{digest}"
    if len(os.fsencode(str(directory / f"{stem}.sock"))) > SOCKET_PATH_LIMIT:
        # Deep runtime directories leave only the identity digest.
        stem = digest
    if len(os.fsencode(str(directory / f"{stem}.sock"))) > SOCKET_PATH_LIMIT:
        raise SessionError(f"session socket path is too long under {directory}")
    return directory / f"{stem}.sock"


def session_log_path(name: str) -> Path:
    """Worker stdout/stderr for a session: raw fd writes and subprocess output."""
    return session_socket_path(name).with_suffix(".log")


def session_lock_path(name: str) -> Path:
    return session_socket_path(name).with_suffix(".lock")


def _prepare_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise SessionError(f"could not prepare the session runtime directory {directory}: {exc}") from exc


@contextlib.contextmanager
def _session_lock(name: str):
    """Serialize attach-or-spawn so two first cells cannot diverge into two interpreters."""
    path = session_lock_path(name)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise SessionError(f"could not open the session lock {path}: {exc}") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_reply(stream: Any) -> dict[str, Any]:
    try:
        line = stream.readline()
    except OSError as exc:
        raise _InterpreterGone(f"interpreter did not answer: {exc}") from exc
    if not line:
        raise _InterpreterGone("interpreter closed the control channel")
    try:
        reply = json.loads(line)
    except ValueError as exc:
        raise SessionError(f"interpreter sent an unreadable reply: {line[:200]!r}") from exc
    if not isinstance(reply, dict):
        raise SessionError("interpreter sent a non-object reply")
    return reply


def _connect(socket_path: Path, timeout_s: float) -> socket.socket:
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout_s)
        connection.connect(str(socket_path))
    except OSError as exc:
        raise _InterpreterGone(f"could not reach the interpreter: {exc}") from exc
    return connection


def _exchange(socket_path: Path, request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    with _connect(socket_path, timeout_s) as connection, connection.makefile("rwb") as stream:
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
        return _read_reply(stream)


def _exchange_cell(
    socket_path: Path,
    request: dict[str, Any],
    timeout_s: float,
    on_started: Callable[[int], None],
) -> dict[str, Any]:
    """Send one cell and report the worker-assigned number before it executes."""
    started_at = time.monotonic()
    with _connect(socket_path, timeout_s) as connection, connection.makefile("rwb") as stream:
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
        started = _read_reply(stream)
        if started.get("status") != "started":
            return started
        on_started(int(started["cells"]))
        connection.settimeout(max(0.1, timeout_s - (time.monotonic() - started_at)))
        return _read_reply(stream)


def _hello(socket_path: Path, timeout_s: float) -> dict[str, Any] | None:
    if not socket_path.exists():
        return None
    try:
        return _exchange(socket_path, {"op": "hello"}, timeout_s)
    except _InterpreterGone:
        return None


def _worker_command() -> list[str]:
    """The command that starts a worker process.

    Under `uv run --with`, sys.executable points into a temporary environment
    that uv removes when its caller exits, so a detached worker could not start
    the browser bridge from a later cell. The launcher therefore supplies a
    command that re-enters its own dependency resolution and keeps it alive for
    the worker's lifetime.
    """
    configured = os.environ.get(WORKER_COMMAND_ENV)
    if configured is None:
        return [sys.executable, "-m", "surf_agent.session", "worker"]
    try:
        command = json.loads(configured)
    except ValueError as exc:
        raise SessionError(f"{WORKER_COMMAND_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise SessionError(f"{WORKER_COMMAND_ENV} must be a JSON list of strings")
    return command


def _spawn_interpreter(
    socket_path: Path, log_path: Path, owner: OwnerRef
) -> tuple[int, int, bool, int]:
    descriptor = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen(
            [
                *_worker_command(),
                str(socket_path), str(owner.pid), str(owner.start_time),
            ],
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=descriptor,
            start_new_session=True,
        )
    finally:
        os.close(descriptor)
    deadline = time.monotonic() + WORKER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        reply = _hello(socket_path, HELLO_TIMEOUT_S)
        if reply is not None:
            return int(reply["pid"]), int(reply["start_time"]), True, int(reply["cells"])
        if process.poll() is not None:
            # Another client may have won the bind race; adopt its interpreter.
            reply = _hello(socket_path, HELLO_TIMEOUT_S)
            if reply is not None:
                return int(reply["pid"]), int(reply["start_time"]), True, int(reply["cells"])
            raise SessionError(f"session interpreter failed to start; see {log_path}")
        time.sleep(0.02)
    process.kill()
    raise SessionError(f"session interpreter did not become ready; see {log_path}")


def _ensure_interpreter(
    name: str, owner: OwnerRef
) -> tuple[int, int, bool, int]:
    """Attach to the session interpreter, replacing a stale one."""
    socket_path = session_socket_path(name)
    log_path = session_log_path(name)
    _prepare_directory(log_path.parent)
    with _session_lock(name):
        reply = _hello(socket_path, HELLO_TIMEOUT_S)
        if reply is not None:
            return int(reply["pid"]), int(reply["start_time"]), False, int(reply["cells"])
        if socket_path.exists():
            # A reaped or killed worker leaves its socket file behind.
            with contextlib.suppress(OSError):
                socket_path.unlink()
        return _spawn_interpreter(socket_path, log_path, owner)


def _kill_interpreter(pid: int, start_time: int) -> None:
    if process_alive(pid, start_time):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def _frame(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _write_bytes(stream: Any, data: bytes) -> None:
    if not data:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        stream.write(data.decode("utf-8", errors="replace"))
        stream.flush()


def _encode(stream: Any) -> str:
    return base64.b64encode(stream.buffer.getvalue()).decode("ascii")


def _decode(value: Any) -> bytes:
    return base64.b64decode(value) if value else b""


def run_cell(
    name: str,
    code: str,
    *,
    argv: tuple[str, ...] = ("-",),
    timeout_s: float = DEFAULT_CELL_TIMEOUT_S,
    owner: OwnerRef | None = None,
) -> CellResult:
    """Run one cell in the named session interpreter, creating it if needed.

    Cell bytes are relayed to this process's stdout/stderr; launcher frames go
    to stderr. The interpreter is destroyed when the cell exceeds *timeout_s*.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise SessionError("cell timeout must be a positive number of seconds")
    owner = owner or resolve_owner()
    socket_path = session_socket_path(name)
    pid, start_time, created, cells = _ensure_interpreter(name, owner)
    origin = f"created; log {session_log_path(name)}" if created else "attached"

    started = time.monotonic()
    number = cells + 1

    def on_started(assigned: int) -> None:
        nonlocal number
        number = assigned
        _frame(f"--- interpreter {pid} (cell #{number}, {origin}) ---")

    try:
        reply = _exchange_cell(
            socket_path,
            {"op": "cell", "code": code, "argv": list(argv), "timeout_s": timeout_s},
            timeout_s + REPLY_GRACE_S,
            on_started,
        )
    except _InterpreterGone:
        elapsed = time.monotonic() - started
        _kill_interpreter(pid, start_time)
        reason = (
            f"cell #{number} exceeded {timeout_s:g} s"
            if elapsed >= timeout_s
            else f"worker exited during cell #{number}"
        )
        _frame(f"--- interpreter replaced ({reason}); bindings lost; side effects unknown ---")
        return CellResult("replaced", pid, number, created, reason, b"", b"", elapsed)

    elapsed = time.monotonic() - started
    status = reply.get("status")
    if status == "busy":
        _frame(f"--- interpreter {pid} busy; no cell started ---")
        return CellResult("busy", pid, number, created, None, b"", b"", elapsed)
    if status not in {"ok", "error"}:
        raise SessionError(f"interpreter sent an unexpected reply: {reply!r}")
    stdout, stderr = _decode(reply.get("stdout")), _decode(reply.get("stderr"))
    _write_bytes(sys.stdout, stdout)
    _write_bytes(sys.stderr, stderr)
    detail = reply.get("exception")
    suffix = f": {detail}" if detail else ""
    _frame(f"--- cell #{number} {status}{suffix} ({elapsed * 1000:.0f} ms) ---")
    return CellResult(status, pid, number, created, detail, stdout, stderr, elapsed)


def reset_bindings(name: str, *, owner: OwnerRef | None = None) -> ResetResult:
    """Discard Python bindings in the session interpreter; never replace it."""
    owner = owner or resolve_owner()
    _prepare_directory(session_log_path(name).parent)
    with _session_lock(name):
        socket_path = session_socket_path(name)
        reply = _hello(socket_path, HELLO_TIMEOUT_S)
        if reply is None:
            if socket_path.exists():
                with contextlib.suppress(OSError):
                    socket_path.unlink()
            _frame(f"--- no interpreter for session {name!r}; bindings are already gone ---")
            return ResetResult("absent", None, None)
    pid, cells = int(reply["pid"]), int(reply["cells"])
    try:
        answer = _exchange(socket_path, {"op": "reset"}, HELLO_TIMEOUT_S)
    except _InterpreterGone:
        _frame(f"--- interpreter {pid} vanished before reset; bindings are gone ---")
        return ResetResult("absent", None, None)
    status = answer.get("status")
    if status == "busy":
        _frame(f"--- interpreter {pid} busy; bindings not cleared ---")
        return ResetResult("busy", pid, cells)
    if status != "ok":
        raise SessionError(f"interpreter sent an unexpected reply: {answer!r}")
    _frame(f"--- interpreter {pid} bindings cleared (cell #{cells}) ---")
    return ResetResult("ok", pid, cells)


class _WorkerState:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.namespace: dict[str, Any] = {"__name__": "__main__"}
        self.cells = 0
        self.running = False
        self.lock = threading.Lock()


def _shutdown(state: _WorkerState, status: int) -> None:
    # Do not leave a socket name behind for a process that no longer accepts cells.
    with contextlib.suppress(OSError):
        state.socket_path.unlink()
    os._exit(status)


def _capture_stream() -> io.TextIOWrapper:
    # write_through keeps text and sys.stdout.buffer writes ordered in one buffer.
    return io.TextIOWrapper(
        io.BytesIO(), encoding="utf-8", errors="replace", newline="", write_through=True
    )


def _expire(state: _WorkerState, number: int) -> None:
    print(f"cell #{number} exceeded its deadline; interpreter exiting", file=sys.stderr, flush=True)
    _shutdown(state, 1)


def _write_reply(stream: Any, reply: dict[str, Any]) -> None:
    stream.write(json.dumps(reply).encode() + b"\n")
    stream.flush()


def _run_cell_request(
    state: _WorkerState, request: dict[str, Any], stream: Any
) -> dict[str, Any]:
    with state.lock:
        if state.running:
            return {"status": "busy", "cells": state.cells}
        state.running = True
        state.cells += 1
        number = state.cells
        namespace = state.namespace
    # Report the assigned number before executing: the caller frames a cell that
    # then dies, and concurrent callers must not both claim the same number.
    _write_reply(stream, {"status": "started", "cells": number})
    timeout_s = float(request.get("timeout_s", DEFAULT_CELL_TIMEOUT_S))
    argv = [str(part) for part in request.get("argv") or ["-"]]
    stdout, stderr = _capture_stream(), _capture_stream()
    expiration = threading.Timer(timeout_s, _expire, args=(state, number))
    expiration.daemon = True
    expiration.start()
    status, detail = "ok", None
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            sys.argv = argv
            try:
                exec(compile(request["code"], "<cell>", "exec"), namespace)
            except BaseException:
                status = "error"
                traceback.print_exc()
                detail = traceback.format_exc().strip().splitlines()[-1]
    finally:
        expiration.cancel()
        with state.lock:
            state.running = False
    return {
        "status": status,
        "cells": number,
        "exception": detail,
        "stdout": _encode(stdout),
        "stderr": _encode(stderr),
    }


def _reset_request(state: _WorkerState) -> dict[str, Any]:
    with state.lock:
        if state.running:
            return {"status": "busy", "cells": state.cells}
        # Old bindings are dropped, not mutated: their objects survive only if the
        # cell itself kept a reference elsewhere. Browser threads are untouched.
        state.namespace = {"__name__": "__main__"}
        return {"status": "ok", "cells": state.cells}


def _hello_reply(state: _WorkerState) -> dict[str, Any]:
    info = read_process(os.getpid())
    return {
        "status": "hello",
        "pid": os.getpid(),
        "start_time": info.start_time if info is not None else 0,
        "cells": state.cells,
    }


def _handle_connection(connection: socket.socket, state: _WorkerState) -> None:
    with connection, connection.makefile("rwb") as stream:
        line = stream.readline()
        if not line:
            return
        try:
            request = json.loads(line)
            if request.get("op") == "hello":
                reply = _hello_reply(state)
            elif request.get("op") == "cell":
                reply = _run_cell_request(state, request, stream)
            elif request.get("op") == "reset":
                reply = _reset_request(state)
            else:
                reply = {"status": "error", "error": f"unknown operation {request.get('op')!r}"}
        except Exception as exc:  # keep the interpreter alive for the next cell
            reply = {"status": "error", "exception": f"{type(exc).__name__}: {exc}", "cells": state.cells}
        _write_reply(stream, reply)


def _watch_owner(state: _WorkerState, owner: OwnerRef) -> None:
    while True:
        time.sleep(OWNER_WATCH_INTERVAL_S)
        if not process_alive(owner.pid, owner.start_time):
            _shutdown(state, 0)


def serve(socket_path: Path, owner: OwnerRef) -> None:
    """Run the worker: accept connections, run one cell at a time."""
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(socket_path.parent, 0o700)
    state = _WorkerState(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    server.listen(SOCKET_BACKLOG)
    threading.Thread(target=_watch_owner, args=(state, owner), daemon=True).start()
    print(f"surf session interpreter ready (pid {os.getpid()})", file=sys.stderr, flush=True)
    try:
        while True:
            connection, _ = server.accept()
            threading.Thread(
                target=_handle_connection, args=(connection, state), daemon=True
            ).start()
    finally:
        server.close()
        with contextlib.suppress(OSError):
            socket_path.unlink()


_USAGE = """usage:
  run.py --session NAME [--timeout SECONDS] - [arguments...]
  run.py --session NAME --reset
"""


def _main_cell(arguments: list[str]) -> int:
    name: str | None = None
    timeout_s = DEFAULT_CELL_TIMEOUT_S
    index = 0
    while index < len(arguments) and arguments[index].startswith("--"):
        option = arguments[index]
        if option == "--session" and index + 1 < len(arguments):
            name = arguments[index + 1]
            index += 2
        elif option == "--timeout" and index + 1 < len(arguments):
            try:
                timeout_s = float(arguments[index + 1])
            except ValueError:
                print(f"surf: --timeout expects seconds, got {arguments[index + 1]!r}", file=sys.stderr)
                return 2
            index += 2
        else:
            print(_USAGE, file=sys.stderr, end="")
            return 2
    argv = arguments[index:]
    if name is None or not argv or argv[0] != "-":
        print(_USAGE, file=sys.stderr, end="")
        return 2
    try:
        result = run_cell(name, sys.stdin.read(), argv=tuple(argv), timeout_s=timeout_s)
    except SessionError as exc:
        print(f"surf: {exc}", file=sys.stderr)
        return 2
    return 0 if result.status == "ok" else 1


def _main_reset(arguments: list[str]) -> int:
    name: str | None = None
    index = 0
    while index < len(arguments) and arguments[index].startswith("--"):
        if arguments[index] == "--session" and index + 1 < len(arguments):
            name = arguments[index + 1]
            index += 2
        else:
            print(_USAGE, file=sys.stderr, end="")
            return 2
    if name is None or index != len(arguments):
        print(_USAGE, file=sys.stderr, end="")
        return 2
    try:
        result = reset_bindings(name)
    except SessionError as exc:
        print(f"surf: {exc}", file=sys.stderr)
        return 2
    return 0 if result.status in {"ok", "absent"} else 1


def _main_worker(arguments: list[str]) -> None:
    if len(arguments) != 3:
        print("usage: python -m surf_agent.session worker SOCKET OWNER_PID OWNER_START",
              file=sys.stderr)
        raise SystemExit(2)
    socket_path, owner_pid, owner_start = arguments
    serve(Path(socket_path), OwnerRef(int(owner_pid), int(owner_start)))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print(_USAGE, file=sys.stderr, end="")
        return 2
    command, rest = arguments[0], arguments[1:]
    if command == "worker":
        _main_worker(rest)
        return 0
    if command == "cell":
        return _main_cell(rest)
    if command == "reset":
        return _main_reset(rest)
    print(_USAGE, file=sys.stderr, end="")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
