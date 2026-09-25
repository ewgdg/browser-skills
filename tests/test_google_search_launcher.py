"""Process-level contracts for the Google Search skill launcher."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ("SURF_AGENT_DEPENDENCY", "SURF_GOOGLE_SEARCH_DEPENDENCY")


def install_skills(destination: Path, *names: str) -> Path:
    for name in names:
        shutil.copytree(ROOT / "skills" / name, destination / name,
                        ignore=shutil.ignore_patterns(".*", "__pycache__"))
    return destination / "surf-google-search/scripts/run.py"


def environment_without_overrides() -> dict[str, str]:
    env = os.environ.copy()
    for name in OVERRIDES:
        env.pop(name, None)
    return env


def test_requires_the_sibling_surf_skill(tmp_path):
    launcher = install_skills(tmp_path / "skills", "surf-google-search")
    result = subprocess.run(
        [sys.executable, str(launcher), "query"],
        text=True, capture_output=True, cwd=tmp_path,
        env=environment_without_overrides(), timeout=10,
    )
    assert result.returncode == 2
    assert "Surf skill" in result.stderr
    assert result.stdout == ""


def test_shares_the_surf_runtime_pin(tmp_path):
    skills = tmp_path / "skills"
    launcher = install_skills(skills, "surf", "surf-google-search")
    (skills / "surf/runtime-revision").write_text("not-a-commit\n")
    result = subprocess.run(
        [sys.executable, str(launcher), "query"],
        text=True, capture_output=True, cwd=tmp_path,
        env=environment_without_overrides(), timeout=10,
    )
    assert result.returncode == 2
    assert "invalid runtime pin" in result.stderr
    assert result.stdout == ""


@pytest.fixture(scope="module")
def local_wheels(tmp_path_factory):
    output = tmp_path_factory.mktemp("built wheels")
    for package in ("surf-agent", "surf-google-search"):
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(output), str(ROOT / "packages" / package)],
            check=True, capture_output=True, text=True, timeout=120,
        )
    return {
        "SURF_AGENT_DEPENDENCY": str(next(output.glob("surf_agent-*.whl"))),
        "SURF_GOOGLE_SEARCH_DEPENDENCY": str(next(output.glob("surf_google_search-*.whl"))),
    }


def test_routes_arguments_to_the_installed_cli(tmp_path, local_wheels):
    launcher = install_skills(tmp_path / "skills", "surf", "surf-google-search")
    working = tmp_path / "unrelated project"
    working.mkdir()
    # Neither the caller's project config nor a same-named module in its working
    # directory may replace the pinned runtime.
    (working / "pyproject.toml").write_text("invalid TOML [")
    (working / "surf_google_search").mkdir()
    (working / "surf_google_search/__main__.py").write_text("print('shadowed')\n")
    env = environment_without_overrides() | local_wheels
    result = subprocess.run(
        [sys.executable, str(launcher), "--page-count", "9", "query"],
        text=True, capture_output=True, cwd=working, env=env, timeout=120,
    )
    assert result.returncode != 0, result.stdout
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["error"]["type"] == "invalid_request"
