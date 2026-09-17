"""Persistent per-session Python interpreters for Surf.

One explicitly created interpreter per session, sequential cells, over a unix
socket. A session is addressed by the id that --new-session reports and later
cells pass back; it ends when it has been idle for its timeout or when a
--kill-session request stops it.

The worker is a detached process: it never inherits the caller's stdout pipe.
Its own fd 1 and 2 start on a pipe the launcher reads only when startup fails,
then point at /dev/null, and cell output travels back over the control socket.

See `plans/active/explicit-sessions.md` for the settled contracts.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import math
import os
import re
import secrets
import select
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
DEFAULT_SESSION_IDLE_TIMEOUT_S = 1800.0
# Long enough to cover reply transfer after a cell that finished inside its own deadline.
REPLY_GRACE_S = 10.0
HELLO_TIMEOUT_S = 5.0
WORKER_START_TIMEOUT_S = 10.0
# How often an idle worker checks whether its timeout has passed.
IDLE_POLL_S = 0.5
# The launcher resolves its dependency manager into this handoff. Direct imports
# of this module (tests, other harnesses) start the worker with sys.executable.
WORKER_COMMAND_ENV = "SURF_SESSION_WORKER_COMMAND"
SOCKET_BACKLOG = 8
# Linux allows 107 bytes for sun_path plus the terminating NUL.
SOCKET_PATH_LIMIT = 107
# Ids are file names, so they stay inside one path segment and cannot begin with
# a dash or a dot.
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
_SOCKET_SUFFIX = ".sock"

# Workers this process started. A worker that exits stays a zombie until its
# creator reaps it: a one-shot launcher exits immediately, but a long-lived
# process that creates sessions must reap its own children as they finish.
_children: list[subprocess.Popen] = []


class SessionError(SurfAgentError):
    """The session interpreter could not be started or spoken to."""


class _InterpreterGone(Exception):
    """The interpreter's control channel is closed; its bindings are gone.

    A *stalled* channel was reachable but did not answer, which means the
    interpreter may still be alive and must not be replaced silently.
    """

    def __init__(self, message: str, *, stalled: bool = False) -> None:
        super().__init__(message)
        self.stalled = stalled


@dataclass(frozen=True)
class CellResult:
    status: str  # "ok", "error", "busy" or "replaced"
    session_id: str
    interpreter_pid: int
    cell_number: int | None  # None when no cell started
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


@dataclass(frozen=True)
class SessionEntry:
    session_id: str
    interpreter_pid: int
    cells: int
    idle_s: float
    uptime_s: float
    idle_timeout_s: float
    cwd: str


def new_session_id(name: str | None = None) -> str:
    """A fresh id: an optional readable slug plus random hex.

    The random part is what keeps two callers from ever choosing the same
    session, whatever name they pass.
    """
    suffix = secrets.token_hex(4)
    if not name:
        return suffix
    slug = _SLUG.sub("-", name).strip("-.")[:32].strip("-.")
    return f"{slug}-{suffix}" if slug else suffix


def _validate_session_id(session_id: str) -> str:
    # Ids are opaque: only ids that exist are accepted, so a typo cannot silently
    # start a second interpreter.
    if not _SESSION_ID.fullmatch(session_id):
        raise SessionError(f"invalid session id {session_id!r}; ids come from --new-session")
    return session_id


def session_socket_dir() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(runtime_dir) if runtime_dir else surf_agent_state_dir()
    return root / "surf-agent"


def session_socket_path(session_id: str) -> Path:
    """The socket file is the session's whole record: no registry, no state file."""
    directory = session_socket_dir()
    path = directory / f"{session_id}{_SOCKET_SUFFIX}"
    if len(os.fsencode(str(path))) > SOCKET_PATH_LIMIT:
        raise SessionError(
            f"session socket path is too long under {directory}; use a shorter --name"
        )
    return directory / f"{session_id}{_SOCKET_SUFFIX}"


def _prepare_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise SessionError(f"could not prepare the session runtime directory {directory}: {exc}") from exc


