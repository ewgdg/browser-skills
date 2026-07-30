from __future__ import annotations

import os
from pathlib import Path

import pytest

LIVE_PROFILE_ENV = "SURF_CHATGPT_LIVE_PROFILE_DIR"


def pytest_addoption(parser: pytest.Parser) -> None:
    live_group = parser.getgroup("live_chatgpt")
    live_group.addoption(
        "--live-chatgpt",
        action="store_true",
        help="Run the serial live ChatGPT compatibility gate.",
    )
    live_group.addoption(
        "--live-chatgpt-profile",
        type=Path,
        default=None,
        help=f"Authenticated dedicated Surf profile (or set {LIVE_PROFILE_ENV}).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_chatgpt: serial live ChatGPT compatibility gate; requires explicit opt-in",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--live-chatgpt"):
        return
    skip_live = pytest.mark.skip(reason="requires explicit --live-chatgpt opt-in")
    for item in items:
        if "live_chatgpt" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def live_chatgpt_profile(request: pytest.FixtureRequest) -> Path:
    configured = request.config.getoption("--live-chatgpt-profile")
    profile = configured or os.environ.get(LIVE_PROFILE_ENV)
    if profile is None:
        pytest.fail(
            f"--live-chatgpt requires --live-chatgpt-profile or {LIVE_PROFILE_ENV}"
        )
    resolved = Path(profile).expanduser().resolve()
    if not resolved.is_dir():
        pytest.fail("the configured live ChatGPT profile is not a directory")
    return resolved
