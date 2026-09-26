"""Documented Surf signatures match the runtime, so agents can copy them instead of probing."""

import inspect
from pathlib import Path
import re

import pytest

from surf_agent import Browser, Thread

ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = ROOT / "skills/surf/docs/python-api.md"
SKILL = ROOT / "skills/surf/SKILL.md"
HANDLES = {"thread": Thread, "browser": Browser}


def public_methods(cls: type) -> list[str]:
    return [name for name, member in vars(cls).items() if callable(member) and not name.startswith("_")]


def rendered(cls: type, method: str) -> str:
    """The call form agents write: parameter names and defaults, annotations only for the result."""
    signature = inspect.signature(getattr(cls, method))
    parameters = [parameter.replace(annotation=inspect.Parameter.empty) for parameter in signature.parameters.values() if parameter.name != "self"]
    call = str(signature.replace(parameters=parameters, return_annotation=inspect.Signature.empty))
    result = signature.return_annotation
    return f"{method}{call} -> {result if isinstance(result, str) else inspect.formatannotation(result)}"


def documented(text: str, pattern: str) -> dict[tuple[str, str], str]:
    return {(match["handle"], match["method"]): match["signature"] for match in re.finditer(pattern, text, flags=re.M)}


API_HEADING = r"^### `(?P<handle>thread|browser)\.(?P<signature>(?P<method>\w+)\(.*\) -> .+)`$"
SKILL_LINE = r"^(?P<handle>thread)\.(?P<signature>(?P<method>\w+)\(.*?\) -> [^#\n]*?)\s*(?:#.*)?$"


@pytest.mark.parametrize("handle", sorted(HANDLES))
def test_python_api_heads_every_method_with_its_runtime_signature(handle):
    cls = HANDLES[handle]
    headings = documented(PYTHON_API.read_text(), API_HEADING)
    expected = {(handle, method): rendered(cls, method) for method in public_methods(cls)}
    assert {key: value for key, value in headings.items() if key[0] == handle} == expected


def test_skill_lists_every_thread_method_with_its_runtime_signature():
    expected = {("thread", method): rendered(Thread, method) for method in public_methods(Thread)}
    assert documented(SKILL.read_text(), SKILL_LINE) == expected
