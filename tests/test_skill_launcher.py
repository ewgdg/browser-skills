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
    """The installed launcher with a private runtime dir and harness session id."""
    launcher, working, env = installed_skill
    runtime = tmp_path / "runtime"
    env = {**env, "XDG_RUNTIME_DIR": str(runtime), "PI_SESSION_ID": "launcher-tests"}
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


def test_session_mode_retains_bindings_across_launcher_invocations(session_launcher):
    launcher, working, env, interpreters = session_launcher
    first = run_session(
        launcher, working, env, interpreters, "--session", "workflow", "-",
        source="answer = 41\nprint('initialized')",
    )
    assert first.returncode == 0, first.stderr
    assert first.stdout == "initialized\n"
    assert "cell #1, created" in first.stderr

    second = run_session(
        launcher, working, env, interpreters, "--session", "workflow", "-", "two words",
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
    assert run_session(
        launcher, working, env, interpreters, "--session", "workflow", "-",
        source="value = 7",
    ).returncode == 0
    reset = run_session(launcher, working, env, interpreters, "--session", "workflow", "--reset")
    assert reset.returncode == 0, reset.stderr
    assert "bindings cleared" in reset.stderr

    after = run_session(
        launcher, working, env, interpreters, "--session", "workflow", "-", source="print(value)",
    )
    assert after.returncode == 1
    assert "NameError" in after.stderr


def test_session_timeout_reports_replacement(session_launcher):
    launcher, working, env, interpreters = session_launcher
    result = run_session(
        launcher, working, env, interpreters, "--session", "workflow", "--timeout", "0.5", "-",
        source="import time\ntime.sleep(30)",
    )
    assert result.returncode == 1
    assert "interpreter replaced" in result.stderr
    assert "exceeded 0.5" in result.stderr


def test_session_mode_rejects_a_file_source(session_launcher):
    launcher, working, env, interpreters = session_launcher
    script = working / "cell.py"
    script.write_text("print('must not run')\n")
    result = run_session(launcher, working, env, interpreters, "--session", "workflow", str(script))
    assert result.returncode == 2
    assert "stdin" in result.stderr
    assert result.stdout == ""


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
    assert launcher.split_options(["script.py", "--session", "x"]) == (
        None, None, False, ["script.py", "--session", "x"]
    )
    assert launcher.split_options(["--", "script.py"]) == (None, None, False, ["--", "script.py"])
    assert launcher.split_options(["--session", "workflow", "--timeout", "5", "-", "arg"]) == (
        "workflow", "5", False, ["-", "arg"]
    )
    assert launcher.split_options(["--reset"]) == (None, None, True, [])
    # A launcher option without its value falls through to ordinary Python.
    assert launcher.split_options(["--session"]) == (None, None, False, ["--session"])


def test_launcher_rejects_invalid_session_arguments():
    launcher = load_launcher()
    assert launcher.session_arguments("workflow", None, False, ["-"]) == [
        "-m", "surf_agent.session", "cell", "--session", "workflow", "-"
    ]
    assert launcher.session_arguments("workflow", "5", False, ["-"]) == [
        "-m", "surf_agent.session", "cell", "--session", "workflow", "--timeout", "5", "-"
    ]
    assert launcher.session_arguments("workflow", None, True, []) == [
        "-m", "surf_agent.session", "reset", "--session", "workflow"
    ]
    # A file source, an empty name, a bad timeout, and --reset with a timeout are all refused.
    for arguments in (("workflow", None, False, ["cell.py"]), ("", None, False, ["-"]),
                      ("workflow", "0", False, ["-"]), ("workflow", "nan", False, ["-"]),
                      ("workflow", "5", True, []), ("workflow", None, True, ["-"])):
        assert launcher.session_arguments(*arguments) is None, arguments


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
