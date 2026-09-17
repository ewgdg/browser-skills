"""The distributed skill must include its launcher without bundling the runtime."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is needed to inspect the skill package")
def test_skill_distribution_contains_executable_payload():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["npm", "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=root, capture_output=True, text=True, check=True, timeout=20,
    )
    paths = {item["path"] for item in json.loads(result.stdout)[0]["files"]}
    assert {"skills/surf/SKILL.md", "skills/surf/scripts/run.py", "skills/surf/runtime-revision"} <= paths
    assert not any(path.startswith("packages/") for path in paths), "the skill installs its runtime, not bundled source"
