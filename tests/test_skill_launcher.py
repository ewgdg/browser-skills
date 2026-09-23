"""Process-level contracts for the independently installed Surf skill."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_missing_or_invalid_pin_fails_before_running_script(tmp_path):
    skill = tmp_path / "installed skill"
    shutil.copytree(ROOT / "skills/surf", skill, ignore=shutil.ignore_patterns(".*"))
    (skill / "runtime-revision").write_text("not-a-commit\n")
    env = os.environ.copy()
    env.pop("SURF_AGENT_DEPENDENCY", None)
    result = subprocess.run(
        [sys.executable, str(skill / "scripts/run.py"), "-"],
        input="print('must not run')", text=True, capture_output=True,
        cwd=tmp_path, env=env, timeout=10,
    )
    assert result.returncode == 2
    assert "invalid runtime pin" in result.stderr
    assert "SURF_AGENT_DEPENDENCY" in result.stderr
    assert result.stdout == ""


@pytest.fixture(scope="module")
def local_wheel(tmp_path_factory):
    output = tmp_path_factory.mktemp("built wheels")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output),
         str(ROOT / "packages/surf-agent")],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return next(output.glob("*.whl"))


def test_wheel_is_importable_without_skill_or_cli(tmp_path, local_wheel):
    script = tmp_path / "project.py"
    script.write_text(
        "import json\nfrom importlib.metadata import distribution\n"
        "import surf_agent\nfrom surf_agent import Thread\n"
        "print(json.dumps({\n"
        "    'module': surf_agent.__file__,\n"
        "    'thread': Thread.__name__,\n"
        "    'commands': [entry.name for entry in distribution('surf-agent').entry_points\n"
        "                 if entry.group == 'console_scripts'],\n"
        "}))\n"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("SURF_AGENT_DEPENDENCY", None)
    result = subprocess.run(
        ["uv", "run", "--no-project", "--no-config", "--isolated",
         "--with", f"surf-agent @ {local_wheel.as_uri()}",
         "--", "python", str(script)],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["thread"] == "Thread"
    assert not Path(observed["module"]).resolve().is_relative_to(ROOT)
    assert observed["commands"] == []


@pytest.fixture
def installed_skill(tmp_path, local_wheel):
    skill = tmp_path / "installed skill"
    shutil.copytree(ROOT / "skills/surf", skill, ignore=shutil.ignore_patterns(".*"))
    working = tmp_path / "unrelated project"
    working.mkdir()
    # An unrelated project's invalid config must not affect the skill runtime.
    (working / "pyproject.toml").write_text("invalid TOML [")
    env = os.environ.copy()
    env["SURF_AGENT_DEPENDENCY"] = str(local_wheel)
    return skill / "scripts/run.py", working, env


def test_installed_skill_executes_file_with_ordinary_python_semantics(installed_skill):
    launcher, working, env = installed_skill
    script_dir = working / "script files"
    script_dir.mkdir()
    (script_dir / "helper.py").write_text("VALUE = 'sibling import'\n")
    script = script_dir / "task.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\nfrom helper import VALUE\n"
        "from surf_agent import Thread\n"
        "print(Thread.__name__, VALUE, Path.cwd().name, sys.argv[1:])\n"
        "print(sys.stdin.read())\n"
    )
    result = subprocess.run(
        [sys.executable, str(launcher), str(script), "two words", "--flag"],
        input="input preserved", text=True, capture_output=True,
        cwd=working, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "Thread sibling import unrelated project ['two words', '--flag']\n"
        "input preserved\n"
    )


@pytest.mark.parametrize(
    ("source", "status", "stdout", "stderr"),
    [
        ("import sys; print(sys.argv); sys.exit(7)", 7, "['-', 'two words']\n", ""),
        ("raise RuntimeError('script failure')", 1, "", "RuntimeError: script failure"),
    ],
)
def test_stdin_preserves_arguments_exits_and_errors(
    installed_skill, source, status, stdout, stderr,
):
    launcher, working, env = installed_skill
    result = subprocess.run(
        [sys.executable, str(launcher), "-", "two words"],
        input=source, text=True, capture_output=True,
        cwd=working, env=env, timeout=120,
    )
    assert result.returncode == status, result.stderr
    assert result.stdout == stdout
    assert stderr in result.stderr


def test_uv_dependency_failure_does_not_execute_script(installed_skill):
    launcher, working, env = installed_skill
    broken = working / "surf_agent-0.1.0-py3-none-any.whl"
    broken.write_text("not a wheel")
    env["SURF_AGENT_DEPENDENCY"] = str(broken)
    result = subprocess.run(
        [sys.executable, str(launcher), "-"],
        input="print('must not run')", text=True, capture_output=True,
        cwd=working, env=env, timeout=30,
    )
    assert result.returncode != 0
    assert result.stderr
    assert result.stdout == ""


def test_missing_uv_explains_installation(installed_skill):
    launcher, working, env = installed_skill
    env["PATH"] = str(working)
    result = subprocess.run(
        [sys.executable, str(launcher), "-"], input="", text=True, capture_output=True,
        cwd=working, env=env, timeout=10,
    )
    assert result.returncode == 127
    assert "Surf requires uv" in result.stderr


@pytest.fixture
def session_launcher(installed_skill, tmp_path):
    """The installed launcher with a private runtime directory."""
    launcher, working, env = installed_skill
    runtime = tmp_path / "runtime"
    env = {**env, "XDG_RUNTIME_DIR": str(runtime)}
    interpreters: list[int] = []
    yield launcher, working, env, interpreters
    for pid in interpreters:
        # Only signal a process that is still this test's session worker.
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"surf_agent.session" in command and str(runtime).encode() in command:
            os.kill(pid, signal.SIGKILL)


def run_session(launcher, working, env, interpreters, *arguments, source=""):
    result = subprocess.run(
        [sys.executable, str(launcher), *arguments], input=source, text=True,
        capture_output=True, cwd=working, env=env, timeout=120,
    )
    interpreters.extend(int(pid) for pid in re.findall(r"--- interpreter (\d+) ", result.stderr))
    return result


def create_session(launcher, working, env, interpreters, *arguments, source=""):
    """Create a session and return its result together with the reported id."""
    result = run_session(launcher, working, env, interpreters, "--new-session", *arguments, source=source)
    match = re.search(r"^session_id: (\S+)$", result.stdout, re.MULTILINE)
    return result, (match.group(1) if match else None)


def metadata_block(session_id: str, idle_timeout_s: int = 1800) -> str:
    return (
        "--- BEGIN session metadata ---\n"
        f"session_id: {session_id}\n"
        f"idle_timeout_s: {idle_timeout_s}\n"
        "--- END session metadata ---\n"
    )


def test_session_mode_retains_bindings_across_launcher_invocations(session_launcher):
    launcher, working, env, interpreters = session_launcher
    first, session_id = create_session(
        launcher, working, env, interpreters, "-", source="answer = 41\nprint('initialized')"
    )
    assert first.returncode == 0, first.stderr
    assert session_id is not None and re.fullmatch(r"[0-9a-f]{8}", session_id)
    # The id is the last thing the create call prints, after the cell's own output.
    assert first.stdout == "initialized\n" + metadata_block(session_id)
    assert "cell #1, created; idle timeout 1800 s" in first.stderr

    second = run_session(
        launcher, working, env, interpreters, "--session", session_id, "-", "two words",
        source=(
            "import subprocess, sys\n"
            "assert answer == 41\n"
            "# The detached worker must keep a usable interpreter and environment:\n"
            "# uv removes the temporary environment that started the first call.\n"
            "subprocess.run([sys.executable, '-c', 'import surf_agent'], check=True)\n"
            "print(answer + 1, sys.argv)\n"
        ),
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout == "42 ['-', 'two words']\n"
    assert "cell #2, attached" in second.stderr
    assert "--- cell #2 ok" in second.stderr


def test_session_reset_clears_bindings(session_launcher):
    launcher, working, env, interpreters = session_launcher
    created, session_id = create_session(launcher, working, env, interpreters, "-", source="value = 7")
    assert created.returncode == 0, created.stderr
    reset = run_session(launcher, working, env, interpreters, "--session", session_id, "--reset")
    assert reset.returncode == 0, reset.stderr
    assert "bindings cleared" in reset.stderr

    after = run_session(
        launcher, working, env, interpreters, "--session", session_id, "-", source="print(value)",
    )
    assert after.returncode == 1
    assert "NameError" in after.stderr


def test_session_timeout_reports_the_ended_session(session_launcher):
    launcher, working, env, interpreters = session_launcher
    created, session_id = create_session(launcher, working, env, interpreters, "-", source="kept = 1")
    assert created.returncode == 0, created.stderr
    result = run_session(
        launcher, working, env, interpreters, "--session", session_id, "--timeout", "0.5", "-",
        source="import time\ntime.sleep(30)",
    )
    assert result.returncode == 1
    assert f"session {session_id} ended" in result.stderr
    assert "--new-session" in result.stderr
    assert "exceeded 0.5" in result.stderr
    # The session is gone rather than quietly remade.
    gone = run_session(launcher, working, env, interpreters, "--session", session_id, "-", source="pass")
    assert gone.returncode == 2
    assert "unknown session" in gone.stderr


def test_session_mode_rejects_a_file_source(session_launcher):
    launcher, working, env, interpreters = session_launcher
    script = working / "cell.py"
    script.write_text("print('must not run')\n")
    result = run_session(launcher, working, env, interpreters, "--new-session", str(script))
    assert result.returncode == 2
    assert "stdin" in result.stderr
    # The refusal names the caller's own flags and path, so the next attempt is
    # the redirect rather than re-authoring the file as a heredoc.
    assert f"`--new-session - < {script}`" in result.stderr
    assert "ordinary script" in result.stderr
    assert result.stdout == ""

    created, session_id = create_session(launcher, working, env, interpreters, "-", source="pass")
    assert created.returncode == 0, created.stderr
    reuse = run_session(launcher, working, env, interpreters, "--session", session_id, str(script))
    assert reuse.returncode == 2
    assert f"`--session {session_id} - < {script}`" in reuse.stderr

    nothing = run_session(launcher, working, env, interpreters, "--session", session_id)
    assert nothing.returncode == 2
    assert "pass '-' as the source" in nothing.stderr

    assert run_session(
        launcher, working, env, interpreters, "--kill-session", session_id
    ).returncode == 0


def test_unknown_session_ids_are_refused(session_launcher):
    launcher, working, env, interpreters = session_launcher
    result = run_session(launcher, working, env, interpreters, "--session", "deadbeef", "-", source="pass")
    assert result.returncode == 2
    assert "unknown session" in result.stderr
    assert result.stdout == ""
    # An id is not a name: text that no create call reported is refused, not created.
    assert run_session(
        launcher, working, env, interpreters, "--session", "workflow", "-", source="pass"
    ).returncode == 2


def test_kill_session_stops_the_interpreter(session_launcher):
    launcher, working, env, interpreters = session_launcher
    created, session_id = create_session(launcher, working, env, interpreters, "-", source="pass")
    assert created.returncode == 0, created.stderr

    killed = run_session(launcher, working, env, interpreters, "--kill-session", session_id)
    assert killed.returncode == 0, killed.stderr
    assert f"session {session_id} stopped" in killed.stdout

    again = run_session(launcher, working, env, interpreters, "--kill-session", session_id)
    assert again.returncode == 1
    assert f"no live session {session_id}" in again.stdout
    assert run_session(
        launcher, working, env, interpreters, "--session", session_id, "-", source="pass"
    ).returncode == 2


def test_list_sessions_reports_the_created_session(session_launcher):
    launcher, working, env, interpreters = session_launcher
    created, session_id = create_session(launcher, working, env, interpreters, "--name", "listed", "-", source="pass")
    assert created.returncode == 0, created.stderr
    assert session_id.startswith("listed-")

    listing = run_session(launcher, working, env, interpreters, "--list-sessions")
    assert listing.returncode == 0, listing.stderr
    assert session_id in listing.stdout
    assert f"cwd {working}" in listing.stdout

    assert run_session(launcher, working, env, interpreters, "--kill-session", session_id).returncode == 0
    empty = run_session(launcher, working, env, interpreters, "--list-sessions")
    assert empty.stdout == "no live sessions\n"


def test_session_idle_timeout_ends_the_interpreter(session_launcher):
    launcher, working, env, interpreters = session_launcher
    created, session_id = create_session(launcher, working, env, interpreters, "--ttl", "1", "-", source="kept = 1")
    assert created.returncode == 0, created.stderr
    assert "idle timeout 1 s" in created.stderr
    time.sleep(2)
    expired = run_session(launcher, working, env, interpreters, "--session", session_id, "-", source="print(kept)")
    assert expired.returncode == 2
    assert "unknown session" in expired.stderr


def test_options_after_the_source_belong_to_python(installed_skill):
    launcher, working, env = installed_skill
    result = subprocess.run(
        [sys.executable, str(launcher), "-", "--session", "x", "--reset"],
        input="import sys; print(sys.argv)", text=True, capture_output=True,
        cwd=working, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "['-', '--session', 'x', '--reset']\n"


def load_launcher():
    """Import the launcher for its pure argument handling, without resolving dependencies."""
    spec = importlib.util.spec_from_file_location(
        "surf_launcher", ROOT / "skills/surf/scripts/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_option_scoping():
    launcher = load_launcher()
    ordinary = launcher.parse_arguments(["script.py", "--session", "x"])
    assert ordinary.session_mode is False
    assert ordinary.python_arguments == ["--", "script.py", "--session", "x"]
    assert launcher.parse_arguments(["--", "script.py"]).python_arguments == ["--", "--", "script.py"]
    assert launcher.parse_arguments(["-"]).session_mode is False


def runtime_request(launcher, arguments: list[str]) -> dict:
    """The request the launcher hands the runtime for these arguments."""
    invocation = launcher.parse_arguments(arguments)
    assert invocation is not None, arguments
    assert invocation.session_mode is True
    assert invocation.python_arguments[:3] == ["-m", "surf_agent.session", "run"]
    return json.loads(invocation.python_arguments[3])


def test_launcher_builds_runtime_requests():
    launcher = load_launcher()
    assert runtime_request(launcher, ["--session", "abc12345", "-"]) == {
        "op": "cell", "mode": "reuse", "session": "abc12345", "argv": ["-"]
    }
    assert runtime_request(launcher, ["--session", "abc12345", "--timeout", "5", "-", "arg"]) == {
        "op": "cell", "mode": "reuse", "session": "abc12345", "timeout": 5.0, "argv": ["-", "arg"]
    }
    assert runtime_request(launcher, ["--new-session", "-"]) == {
        "op": "cell", "mode": "new", "argv": ["-"]
    }
    assert runtime_request(launcher, ["--new-session", "--name", "demo", "--ttl", "60", "-"]) == {
        "op": "cell", "mode": "new", "name": "demo", "ttl": 60.0, "argv": ["-"]
    }
    assert runtime_request(launcher, ["--session", "abc12345", "--reset"]) == {
        "op": "reset", "session": "abc12345"
    }
    assert runtime_request(launcher, ["--kill-session", "abc12345"]) == {
        "op": "kill", "session": "abc12345"
    }
    assert runtime_request(launcher, ["--list-sessions"]) == {"op": "list"}


def test_launcher_rejects_invalid_session_arguments():
    launcher = load_launcher()
    # A file source, a bad timeout, a mistimed option, and a missing mode are refused.
    for arguments in (
        ["--new-session", "cell.py"],
        ["--session", "workflow", "cell.py"],
        ["--session", "workflow", "--timeout", "0", "-"],
        ["--session", "workflow", "--timeout", "nan", "-"],
        ["--new-session", "--ttl", "0", "-"],
        ["--session", "workflow", "--ttl", "60", "-"],
        ["--session", "workflow", "--name", "demo", "-"],
        ["--session", "workflow", "--reset", "-"],
        ["--session", "workflow", "--reset", "--timeout", "5"],
        ["--session", "workflow", "--reset", "--ttl", "5"],
        ["--new-session", "--kill-session", "abc12345"],
        ["--kill-session"],
        ["--list-sessions", "-"],
        ["--list-sessions", "--timeout", "5"],
        ["--timeout", "5", "-"],
        ["--reset"],
        ["--session"],
        ["--new-session"],
    ):
        assert launcher.parse_arguments(arguments) is None, arguments


def test_release_pin_requests_exact_git_revision_and_extra(installed_skill):
    launcher, working, env = installed_skill
    revision = "0123456789abcdef0123456789abcdef01234567"
    (launcher.parents[1] / "runtime-revision").write_text(revision)
    env.pop("SURF_AGENT_DEPENDENCY")
    # Stub only the external installer boundary; no remote commit is implied.
    uv = working / "uv"
    uv.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[1:])\n")
    uv.chmod(0o755)
    env["PATH"] = str(working)
    result = subprocess.run(
        [sys.executable, str(launcher), "-"], input="", text=True, capture_output=True,
        cwd=working, env=env, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git@"
        "0123456789abcdef0123456789abcdef01234567#subdirectory=packages/surf-agent"
    ) in result.stdout