def _read_reply(stream: Any) -> dict[str, Any]:
    try:
        line = stream.readline()
    except TimeoutError as exc:
        raise _InterpreterGone(f"interpreter did not answer: {exc}", stalled=True) from exc
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
    except TimeoutError as exc:
        # A unix connect only blocks when the accept queue is full, so a listener
        # exists but is not accepting: the stalled condition, not a stale socket.
        raise _InterpreterGone(f"could not reach the interpreter: {exc}", stalled=True) from exc
    except BlockingIOError as exc:
        # A saturated accept queue reports EAGAIN immediately instead of blocking.
        raise _InterpreterGone(f"could not reach the interpreter: {exc}", stalled=True) from exc
    except OSError as exc:
        raise _InterpreterGone(f"could not reach the interpreter: {exc}") from exc
    return connection


def _send_request(stream: Any, request: dict[str, Any]) -> None:
    try:
        stream.write(json.dumps(request).encode() + b"\n")
        stream.flush()
    except TimeoutError as exc:
        raise _InterpreterGone(f"interpreter did not accept the request: {exc}", stalled=True) from exc
    except OSError as exc:
        # The interpreter died or closed between attaching and this request.
        raise _InterpreterGone(f"interpreter did not accept the request: {exc}") from exc


def _exchange(socket_path: Path, request: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    with _connect(socket_path, timeout_s) as connection, connection.makefile("rwb") as stream:
        _send_request(stream, request)
        return _read_reply(stream)


def _exchange_cell(
    socket_path: Path,
    request: dict[str, Any],
    wait_s: float,
    on_started: Callable[[int, int], None],
) -> dict[str, Any]:
    """Send one cell and report the worker-assigned number before it executes.

    *wait_s* covers the cell deadline plus reply transfer. The worker owns the
    deadline, so this wait is only a backstop and the remaining allowance is
    still wide enough for a long result to finish transferring.
    """
    started_at = time.monotonic()
    with _connect(socket_path, wait_s) as connection, connection.makefile("rwb") as stream:
        _send_request(stream, request)
        started = _read_reply(stream)
        if started.get("status") != "started":
            return started
        try:
            interpreter_pid, number = int(started["pid"]), int(started["cells"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionError(
                f"interpreter sent an incomplete started reply: {started!r}"
            ) from exc
        on_started(interpreter_pid, number)
        connection.settimeout(max(0.1, wait_s - (time.monotonic() - started_at)))
        return _read_reply(stream)


def _listener_pid(socket_path: Path) -> int | None:
    """The worker process whose command line names this session socket.

    Reading /proc keeps the diagnostic independent of the socket's accept queue,
    which can be full exactly when a suspended worker is not accepting, and it is
    what makes killing safe: a recycled pid cannot claim this socket's name.
    """
    wanted = str(socket_path)
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        arguments = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if not arguments or not Path(arguments[0]).name.startswith("python"):
            continue
        joined = " ".join(arguments)
        # Worker shape: `python -m surf_agent.session worker ...` or that command
        # inside an inline `-c` bootstrap, always with this session's socket path.
        if wanted not in arguments or "surf_agent.session" not in joined:
            continue
        if ("-m" in arguments or "-c" in arguments) and "worker" in joined:
            return int(entry.name)
    return None


def _stalled_error(socket_path: Path, session_id: str) -> SessionError:
    pid = _listener_pid(socket_path)
    who = f" (pid {pid})" if pid is not None else ""
    return SessionError(
        f"the session interpreter{who} did not answer; stop it with "
        f"--kill-session {session_id} and retry, or resume that process"
    )


def _hello(socket_path: Path, timeout_s: float) -> dict[str, Any] | None:
    if not socket_path.exists():
        return None
    try:
        return _exchange(socket_path, {"op": "hello"}, timeout_s)
    except _InterpreterGone as exc:
        if exc.stalled:
            # A live interpreter that cannot answer any thread is stopped or
            # wedged. Treating it as absent would leave the id pointing at a
            # process nobody can talk to, so fail fast and name it.
            raise _stalled_error(socket_path, socket_path.name[: -len(_SOCKET_SUFFIX)]) from exc
        return None


def _hello_quietly(socket_path: Path, timeout_s: float) -> dict[str, Any] | None:
    """Spawn-time and listing probe: an interpreter that cannot answer is not usable."""
    try:
        return _hello(socket_path, timeout_s)
    except SessionError:
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
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) for part in command
    ):
        raise SessionError(f"{WORKER_COMMAND_ENV} must be a non-empty JSON list of strings")
    return command


# A worker started by an earlier runtime answers hello without these fields. It
# cannot be listed or spoken to by this one; it exits with the process that made it.
_HELLO_FIELDS = ("ttl_s", "idle_s", "uptime_s")


def _compatible_reply(reply: dict[str, Any], session_id: str) -> dict[str, Any]:
    if all(field in reply for field in _HELLO_FIELDS):
        return reply
    raise SessionError(
        f"session {session_id!r} runs an interpreter this runtime does not speak to; "
        "it exits with the process that created it"
    )


def _startup_output(process: subprocess.Popen) -> str:
    """What the worker wrote before it detached its descriptors.

    The launcher reads the pipe only on failure, and only after the worker is
    gone, so a bounded wait is enough to prove there is nothing to read.
    """
    stream = process.stdout
    if stream is None:
        return "no output"
    try:
        ready, _, _ = select.select([stream], [], [], 1.0)
        if not ready:
            return "no output"
        text = stream.read().decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        text = ""
    finally:
        with contextlib.suppress(OSError):
            stream.close()
    return text or "no output"


def _release_startup_pipe(process: subprocess.Popen) -> None:
    if process.stdout is not None:
        with contextlib.suppress(OSError):
            process.stdout.close()


def _reap_children(wait_s: float = 0.0) -> None:
    """Reap finished workers; every entry point calls this.

    *wait_s* gives a caller that has just asked a worker to stop time to see it
    exit, so "stopped" means the process is gone rather than merely dying.
    """
    deadline = time.monotonic() + wait_s
    while True:
        pending = False
        for process in list(_children):
            if process.poll() is not None:
                with contextlib.suppress(ValueError):
                    _children.remove(process)
            else:
                pending = True
        if not pending or time.monotonic() >= deadline:
            return
        time.sleep(0.02)


def _start_interpreter(socket_path: Path, idle_timeout_s: float) -> tuple[int, int]:
    """Spawn a worker for this socket path and wait until it accepts cells.

    Returns the interpreter pid and its cell count.
    """
    try:
        process = subprocess.Popen(
            [*_worker_command(), str(socket_path), f"{idle_timeout_s:g}"],
            stdin=subprocess.DEVNULL,
            # A detached worker must not hold the caller's stdout pipe, or the tool
            # call never sees EOF. This pipe belongs to the launcher alone; the
            # worker releases it once it listens, and it is read only on failure.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise SessionError(f"could not start the session interpreter: {exc}") from exc
    deadline = time.monotonic() + WORKER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        reply = _hello_quietly(socket_path, HELLO_TIMEOUT_S)
        if reply is not None:
            _release_startup_pipe(process)
            _children.append(process)
            return int(reply["pid"]), int(reply["cells"])
        if process.poll() is not None:
            raise SessionError(
                f"session interpreter failed to start: {_startup_output(process)}"
            )
        time.sleep(0.02)
    process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=WORKER_START_TIMEOUT_S)
    raise SessionError(
        f"session interpreter did not become ready: {_startup_output(process)}"
    )


def _kill_interpreter(socket_path: Path, pid: int) -> None:
    # Only the process that still names this socket may be killed: a recycled pid
    # must never be signalled.
    if _listener_pid(socket_path) != pid:
        return
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
    try:
        return base64.b64encode(stream.buffer.getvalue()).decode("ascii")
    except (ValueError, OSError):
        # A cell that closed its own stdout or stderr already discarded its bytes.
        return ""


def _decode(value: Any) -> bytes:
    return base64.b64decode(value) if value else b""


def _metadata_block(session_id: str, idle_timeout_s: float) -> bytes:
    # Named delimiters rather than bare `---` fences: the same tool result can hold
    # observation frames and unified diffs, whose headers start with `---` too.
    return (
        "--- BEGIN session metadata ---\n"
        f"session_id: {session_id}\n"
        f"idle_timeout_s: {idle_timeout_s:g}\n"
        "--- END session metadata ---\n"
    ).encode()


def _duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _discard_stale_socket(socket_path: Path) -> None:
    """Remove a socket file nothing is listening on.

    Only once it is older than a worker start: a worker that has just bound its
    socket but is not yet accepting would otherwise lose its name.
    """
    try:
        if time.time() - socket_path.stat().st_mtime < WORKER_START_TIMEOUT_S:
            return
        socket_path.unlink()
    except OSError:
        pass


def _session_entry(session_id: str, reply: dict[str, Any]) -> SessionEntry:
    def number(key: str) -> float:
        try:
            return float(reply[key])
        except (KeyError, TypeError, ValueError):
            return 0.0

    return SessionEntry(
        session_id=session_id,
        interpreter_pid=int(number("pid")),
        cells=int(number("cells")),
        idle_s=number("idle_s"),
        uptime_s=number("uptime_s"),
        idle_timeout_s=number("ttl_s"),
        cwd=str(reply.get("cwd") or ""),
    )


def list_sessions() -> list[SessionEntry]:
    """Live sessions, discovered from the socket directory that names them."""
    _reap_children()
    try:
        paths = sorted(session_socket_dir().glob(f"*{_SOCKET_SUFFIX}"))
    except OSError:
        return []
    entries: list[SessionEntry] = []
    for path in paths:
        session_id = path.name[: -len(_SOCKET_SUFFIX)]
        reply = _hello_quietly(path, HELLO_TIMEOUT_S)
        if reply is None:
            _discard_stale_socket(path)
            continue
        if not all(field in reply for field in _HELLO_FIELDS):
            _frame(f"--- ignoring session {session_id}: different interpreter protocol ---")
            continue
        entries.append(_session_entry(session_id, reply))
    return entries


def _unknown_session_error(session_id: str) -> SessionError:
    live = ", ".join(entry.session_id for entry in list_sessions())
    known = f"live sessions: {live}" if live else "no live sessions"
    return SessionError(f"unknown session {session_id!r} ({known}); create one with --new-session")


def kill_session(session_id: str, *, timeout_s: float = WORKER_START_TIMEOUT_S) -> bool:
    """Stop a session interpreter; True when one was there to stop."""
    _reap_children()
    _validate_session_id(session_id)
    socket_path = session_socket_path(session_id)
    reply = _hello(socket_path, HELLO_TIMEOUT_S)
    if reply is None:
        _discard_stale_socket(socket_path)
        return False
    _compatible_reply(reply, session_id)
    answer = _exchange(socket_path, {"op": "shutdown"}, HELLO_TIMEOUT_S)
    if answer.get("status") != "ok":
        raise SessionError(f"interpreter sent an unexpected reply: {answer!r}")
    # The worker removes its own socket; waiting keeps a following --list-sessions
    # from reporting a session that is already gone.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and socket_path.exists():
        time.sleep(0.02)
    # A worker removes its socket just before exiting, so wait for the exit too:
    # otherwise a stopped session leaves a zombie behind its own confirmation.
    _reap_children(wait_s=min(timeout_s, 2.0))
    with contextlib.suppress(OSError):
        socket_path.unlink()
    return True


def run_cell(
    session_id: str,
    code: str,
    *,
    create: bool = False,
    idle_timeout_s: float = DEFAULT_SESSION_IDLE_TIMEOUT_S,
    argv: tuple[str, ...] = ("-",),
    timeout_s: float = DEFAULT_CELL_TIMEOUT_S,
) -> CellResult:
    """Run one cell in a session interpreter.

    With *create*, a new interpreter is started for *session_id* and this call
    reports it in a metadata block. Otherwise the call attaches to the existing
    interpreter for that id and fails when there is none.

    Cell bytes are relayed to this process's stdout/stderr; launcher frames go to
    stderr. The interpreter is destroyed when the cell exceeds *timeout_s*.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise SessionError("cell timeout must be a positive number of seconds")
    if not math.isfinite(idle_timeout_s) or idle_timeout_s <= 0:
        raise SessionError("session idle timeout must be a positive number of seconds")
    _validate_session_id(session_id)
    _reap_children()
    socket_path = session_socket_path(session_id)
    _prepare_directory(socket_path.parent)
    if create:
        pid, cells = _start_interpreter(socket_path, idle_timeout_s)
        origin = f"created; idle timeout {idle_timeout_s:g} s"
    else:
        reply = _hello(socket_path, HELLO_TIMEOUT_S)
        if reply is None:
            _discard_stale_socket(socket_path)
            raise _unknown_session_error(session_id)
        _compatible_reply(reply, session_id)
        try:
            pid, cells = int(reply["pid"]), int(reply["cells"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionError(f"interpreter sent an incomplete hello reply: {reply!r}") from exc
        origin = "attached"

    started = time.monotonic()
    number = cells + 1
    result_pid = pid
    cell_started = False

    def on_started(interpreter_pid: int, assigned: int) -> None:
        nonlocal number, result_pid, cell_started
        number = assigned
        result_pid = interpreter_pid
        cell_started = True
        _frame(f"--- interpreter {interpreter_pid} (cell #{number}, {origin}) ---")

    try:
        reply = _exchange_cell(
            socket_path,
            {"op": "cell", "code": code, "argv": list(argv), "timeout_s": timeout_s},
            timeout_s + REPLY_GRACE_S,
            on_started,
        )
    except _InterpreterGone as exc:
        if exc.stalled and not cell_started:
            # No cell was accepted yet, so the session is stuck rather than lost.
            raise _stalled_error(socket_path, session_id) from exc
        elapsed = time.monotonic() - started
        _kill_interpreter(socket_path, pid)
        if elapsed >= timeout_s:
            reason = f"cell #{number} exceeded {timeout_s:g} s"
        elif exc.stalled:
            reason = f"interpreter stopped responding during cell #{number}"
        else:
            reason = f"worker exited during cell #{number}"
        _frame(f"--- interpreter replaced ({reason}); bindings lost; side effects unknown ---")
        return CellResult("replaced", session_id, result_pid, number, create, reason, b"", b"", elapsed)

    elapsed = time.monotonic() - started
    status = reply.get("status")
    if status == "busy":
        busy_pid = int(reply.get("pid", pid))
        _frame(f"--- interpreter {busy_pid} busy; no cell started ---")
        return CellResult("busy", session_id, busy_pid, None, create, None, b"", b"", elapsed)
    if status not in {"ok", "error"}:
        raise SessionError(f"interpreter sent an unexpected reply: {reply!r}")
    result_pid = int(reply.get("pid", result_pid))
    stdout, stderr = _decode(reply.get("stdout")), _decode(reply.get("stderr"))
    _write_bytes(sys.stdout, stdout)
    _write_bytes(sys.stderr, stderr)
    detail = reply.get("exception")
    suffix = f": {detail}" if detail else ""
    _frame(f"--- cell #{number} {status}{suffix} ({elapsed * 1000:.0f} ms) ---")
    if create:
        # Last on stdout, after the cell's own output: the caller reads the session
        # id from the end of its own stream. A replaced interpreter reports no id,
        # because the session it names no longer exists.
        _write_bytes(sys.stdout, _metadata_block(session_id, idle_timeout_s))
    return CellResult(status, session_id, result_pid, number, create, detail, stdout, stderr, elapsed)


def reset_bindings(session_id: str) -> ResetResult:
    """Discard Python bindings in the session interpreter; never replace it."""
    _reap_children()
    _validate_session_id(session_id)
    socket_path = session_socket_path(session_id)
    _prepare_directory(socket_path.parent)
    reply = _hello(socket_path, HELLO_TIMEOUT_S)
    if reply is None:
        _discard_stale_socket(socket_path)
        _frame(f"--- no live session {session_id}; bindings are already gone ---")
        return ResetResult("absent", None, None)
    _compatible_reply(reply, session_id)
    pid, cells = int(reply["pid"]), int(reply["cells"])
    try:
        answer = _exchange(socket_path, {"op": "reset"}, HELLO_TIMEOUT_S)
    except _InterpreterGone as exc:
        if exc.stalled:
            raise _stalled_error(socket_path, session_id) from exc
        _frame(f"--- interpreter {pid} vanished before reset; bindings are gone ---")
        return ResetResult("absent", None, None)
    status = answer.get("status")
    answered_pid = int(answer.get("pid", pid))
    if status == "busy":
        _frame(f"--- interpreter {answered_pid} busy; bindings not cleared ---")
        return ResetResult("busy", answered_pid, cells)
    if status != "ok":
        raise SessionError(f"interpreter sent an unexpected reply: {answer!r}")
    _frame(f"--- interpreter {answered_pid} bindings cleared (cell #{cells}) ---")
    return ResetResult("ok", answered_pid, cells)


class _WorkerState:
    def __init__(self, socket_path: Path, idle_timeout_s: float) -> None:
        self.socket_path = socket_path
        self.socket_inode: int | None = None
        self.namespace: dict[str, Any] = {"__name__": "__main__"}
        self.cells = 0
        self.running = False
        self.stopping = False
        self.idle_timeout_s = idle_timeout_s
        self.started = time.monotonic()
        self.last_activity = self.started
        self.lock = threading.Lock()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_activity)


def _remove_socket(state: _WorkerState) -> None:
    """Remove our socket name only while it still belongs to this worker.

    A replacement interpreter may already have bound a fresh socket at the same
    path; removing that one would detach it from future clients. Our own
    listening socket keeps the inode alive, so it cannot be reused by then.
    """
    try:
        if state.socket_inode is None:
            return
        if os.stat(state.socket_path).st_ino == state.socket_inode:
            state.socket_path.unlink()
    except OSError:
        pass


def _shutdown(state: _WorkerState, status: int) -> None:
    # Do not leave a socket name behind for a process that no longer accepts cells.
    _remove_socket(state)
    os._exit(status)


class _NullBackedBuffer(io.BytesIO):
    """Captured cell bytes that still report this worker's own descriptor.

    A cell that hands sys.stdout to a subprocess, or asks for its fileno, writes
    to the descriptor the worker holds - which is /dev/null once it is listening.
    """

    def __init__(self, file_descriptor: int) -> None:
        super().__init__()
        self._file_descriptor = file_descriptor

    def fileno(self) -> int:
        return self._file_descriptor


def _capture_stream(errors: str, file_descriptor: int) -> io.TextIOWrapper:
    # write_through keeps text and buffer writes ordered in one buffer. The error
    # handler mirrors CPython's own stdout (strict) and stderr (backslashreplace),
    # so cells encode text exactly as a script would.
    return io.TextIOWrapper(
        _NullBackedBuffer(file_descriptor), encoding="utf-8", errors=errors, newline="",
        write_through=True,
    )


def _expire(state: _WorkerState) -> None:
    # The cell's own deadline, enforced from a timer so a caller that dies cannot
    # leave a busy interpreter behind. The next call reports the replacement.
    _shutdown(state, 1)


def _write_reply(stream: Any, reply: dict[str, Any]) -> None:
    stream.write(json.dumps(reply).encode() + b"\n")
    stream.flush()


def _run_cell_request(
    state: _WorkerState, request: dict[str, Any], stream: Any
) -> dict[str, Any]:
    with state.lock:
        if state.running:
            return {"status": "busy", "pid": os.getpid(), "cells": state.cells}
        state.running = True
        state.cells += 1
        number = state.cells
        namespace = state.namespace
    # Every exit path below must clear the flag: a worker left claiming a cell
    # would reject every later cell and reset until it exits.
    expiration: threading.Timer | None = None
    try:
        stdout = _capture_stream("strict", 1)
        stderr = _capture_stream("backslashreplace", 2)
        status, detail = "ok", None
        # Report the assigned number before executing: the caller frames a cell
        # that then dies, and concurrent callers must not claim the same number.
        _write_reply(stream, {"status": "started", "pid": os.getpid(), "cells": number})
        timeout_s = float(request.get("timeout_s", DEFAULT_CELL_TIMEOUT_S))
        argv = [str(part) for part in request.get("argv") or ["-"]]
        expiration = threading.Timer(timeout_s, _expire, args=(state,))
        expiration.daemon = True
        expiration.start()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            sys.argv = argv
            try:
                exec(compile(request["code"], "<cell>", "exec"), namespace)
            except BaseException:
                status = "error"
                traceback.print_exc()
                detail = traceback.format_exc().strip().splitlines()[-1]
    finally:
        if expiration is not None:
            expiration.cancel()
        with state.lock:
            state.running = False
        # Idle time starts when the cell finishes, so a long cell is never cut
        # short by the session timeout.
        state.touch()
    return {
        "status": status,
        "pid": os.getpid(),
        "cells": number,
        "exception": detail,
        "stdout": _encode(stdout),
        "stderr": _encode(stderr),
    }


def _reset_request(state: _WorkerState) -> dict[str, Any]:
    with state.lock:
        if state.running:
            return {"status": "busy", "pid": os.getpid(), "cells": state.cells}
        # Old bindings are dropped, not mutated: their objects survive only if the
        # cell itself kept a reference elsewhere. Browser threads are untouched.
        state.namespace = {"__name__": "__main__"}
    state.touch()
    return {"status": "ok", "pid": os.getpid(), "cells": state.cells}


def _shutdown_request(state: _WorkerState) -> dict[str, Any]:
    # The reply must reach the caller before this process exits, so the request
    # only marks the worker; the accept loop stops it.
    state.stopping = True
    return {"status": "ok", "pid": os.getpid(), "cells": state.cells}


def _worker_cwd() -> str:
    with contextlib.suppress(OSError):
        return os.getcwd()
    return ""


def _hello_reply(state: _WorkerState) -> dict[str, Any]:
    return {
        "status": "hello",
        "pid": os.getpid(),
        "cells": state.cells,
        "idle_s": state.idle_seconds(),
        "uptime_s": max(0.0, time.monotonic() - state.started),
        "ttl_s": state.idle_timeout_s,
        "cwd": _worker_cwd(),
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
            elif request.get("op") == "shutdown":
                reply = _shutdown_request(state)
            else:
                reply = {
                    "status": "error",
                    "pid": os.getpid(),
                    "error": f"unknown operation {request.get('op')!r}",
                }
        except Exception as exc:  # keep the interpreter alive for the next cell
            reply = {
                "status": "error",
                "pid": os.getpid(),
                "exception": f"{type(exc).__name__}: {exc}",
                "cells": state.cells,
            }
        try:
            _write_reply(stream, reply)
        except OSError:
            # The caller went away; the interpreter remains available.
            return


def _detach_output() -> None:
    """Release the launcher's startup pipe.

    The pipe exists only so the launcher can explain a worker that dies before it
    listens. Keeping it after that would leave later descriptor writes aimed at a
    reader that has gone.
    """
    try:
        descriptor = os.open(os.devnull, os.O_RDWR)
    except OSError:
        return
    try:
        os.dup2(descriptor, 1)
        os.dup2(descriptor, 2)
    finally:
        os.close(descriptor)


def _idle_expired(state: _WorkerState) -> bool:
    if state.stopping or state.running:
        return False
    return state.idle_seconds() >= state.idle_timeout_s


def serve(socket_path: Path, idle_timeout_s: float) -> None:
    """Run the worker: accept connections, run one cell at a time, expire when idle."""
    socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(socket_path.parent, 0o700)
    state = _WorkerState(socket_path, idle_timeout_s)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        state.socket_inode = os.stat(socket_path).st_ino
        os.chmod(socket_path, 0o600)
        server.listen(SOCKET_BACKLOG)
        server.settimeout(IDLE_POLL_S)
        _detach_output()
        while True:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                # A shutdown request is handled on its own thread, so it is seen
                # here; a stopped worker must not linger until its idle timeout.
                if state.stopping or _idle_expired(state):
                    _shutdown(state, 0)
                continue
            threading.Thread(
                target=_handle_connection, args=(connection, state), daemon=True
            ).start()
            if state.stopping:
                _shutdown(state, 0)
    finally:
        server.close()
        _remove_socket(state)


_USAGE = """usage:
  python -m surf_agent.session worker SOCKET IDLE_TIMEOUT_SECONDS
  python -m surf_agent.session run JSON_REQUEST
"""


def _usage_error(message: str) -> int:
    print(f"surf: {message}", file=sys.stderr)
    print(_USAGE, file=sys.stderr, end="")
    return 2


def _requested_session(request: dict[str, Any]) -> str:
    session_id = request.get("session")
    if not isinstance(session_id, str) or not session_id:
        raise SessionError("the request names no session")
    return session_id


def _requested_seconds(request: dict[str, Any], key: str, default: float) -> float:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionError(f"request field {key!r} must be a number of seconds")
    return float(value)


def _requested_argv(request: dict[str, Any]) -> tuple[str, ...]:
    argv = request.get("argv") or ["-"]
    if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
        raise SessionError("request field 'argv' must be a list of strings")
    return tuple(argv)


def _print_sessions() -> int:
    entries = list_sessions()
    if not entries:
        print("no live sessions")
        return 0
    for entry in entries:
        print(
            f"{entry.session_id}  pid {entry.interpreter_pid}  cell #{entry.cells}  "
            f"idle {_duration(entry.idle_s)}  up {_duration(entry.uptime_s)}  "
            f"ttl {entry.idle_timeout_s:g}s  cwd {entry.cwd}"
        )
    return 0


def _requested_cell(request: dict[str, Any]) -> int:
    mode = request.get("mode")
    if mode == "new":
        name = request.get("name")
        if not isinstance(name, str) and name is not None:
            raise SessionError("request field 'name' must be a string")
        session_id = new_session_id(name)
    elif mode == "reuse":
        session_id = _requested_session(request)
    else:
        raise SessionError(f"unknown cell mode {mode!r}")
    result = run_cell(
        session_id,
        sys.stdin.read(),
        create=mode == "new",
        idle_timeout_s=_requested_seconds(request, "ttl", DEFAULT_SESSION_IDLE_TIMEOUT_S),
        argv=_requested_argv(request),
        timeout_s=_requested_seconds(request, "timeout", DEFAULT_CELL_TIMEOUT_S),
    )
    return 0 if result.status == "ok" else 1


def _main_run(arguments: list[str]) -> int:
    """Execute one launcher request.

    The launcher owns the command-line grammar, so this seam takes a validated
    request instead of repeating it. Keeping the grammar in one place is why the
    runtime has no `--new-session`/`--session` parsing of its own.
    """
    if len(arguments) != 1:
        return _usage_error("run takes one JSON request")
    try:
        request = json.loads(arguments[0])
    except ValueError as exc:
        return _usage_error(f"request is not valid JSON: {exc}")
    if not isinstance(request, dict):
        return _usage_error("request must be a JSON object")
    try:
        operation = request.get("op")
        if operation == "cell":
            return _requested_cell(request)
        if operation == "reset":
            result = reset_bindings(_requested_session(request))
            return 0 if result.status in {"ok", "absent"} else 1
        if operation == "kill":
            session_id = _requested_session(request)
            if kill_session(session_id):
                print(f"session {session_id} stopped")
                return 0
            print(f"no live session {session_id}")
            return 1
        if operation == "list":
            return _print_sessions()
    except SessionError as exc:
        print(f"surf: {exc}", file=sys.stderr)
        return 2
    return _usage_error(f"unknown request {operation!r}")


def _main_worker(arguments: list[str]) -> None:
    if len(arguments) != 2:
        print("usage: python -m surf_agent.session worker SOCKET IDLE_TIMEOUT_SECONDS",
              file=sys.stderr)
        raise SystemExit(2)
    socket_path, idle_timeout = arguments
    serve(Path(socket_path), float(idle_timeout))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print(_USAGE, file=sys.stderr, end="")
        return 2
    command, rest = arguments[0], arguments[1:]
    if command == "worker":
        _main_worker(rest)
        return 0
    if command == "run":
        return _main_run(rest)
    print(_USAGE, file=sys.stderr, end="")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

