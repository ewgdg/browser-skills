"""Distribute every skill and its references, without bundling the runtime."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


def test_shipped_surf_skill_has_an_immutable_runtime_pin():
    root = Path(__file__).resolve().parents[1]
    revision = (root / "skills/browser/runtime-revision").read_text().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", revision), (
        "Publish the tested runtime and pin its full commit before distributing the skill"
    )


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is needed to inspect the skill package")
def test_skill_distribution_contains_executable_payload():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["npm", "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=root, capture_output=True, text=True, check=True, timeout=20,
    )
    packed = json.loads(result.stdout)
    # npm 12 keys the report by package name; earlier versions return a list.
    (package,) = packed.values() if isinstance(packed, dict) else packed
    paths = {item["path"] for item in package["files"]}
    documents = {
        path.relative_to(root).as_posix()
        for pattern in ("skills/*/SKILL.md", "skills/*/docs/**/*.md")
        for path in root.glob(pattern)
    }
    assert documents | {
        "skills/browser/scripts/run.py",
        "skills/browser/runtime-revision",
        "skills/surf-google-search/scripts/run.py",
    } <= paths
    assert not any(path.startswith("packages/") for path in paths), "the skill installs its runtime, not bundled source"


def test_every_skill_frontmatter_parses_as_yaml():
    # Installers skip a skill whose frontmatter is invalid YAML, and an unquoted
    # value containing ": " is one: `npx skills update` then fails with no reason.
    import yaml

    root = Path(__file__).resolve().parents[1]
    for skill in sorted(root.glob("skills/*/SKILL.md")):
        frontmatter = skill.read_text().split("---\n")[1]
        fields = yaml.safe_load(frontmatter)
        assert fields["name"] == skill.parent.name, skill
        assert isinstance(fields["description"], str) and fields["description"], skill
