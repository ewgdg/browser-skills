"""Contract tests for explicitly created session interpreters."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from surf_agent import session


def process_state(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    try:
        return raw.rsplit(b") ", 1)[1].split()[0].decode()
    except (IndexError, ValueError):
        return None


def wait_for(predicate, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def wait_for_exit(pid: int, timeout_s: float = 10.0) -> bool:
    return wait_for(lambda: process_state(pid) in (None, "Z"), timeout_s)


def stop_worker(socket_path: Path) -> None:
    """Stop whatever still listens on this socket, and drop the name."""
    pid = session._listener_pid(socket_path)
    if pid is not None:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
    with contextlib.suppress(OSError):
        socket_path.unlink()


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep every test's sockets and worker processes out of the real runtime dir."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv(session.WORKER_COMMAND_ENV, raising=False)
    yield
    directory = session.session_socket_dir()
    if directory.exists():
        for socket_path in directory.glob(f"*{session._SOCKET_SUFFIX}"):
            stop_worker(socket_path)


def create(code: str, *, name: str | None = None, idle_timeout_s: float | None = None, **kwargs) -> session.CellResult:
    """Create a session and run its first cell."""
    options = {"create": True}
    if idle_timeout_s is not None:
        options["idle_timeout_s"] = idle_timeout_s
    options.update(kwargs)
    return session.run_cell(session.new_session_id(name), code, **options)


def run_in(session_id: str, code: str, **kwargs) -> session.CellResult:
    return session.run_cell(session_id, code, **kwargs)


def metadata_block(session_id: str, idle_timeout_s: float = session.DEFAULT_SESSION_IDLE_TIMEOUT_S) -> str:
    return (
        "--- BEGIN session metadata ---\n"
        f"session_id: {session_id}\n"
        f"idle_timeout_s: {idle_timeout_s:g}\n"
        "--- END session metadata ---\n"
    )


def start_blocking_cell(session_id: str, tmp_path: Path, *, timeout_s: float = 60.0):
    """Start a cell that blocks until the returned release file appears."""
    marker, release = tmp_path / "marker", tmp_path / "release"
    code = (
        "import pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).touch()\n"
        "deadline = time.monotonic() + 30\n"
        f"while not pathlib.Path({str(release)!r}).exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )
    thread = threading.Thread(
        target=session.run_cell, args=(session_id, code), kwargs={"timeout_s": timeout_s}
    )
    thread.start()
    assert wait_for(marker.exists), "blocking cell never started"
    return thread, release


def send_raw(socket_path: Path, payload: bytes) -> socket.socket:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(socket_path))
    connection.sendall(payload)
    return connection


@contextlib.contextmanager
def fake_interpreter(socket_path: Path, reply: bytes | None):
    """A listener that answers each connection with *reply*, or closes it."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.settimeout(0.2)
    server.listen(8)
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            with connection:
                if reply is None:
                    continue
                stream = connection.makefile("rwb")
                stream.readline()
                stream.write(reply)
                stream.flush()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)
        server.close()
        with contextlib.suppress(OSError):
            socket_path.unlink()


# Cells and bindings


def test_cells_run_in_order_and_retain_bindings():
    first = create("answer = 41\nprint('first')")
    assert first.status == "ok"
    assert first.cell_number == 1
    assert first.created is True
    assert first.stdout == b"first\n"

    second = run_in(first.session_id, "print('answer', answer + 1)")
    assert second.status == "ok"
    assert second.cell_number == 2
    assert second.created is False
    assert second.interpreter_pid == first.interpreter_pid
    assert second.stdout == b"answer 42\n"


def test_cell_globals_are_script_globals():
    result = create("import sys\nprint(__name__, sys.argv, 'Thread' in dir())")
    assert result.stdout == b"__main__ ['-'] False\n"


def test_cell_output_and_errors_are_captured_per_cell():
    created = create("print('out')\nraise ValueError('boom')")
    assert created.status == "error"
    assert created.stdout == b"out\n"
    assert b"ValueError: boom" in created.stderr
    assert "ValueError: boom" in (created.detail or "")

    after = run_in(created.session_id, "print('recovered')")
    assert after.status == "ok"
    assert after.stderr == b""


def test_system_exit_ends_only_the_cell():
    created = create("raise SystemExit(3)")
    assert created.status == "error"
    assert "SystemExit" in (created.detail or "")
    after = run_in(created.session_id, "print('alive')")
    assert after.status == "ok"


