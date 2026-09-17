"""Contract tests for the persistent per-session interpreter."""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from surf_agent import session


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test's sockets, logs and worker processes out of the real runtime dir."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("PI_SESSION_ID", raising=False)
    monkeypatch.delenv("PI_SESSION_FILE", raising=False)


@pytest.fixture
def owner():
    """A stand-in for the harness process: long-lived, killable, and not our parent."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    info = session.read_process(process.pid)
    assert info is not None
    try:
        yield session.OwnerRef(pid=process.pid, start_time=info.start_time)
    finally:
        process.kill()
        process.wait()


def wait_for_exit(pid: int, timeout_s: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = session.read_process(pid)
        if info is None or info.state == "Z":
            return "gone" if info is None else "Z"
        time.sleep(0.05)
    return "alive"


def test_cells_run_in_order_and_retain_bindings(owner):
    first = session.run_cell("work", "answer = 41\nprint('first')", owner=owner)
    assert first.status == "ok"
    assert first.cell_number == 1
    assert first.created is True
    assert first.stdout == b"first\n"

    second = session.run_cell("work", "print('answer', answer + 1)", owner=owner)
    assert second.status == "ok"
    assert second.cell_number == 2
    assert second.created is False
    assert second.interpreter_pid == first.interpreter_pid
    assert second.stdout == b"answer 42\n"


def test_cell_globals_are_script_globals(owner):
    result = session.run_cell(
        "work",
        "import sys\nprint(__name__, sys.argv, 'Thread' in dir())",
        argv=("-", "two words"),
        owner=owner,
    )
    assert result.status == "ok"
    assert result.stdout == b"__main__ ['-', 'two words'] False\n"


def test_cell_output_and_errors_are_captured_per_cell(owner):
    result = session.run_cell(
        "work",
        "import sys\nprint('out')\nprint('problem', file=sys.stderr)\nraise ValueError('boom')",
        owner=owner,
    )
    assert result.status == "error"
    assert result.detail == "ValueError: boom"
    assert result.stdout == b"out\n"
    assert b"Traceback" in result.stderr
    assert b"ValueError: boom" in result.stderr

    # The namespace survives a cell error.
    after = session.run_cell("work", "print('still here')", owner=owner)
    assert after.status == "ok"
    assert after.cell_number == 2


def test_system_exit_ends_only_the_cell(owner):
    result = session.run_cell("work", "import sys\nsys.exit(3)", owner=owner)
    assert result.status == "error"
    assert result.detail == "SystemExit: 3"
    assert session.run_cell("work", "print('alive')", owner=owner).status == "ok"


def test_reset_discards_bindings_without_replacing_interpreter(owner):
    first = session.run_cell("work", "value = 7", owner=owner)
    result = session.reset_bindings("work", owner=owner)
    assert result.status == "ok"
    assert result.interpreter_pid == first.interpreter_pid

    after = session.run_cell("work", "print(value)", owner=owner)
    assert after.status == "error"
    assert "NameError" in (after.detail or "")
    # Reset does not restart the cell counter: the interpreter identity is unchanged.
    assert after.cell_number == 2


def test_reset_without_an_interpreter_is_harmless(owner):
    assert session.reset_bindings("nothing-here", owner=owner).status == "absent"


def test_concurrent_first_cells_converge_on_one_interpreter(capfd, owner):
    results: list[session.CellResult] = []

    def run(index: int) -> None:
        results.append(
            session.run_cell("work", f"print('cell {index}')", owner=owner, timeout_s=30.0)
        )

    threads = [threading.Thread(target=run, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60.0)

    assert len(results) == 3
    assert len({result.interpreter_pid for result in results}) == 1
    assert {result.status for result in results} <= {"ok", "busy"}
    finished = sorted(result.cell_number for result in results if result.status == "ok")
    assert finished == list(range(1, len(finished) + 1))
    # Every cell number is assigned and framed exactly once, headers and trailers alike.
    frames = capfd.readouterr().err
    assert sorted(re.findall(r"--- interpreter \d+ \(cell #(\d+), ", frames)) == [
        str(number) for number in finished
    ]
    assert sorted(re.findall(r"--- cell #(\d+) ok", frames)) == [str(number) for number in finished]


def test_busy_cell_fails_fast_instead_of_queueing(owner, tmp_path):
    marker = tmp_path / "started"
    code = f"import time\nopen({str(marker)!r}, 'w').close()\ntime.sleep(1.5)"
    slow = threading.Thread(
        target=session.run_cell, args=("work", code), kwargs={"owner": owner, "timeout_s": 30.0}
    )
    slow.start()
    try:
        deadline = time.monotonic() + 5.0
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "slow cell never started"

        started = time.monotonic()
        result = session.run_cell("work", "print('never runs')", owner=owner)
        assert result.status == "busy"
        assert time.monotonic() - started < 1.0
        assert result.stdout == b""
    finally:
        slow.join(timeout=30.0)


def test_timeout_replaces_interpreter_and_reports_new_identity(owner):
    first = session.run_cell("work", "kept = 1", owner=owner)
    slow = session.run_cell("work", "import time\ntime.sleep(30)", timeout_s=0.5, owner=owner)
    assert slow.status == "replaced"
    assert "exceeded 0.5" in (slow.detail or "")
    assert slow.duration_s < 5.0
    assert wait_for_exit(first.interpreter_pid) in {"gone", "Z"}

    after = session.run_cell("work", "print(kept)", owner=owner)
    assert after.created is True
    assert after.cell_number == 1
    assert after.interpreter_pid != first.interpreter_pid
    assert after.status == "error"
    assert "NameError" in (after.detail or "")


def test_cell_that_kills_its_worker_prints_nothing(capfd, owner):
    first = session.run_cell("work", "kept = 1", owner=owner)
    result = session.run_cell(
        "work",
        "import os, signal\nprint('before death')\nos.kill(os.getpid(), signal.SIGKILL)",
        owner=owner,
    )
    assert result.status == "replaced"
    assert "exited during" in (result.detail or "")
    assert result.stdout == b""
    # The header is printed outside the cell, so the caller can still see where it died.
    frames = capfd.readouterr().err
    assert f"--- interpreter {first.interpreter_pid} (cell #2, attached) ---" in frames


def test_dead_worker_is_replaced_on_the_next_cell(owner):
    first = session.run_cell("work", "kept = 2", owner=owner)
    os.kill(first.interpreter_pid, signal.SIGKILL)
    assert wait_for_exit(first.interpreter_pid) in {"gone", "Z"}

    second = session.run_cell("work", "print(kept)", owner=owner)
    assert second.created is True
    assert second.cell_number == 1
    assert second.interpreter_pid != first.interpreter_pid
    assert second.status == "error"


def test_sessions_do_not_share_globals(owner):
    first = session.run_cell("alpha", "marker = 'alpha'", owner=owner)
    second = session.run_cell("beta", "print(marker)", owner=owner)
    assert second.status == "error"
    assert second.interpreter_pid != first.interpreter_pid
    assert session.read_process(first.interpreter_pid) is not None


def test_session_key_is_scoped_to_the_harness_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("PI_SESSION_ID", "one")
    first = session.session_socket_path("work")
    monkeypatch.setenv("PI_SESSION_ID", "two")
    second = session.session_socket_path("work")
    assert first != second
    assert str(first.parent) == str(tmp_path / "runtime" / "surf-agent")
    assert len(os.fsencode(str(first))) <= session.SOCKET_PATH_LIMIT


def test_session_files_are_private(owner):
    session.run_cell("work", "pass", owner=owner)
    socket_path = session.session_socket_path("work")
    log_path = session.session_log_path("work")
    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_environment_is_pinned_when_the_interpreter_is_created(monkeypatch, owner):
    monkeypatch.setenv("SURF_AGENT_HOME", "/first")
    session.run_cell("work", "import os\npinned = os.environ['SURF_AGENT_HOME']", owner=owner)
    monkeypatch.setenv("SURF_AGENT_HOME", "/second")
    result = session.run_cell(
        "work", "import os\nprint(pinned, os.environ['SURF_AGENT_HOME'])", owner=owner
    )
    assert result.stdout == b"/first /first\n"


def test_owner_liveness_distinguishes_reuse_and_zombies():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    info = session.read_process(process.pid)
    assert info is not None
    assert session.process_alive(process.pid, info.start_time) is True
    assert session.process_alive(process.pid, info.start_time + 1) is False
    process.kill()
    process.wait()
    assert session.process_alive(process.pid, info.start_time) is False

    # A reaped child lingers as a zombie while its parent lives; it is not an owner.
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    try:
        deadline = time.monotonic() + 5.0
        info = session.read_process(pid)
        while info is not None and info.state != "Z" and time.monotonic() < deadline:
            time.sleep(0.05)
            info = session.read_process(pid)
        assert info is not None and info.state == "Z"
        start_time = info.start_time
        assert session.process_alive(pid, start_time) is False
    finally:
        os.waitpid(pid, 0)


def test_owner_death_reaps_the_worker():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    info = session.read_process(process.pid)
    assert info is not None
    owner = session.OwnerRef(pid=process.pid, start_time=info.start_time)
    result = session.run_cell("work", "kept = 1", owner=owner)
    assert result.status == "ok"

    process.kill()
    process.wait()
    assert wait_for_exit(result.interpreter_pid) in {"gone", "Z"}
    # The reaped worker cleans up its socket name on the way out.
    assert not session.session_socket_path("work").exists()
    # The worker is our child in-process; reap it so it does not linger as a zombie.
    try:
        os.waitpid(result.interpreter_pid, os.WNOHANG)
    except ChildProcessError:
        pass


def stop_worker(pid: int, socket_path: Path) -> None:
    """Stop only a process that is still this test's worker for that socket."""
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return
    if b"surf_agent.session" in command and str(socket_path).encode() in command:
        os.kill(pid, signal.SIGKILL)


def test_worker_never_inherits_the_callers_stdout(monkeypatch, tmp_path):
    monkeypatch.setenv("PI_SESSION_ID", "fd-test")
    code = "import os\nos.write(1, b'raw bytes\\n')\nprint('after raw')"
    result = subprocess.run(
        [sys.executable, "-m", "surf_agent.session", "cell", "--session", "fd", "-"],
        input=code, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    # If the detached worker inherited this pipe, the call would never see EOF.
    assert result.stdout == "after raw\n"
    assert "raw bytes" not in result.stdout

    log_path = session.session_log_path("fd")
    assert "raw bytes" in log_path.read_text()

    worker_pid = int(result.stderr.split("--- interpreter ")[1].split(" ")[0])
    assert os.readlink(f"/proc/{worker_pid}/fd/1") == str(log_path)
    assert os.readlink(f"/proc/{worker_pid}/fd/2") == str(log_path)
    # The launcher resolved the harness ancestor as owner; this test stops it directly.
    stop_worker(worker_pid, session.session_socket_path("fd"))
