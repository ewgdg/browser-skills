"""Patchright behaviours the windowless launch relies on.

Neither is documented Patchright API, so these run against the installed
Patchright and fail on the version bump that changes them.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from surf_agent.backends.patchright.bridge import PatchrightRuntime
from surf_agent.backends.patchright.launch_args import capture_default_args

pytestmark = pytest.mark.skipif(
    os.environ.get("SURF_TEST_LIVE_PATCHRIGHT") != "1",
    reason="requires installed Chrome and Patchright; set SURF_TEST_LIVE_PATCHRIGHT=1",
)


def test_patchright_hands_its_full_flag_list_to_the_executable(tmp_path):
    from patchright.async_api import async_playwright

    profile = tmp_path / "profile"

    async def capture() -> list[str]:
        async with async_playwright() as playwright:
            return await capture_default_args(playwright.chromium, user_data_dir=str(profile), headless=False)

    flags = asyncio.run(capture())

    assert f"--user-data-dir={profile}" in flags
    assert "--remote-debugging-pipe" in flags
    assert not any(flag.startswith("--headless") for flag in flags)


def test_windowless_launch_returns_without_a_page(tmp_path):
    # Headed on purpose: headless Chrome opens its first page regardless, so only
    # a headed launch proves Patchright skipped its first-page wait.
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile", headless=False)
    started = time.monotonic()
    try:
        runtime.start()
        assert time.monotonic() - started < 15
        assert runtime._visible_pages() == []
        assert runtime.call("open", {"thread": "contract", "url": "about:blank"}) == "opened about:blank\n"
    finally:
        runtime.stop()