# Ids and addressing


def test_new_session_ids_are_readable_and_unique():
    assert re.fullmatch(r"[0-9a-f]{8}", session.new_session_id())
    assert re.fullmatch(r"my-task-[0-9a-f]{8}", session.new_session_id("my task!"))
    assert len({session.new_session_id("task") for _ in range(50)}) == 50


def test_invalid_session_ids_are_rejected():
    for bad in ("", "-leading", ".hidden", "with/slash", "a" * 70, "space here"):
        with pytest.raises(session.SessionError):
            run_in(bad, "pass")


def test_unknown_session_id_is_rejected_and_lists_live_sessions():
    created = create("pass")
    with pytest.raises(session.SessionError) as excinfo:
        run_in(session.new_session_id("other"), "pass")
    message = str(excinfo.value)
    assert "unknown session" in message
    assert created.session_id in message


def test_two_sessions_do_not_share_globals():
    first = create("kept = 'first'")
    second = create("kept = 'second'")
    assert first.interpreter_pid != second.interpreter_pid
    assert run_in(first.session_id, "print(kept)").stdout == b"first\n"
    assert run_in(second.session_id, "print(kept)").stdout == b"second\n"


# The metadata block


def test_create_prints_the_metadata_block_last_on_stdout(capfd):
    created = create("print('cell output')", name="demo", idle_timeout_s=90.0)
    captured = capfd.readouterr()
    assert captured.out == "cell output\n" + metadata_block(created.session_id, 90.0)
    assert created.session_id.startswith("demo-")
    assert "(cell #1, created; idle timeout 90 s)" in captured.err


def test_create_prints_the_block_when_the_first_cell_raises(capfd):
    created = create("raise ValueError('boom')")
    captured = capfd.readouterr()
    assert captured.out == metadata_block(created.session_id)


def test_attach_prints_no_metadata_block(capfd):
    created = create("pass")
    capfd.readouterr()
    run_in(created.session_id, "print('only output')")
    captured = capfd.readouterr()
    assert captured.out == "only output\n"
    assert "session metadata" not in captured.out


def test_cell_lookalike_block_does_not_change_the_rule(capfd):
    lookalike = "--- BEGIN session metadata ---\nsession_id: fake\n--- END session metadata ---"
    created = create(f"print({lookalike!r})")
    captured = capfd.readouterr()
    assert "session_id: fake" in captured.out
    assert captured.out.endswith(metadata_block(created.session_id))


# Lifetime


def test_idle_interpreter_exits_after_its_timeout():
    created = create("kept = 1", idle_timeout_s=1.0)
    assert wait_for_exit(created.interpreter_pid)
    assert wait_for(lambda: not session.session_socket_path(created.session_id).exists())
    with pytest.raises(session.SessionError, match="unknown session"):
        run_in(created.session_id, "pass")
    assert process_state(created.interpreter_pid) is None


def test_a_running_cell_is_not_cut_by_the_idle_timeout():
    created = create("pass", idle_timeout_s=1.0)
    slow = run_in(created.session_id, "import time\ntime.sleep(2)", timeout_s=30.0)
    assert slow.status == "ok"
    assert run_in(created.session_id, "print('still here')").stdout == b"still here\n"


def test_kill_session_stops_it_and_removes_its_files():
    created = create("kept = 1")
    assert session.kill_session(created.session_id) is True
    # The creator reaps its own worker: a finished worker must not linger as a zombie.
    assert process_state(created.interpreter_pid) is None
    assert not session.session_socket_path(created.session_id).exists()
    assert session.kill_session(created.session_id) is False
    with pytest.raises(session.SessionError, match="unknown session"):
        run_in(created.session_id, "pass")


def test_kill_session_stops_an_interpreter_that_cannot_answer(monkeypatch):
    """The advice for a wedged interpreter must not be the command that just failed."""
    monkeypatch.setattr(session, "HELLO_TIMEOUT_S", 0.5)
    created = create("pass")
    os.kill(created.interpreter_pid, signal.SIGSTOP)
    try:
        assert session.kill_session(created.session_id) is True
    finally:
        with contextlib.suppress(OSError):
            os.kill(created.interpreter_pid, signal.SIGCONT)
    assert wait_for_exit(created.interpreter_pid)
    assert not session.session_socket_path(created.session_id).exists()


def test_list_sessions_reports_an_interpreter_that_cannot_answer(monkeypatch):
    """A live interpreter that stops answering keeps its name and stays stoppable."""
    monkeypatch.setattr(session, "HELLO_TIMEOUT_S", 0.5)
    created = create("pass")
    socket_path = session.session_socket_path(created.session_id)
    # Older than the stale-socket grace, so only the new rule can protect it.
    old = time.time() - 600
    os.utime(socket_path, (old, old))
    os.kill(created.interpreter_pid, signal.SIGSTOP)
    try:
        entries = session.list_sessions()
        assert [entry.session_id for entry in entries] == [created.session_id]
        assert entries[0].state == "unresponsive"
        assert entries[0].interpreter_pid == created.interpreter_pid
        assert socket_path.exists()
    finally:
        with contextlib.suppress(OSError):
            os.kill(created.interpreter_pid, signal.SIGCONT)
    assert session.kill_session(created.session_id) is True
    assert wait_for_exit(created.interpreter_pid)


def test_listener_is_found_when_proc_is_unavailable(monkeypatch):
    """A host without /proc must still be able to identify, and therefore stop, a worker."""
    created = create("pass")
    monkeypatch.setattr(session, "_proc_available", lambda: False)
    socket_path = session.session_socket_path(created.session_id)
    assert session._listener_pid(socket_path) == created.interpreter_pid
    assert session.kill_session(created.session_id) is True


def test_list_sessions_reports_live_sessions():
    created = create("pass", name="listed", idle_timeout_s=42.0)
    entries = {entry.session_id: entry for entry in session.list_sessions()}
    assert created.session_id in entries
    entry = entries[created.session_id]
    assert entry.interpreter_pid == created.interpreter_pid
    assert entry.cells == 1
    assert entry.idle_timeout_s == 42.0
    assert entry.idle_s < 5.0
    assert entry.cwd == os.getcwd()


def test_list_sessions_sweeps_stale_sockets_and_ignores_other_protocols(capfd):
    stale = session.session_socket_path(session.new_session_id("stale"))
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.touch()
    old = time.time() - 60
    os.utime(stale, (old, old))
    other = session.session_socket_path(session.new_session_id("other"))
    with fake_interpreter(other, b'{"status": "hello", "pid": 1, "cells": 0}\n'):
        entries = session.list_sessions()
    assert entries == []
    assert not stale.exists()
    assert "different interpreter protocol" in capfd.readouterr().err


def test_kill_refuses_an_interpreter_this_runtime_cannot_stop():
    socket_path = session.session_socket_path(session.new_session_id("other"))
    with fake_interpreter(socket_path, b'{"status": "hello", "pid": 1, "cells": 0}\n'):
        with pytest.raises(session.SessionError, match="does not speak to"):
            session.kill_session(session_id_from(socket_path))


def session_id_from(socket_path: Path) -> str:
    return socket_path.name[: -len(session._SOCKET_SUFFIX)]


# Reset


def test_reset_discards_bindings_without_replacing_interpreter():
    created = create("kept = 1")
    result = session.reset_bindings(created.session_id)
    assert result.status == "ok"
    assert result.interpreter_pid == created.interpreter_pid
    after = run_in(created.session_id, "print('kept' in dir())")
    assert after.status == "ok"
    assert after.stdout == b"False\n"


def test_reset_without_an_interpreter_is_harmless():
    result = session.reset_bindings(session.new_session_id("gone"))
    assert result.status == "absent"
    assert result.interpreter_pid is None


def test_reset_while_a_cell_runs_is_rejected(tmp_path):
    created = create("pass")
    thread, release = start_blocking_cell(created.session_id, tmp_path)
    try:
        result = session.reset_bindings(created.session_id)
        assert result.status == "busy"
        assert result.interpreter_pid == created.interpreter_pid
    finally:
        release.touch()
        thread.join(timeout=30)


# Concurrency and robustness


def test_busy_cell_fails_fast_instead_of_queueing(tmp_path):
    created = create("pass")
    thread, release = start_blocking_cell(created.session_id, tmp_path)
    try:
        started = time.monotonic()
        busy = run_in(created.session_id, "pass")
        assert busy.status == "busy"
        assert busy.duration_s < 2.0
        assert time.monotonic() - started < 2.0
    finally:
        release.touch()
        thread.join(timeout=30)


def test_client_that_disconnects_early_does_not_wedge_the_session():
    created = create("pass")
    socket_path = session.session_socket_path(created.session_id)
    connection = send_raw(socket_path, b'{"op": "cell", "code": "print(1)"}\n')
    connection.close()
    assert run_in(created.session_id, "print('after')").stdout == b"after\n"


def test_malformed_request_does_not_wedge_the_session():
    created = create("pass")
    socket_path = session.session_socket_path(created.session_id)
    with send_raw(socket_path, b"not json\n") as connection:
        reply = json.loads(connection.makefile("rb").readline())
    assert reply["status"] == "error"
    assert run_in(created.session_id, "print('after')").stdout == b"after\n"


# Faults and replacement


def test_timeout_replaces_interpreter_and_reports_the_loss():
    created = create("kept = 1")
    slow = run_in(created.session_id, "import time\ntime.sleep(30)", timeout_s=0.5)
    assert slow.status == "replaced"
    assert "exceeded 0.5" in (slow.detail or "")
    assert slow.duration_s < 5.0
    assert wait_for_exit(created.interpreter_pid)
    # A replaced interpreter is gone: its id is unknown rather than silently remade.
    with pytest.raises(session.SessionError, match="unknown session"):
        run_in(created.session_id, "print(kept)")


def test_cell_that_kills_its_worker_prints_nothing(capfd):
    created = create("kept = 1")
    capfd.readouterr()
    result = run_in(
        created.session_id,
        "import os, signal\nprint('before death')\nos.kill(os.getpid(), signal.SIGKILL)",
    )
    assert result.status == "replaced"
    assert "exited during" in (result.detail or "")
    assert result.stdout == b""
    frames = capfd.readouterr().err
    assert f"--- interpreter {created.interpreter_pid} (cell #2, attached) ---" in frames


def test_caller_replaces_a_worker_that_cannot_answer(monkeypatch):
    monkeypatch.setattr(session, "REPLY_GRACE_S", 0.5)
    created = create("pass")
    outcome: list[session.CellResult] = []
    thread = threading.Thread(
        target=lambda: outcome.append(
            run_in(created.session_id, "import time\ntime.sleep(30)", timeout_s=1.0)
        )
    )
    thread.start()
    assert wait_for(lambda: process_state(created.interpreter_pid) is not None)
    time.sleep(0.3)
    os.kill(created.interpreter_pid, signal.SIGSTOP)
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert outcome and outcome[0].status == "replaced"
    assert "exceeded 1" in (outcome[0].detail or "")
    assert wait_for_exit(created.interpreter_pid)


def test_timeout_that_cannot_confirm_the_pid_is_reported_not_claimed(monkeypatch, capfd):
    """A pid this host cannot tie to the socket is never signalled, and the call says so."""
    monkeypatch.setattr(session, "REPLY_GRACE_S", 0.5)
    created = create("pass")
    monkeypatch.setattr(session, "_listener_pid", lambda socket_path: None)
    outcome: list[session.CellResult] = []
    thread = threading.Thread(
        target=lambda: outcome.append(
            run_in(created.session_id, "import time\ntime.sleep(30)", timeout_s=1.0)
        )
    )
    thread.start()
    assert wait_for(lambda: process_state(created.interpreter_pid) is not None)
    time.sleep(0.3)
    os.kill(created.interpreter_pid, signal.SIGSTOP)
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert outcome and outcome[0].status == "replaced"
    frames = capfd.readouterr().err
    assert "not stopped" in frames
    assert f"kill -9 {created.interpreter_pid}" in frames
    # It is still there: the call reported that instead of claiming it was destroyed.
    assert process_state(created.interpreter_pid) == "T"
    os.kill(created.interpreter_pid, signal.SIGCONT)
    os.kill(created.interpreter_pid, signal.SIGKILL)
    assert wait_for_exit(created.interpreter_pid)


def test_stopped_interpreter_is_reported_not_replaced(monkeypatch):
    monkeypatch.setattr(session, "HELLO_TIMEOUT_S", 0.5)
    created = create("pass")
    os.kill(created.interpreter_pid, signal.SIGSTOP)
    try:
        with pytest.raises(session.SessionError, match="did not answer"):
            run_in(created.session_id, "pass")
    finally:
        os.kill(created.interpreter_pid, signal.SIGKILL)
    assert wait_for_exit(created.interpreter_pid)


def test_interpreter_that_closes_the_channel_is_reported_as_going_away():
    socket_path = session.session_socket_path(session.new_session_id("closer"))
    with fake_interpreter(socket_path, None):
        # An interpreter that accepted the connection is not an absent one; the
        # difference decides whether the agent should retry or start over.
        with pytest.raises(session.SessionError, match="going away"):
            run_in(session_id_from(socket_path), "pass")


def test_malformed_hello_reply_is_reported():
    socket_path = session.session_socket_path(session.new_session_id("garbage"))
    with fake_interpreter(socket_path, b"not a reply\n"):
        with pytest.raises(session.SessionError, match="unreadable reply"):
            run_in(session_id_from(socket_path), "pass")


# Worker configuration


def test_configured_worker_command_starts_the_interpreter(monkeypatch):
    bootstrap = (
        "import sys; from surf_agent.session import main; "
        "raise SystemExit(main(['worker', *sys.argv[1:]]))"
    )
    monkeypatch.setenv(
        session.WORKER_COMMAND_ENV, json.dumps([sys.executable, "-c", bootstrap])
    )
    result = create("print('configured command')")
    assert result.status == "ok"
    assert result.stdout == b"configured command\n"


def test_invalid_worker_command_is_rejected(monkeypatch):
    for value in ("not json", json.dumps([]), json.dumps(["python", 3])):
        monkeypatch.setenv(session.WORKER_COMMAND_ENV, value)
        with pytest.raises(session.SessionError):
            create("pass")


def test_failing_worker_reports_its_own_output(monkeypatch):
    monkeypatch.setenv(
        session.WORKER_COMMAND_ENV,
        json.dumps([sys.executable, "-c", "import sys; print('boom: cannot start'); sys.exit(3)"]),
    )
    with pytest.raises(session.SessionError, match="boom: cannot start"):
        create("pass")


def test_runtime_rejects_a_malformed_request():
    for request in (
        "not json",
        json.dumps(["list"]),
        json.dumps({"op": "nonsense"}),
        json.dumps({"op": "cell", "mode": "reuse"}),
        json.dumps({"op": "cell", "mode": "new", "ttl": "soon"}),
        json.dumps({"op": "kill"}),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "surf_agent.session", "run", request],
            input="pass", capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 2, (request, result.stdout, result.stderr)
        assert result.stderr.startswith("surf: "), (request, result.stderr)


# Files and streams


def test_session_files_are_private_and_leave_no_artifacts():
    created = create("print('hi')")
    directory = session.session_socket_dir()
    socket_path = session.session_socket_path(created.session_id)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    assert [path.name for path in directory.iterdir()] == [socket_path.name]
    assert os.readlink(f"/proc/{created.interpreter_pid}/fd/1") == os.devnull
    assert os.readlink(f"/proc/{created.interpreter_pid}/fd/2") == os.devnull


def test_a_cell_that_closes_its_stream_keeps_what_it_printed():
    # Closing a stream is the cell's own business; the bytes it already wrote are
    # the cell's whole result and must not disappear with the stream.
    created = create("import sys\nprint('kept')\nsys.stdout.close()")
    assert created.status == "ok"
    assert created.stdout == b"kept\n"


def test_descriptor_writes_are_dropped_without_a_file():
    created = create(
        "import os, subprocess, sys\n"
        "os.write(1, b'raw stdout\\n')\n"
        "os.write(2, b'raw stderr\\n')\n"
        "subprocess.run(['/bin/echo', 'child output'])\n"
        "print('print is visible')\n"
    )
    assert created.status == "ok"
    assert created.stdout == b"print is visible\n"
    assert created.stderr == b""
    assert list(session.session_socket_dir().iterdir()) == [
        session.session_socket_path(created.session_id)
    ]


def test_worker_never_inherits_the_callers_stdout():
    code = (
        "import os, subprocess, sys\n"
        "assert sys.stdout.fileno() == 1 and sys.stderr.fileno() == 2\n"
        "subprocess.run(['/bin/echo', 'child output'], check=True, stdout=sys.stdout)\n"
        "os.write(1, b'raw bytes\\n')\n"
        "print('after raw')\n"
    )
    request = json.dumps({"op": "cell", "mode": "new", "name": "fd", "argv": ["-"]})
    result = subprocess.run(
        [sys.executable, "-m", "surf_agent.session", "run", request],
        input=code, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # If the detached worker inherited this pipe, the call would never see EOF.
    assert "after raw" in result.stdout
    assert "raw bytes" not in result.stdout
    assert "child output" not in result.stdout
    session_id = re.search(r"session_id: (\S+)", result.stdout).group(1)
    try:
        assert result.stdout.endswith(metadata_block(session_id))
    finally:
        assert session.kill_session(session_id) is True

