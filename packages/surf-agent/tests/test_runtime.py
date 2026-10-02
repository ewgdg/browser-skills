import json
import shlex
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import surf_agent.backends.patchright.bridge as patchright_bridge
from surf_agent.backends.patchright.bridge import PageSlot, PatchrightRuntime
from surf_agent.errors import BridgeUnavailable
from surf_agent.snapshots import SnapshotCapture, choose_snapshot_diff
from surf_agent.runtime import (
    find_chrome_bin,
    APP_DIRS,
    SurfAgent,
    SurfAgentError,
    config_file,
    default_patchright_profile_dir,
    surf_agent_config_dir,
    surf_agent_data_dir,
    surf_agent_state_dir,
)


def make_agent(home: str, **kwargs) -> SurfAgent:
    """An agent whose lifecycle state stays under *home*, with a stub Chrome command."""
    with patch.dict("os.environ", {"SURF_AGENT_HOME": home}):
        return SurfAgent(chrome_bin="chrome", command_timeout_s=1, **kwargs)


def snapshot_text(changes=None, *, line_count=220):
    changes = changes or {}
    lines = ["snapshot:"]
    for index in range(line_count):
        text = changes.get(index, f"stable content line {index:03d}")
        lines.append(f"uid=g{index}: {text} {'x' * 30}")
    return "\n".join(lines) + "\n"


def snapshot_capture(text=None, **overrides):
    url = overrides.pop("url", "https://example.test/path#section")
    return SnapshotCapture(
        text=text if text is not None else snapshot_text(),
        page_id=overrides.pop("page_id", 22),
        url=url,
        title=overrides.pop("title", "Example"),
        origin=overrides.pop("origin", "https://example.test"),
        url_without_fragment=overrides.pop(
            "url_without_fragment", "https://example.test/path"
        ),
    )


class RuntimeTests(unittest.TestCase):
    def test_surf_agent_home_overrides_all_default_roots(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"SURF_AGENT_HOME": tmp}, clear=True),
        ):
            home = Path(tmp)
            self.assertEqual(surf_agent_config_dir(), home)
            self.assertEqual(surf_agent_state_dir(), home)
            self.assertEqual(surf_agent_data_dir(), home)
            self.assertEqual(config_file(), home / "config.json")
            self.assertEqual(default_patchright_profile_dir(), home / "profiles" / "chrome")

    def test_platformdirs_fallback_replaces_package_local_data_dir(self):
        with patch.dict("os.environ", {}, clear=True):
            package_local = Path(__file__).resolve().parents[1] / ".surf-agent"
            self.assertEqual(
                config_file(), Path(APP_DIRS.user_config_dir) / "config.json"
            )
            self.assertEqual(
                default_patchright_profile_dir(),
                Path(APP_DIRS.user_data_dir) / "profiles" / "chrome",
            )
            self.assertNotEqual(
                default_patchright_profile_dir(), package_local / "profiles" / "chrome"
            )

    def test_default_profiles_live_under_surf_agent_data_dir(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"SURF_AGENT_HOME": tmp}, clear=True),
        ):
            self.assertEqual(
                default_patchright_profile_dir(), surf_agent_data_dir() / "profiles" / "chrome"
            )

    def test_patchright_profile_open_uses_patchright_profile_dir_and_class(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                "os.environ",
                {
                    "SURF_AGENT_PATCHRIGHT_APP_ID": "surf-agent-test",
                    "SURF_AGENT_PATCHRIGHT_CLASS": "surf-agent-window",
                },
                clear=True,
            ),
        ):
            profile = Path(tmp) / "patchright-profile"
            agent = make_agent(tmp, patchright_profile_dir=profile)
            pops = []
            with patch(
                "surf_agent.backends.local_bridge.subprocess.Popen",
                side_effect=lambda *a, **kw: pops.append((a, kw)) or object(),
            ):
                with patch.object(
                    agent.patchright_client, "_health_ok", return_value=False
                ):
                    self.assertEqual(agent.profile_open("https://x.test"), 0)

        self.assertEqual(
            pops[0][0][0],
            [
                "chrome",
                "--class=surf-agent-window",
                f"--user-data-dir={profile}",
                "--new-window",
                "--name=surf-agent-test",
                "https://x.test",
            ],
        )

    def test_patchright_new_window_needs_no_open_page(self):
        # A windowless launch has no page to anchor a CDP session; opening one would
        # show a foreground window and steal focus.
        class FakePage:
            def __init__(self, url="about:blank", *, target_id="page-target"):
                self.url = url
                self.target_id = target_id
                self.closed = False

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

        class FakeSession:
            def __init__(self, context, page):
                self.context = context
                self.page = page

            def send(self, method, params=None):
                if method == "Target.getTargetInfo":
                    return {"targetInfo": {"targetId": self.page.target_id}}
                self.context.cdp_calls.append((method, params))
                self.context.pages.append(FakePage(params["url"], target_id="target-1"))
                return {"targetId": "target-1"}

            def detach(self):
                pass

        class FakeContext:
            def __init__(self):
                self.pages = []
                self.cdp_calls = []
                self.new_page_calls = 0
                self.browser = types.SimpleNamespace(
                    new_browser_cdp_session=lambda: FakeSession(self, None)
                )

            def new_page(self):
                self.new_page_calls += 1
                raise AssertionError("a new page would open a foreground window")

            def new_cdp_session(self, page):
                return FakeSession(self, page)

        context = FakeContext()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = context

        slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

        self.assertEqual(slot.page.url, "https://welcome.test/")
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(
            context.cdp_calls,
            [
                (
                    "Target.createTarget",
                    {
                        "url": "https://welcome.test/",
                        "newWindow": True,
                        "background": True,
                    },
                )
            ],
        )

    def test_patchright_new_page_restarts_closed_context_then_retries_cdp(self):
        class FakePage:
            def __init__(self, url="about:blank", *, target_id="page-target"):
                self.url = url
                self.target_id = target_id
                self.closed = False

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

        class FakeSession:
            def __init__(self, context, page):
                self.context = context
                self.page = page
                self.detached = False

            def send(self, method, params=None):
                if method == "Target.getTargetInfo":
                    return {"targetInfo": {"targetId": self.page.target_id}}
                self.context.cdp_calls.append((method, params))
                if self.context.fail_closed:
                    raise RuntimeError(patchright_bridge.CLOSED_TARGET_MESSAGE)
                page = FakePage(params["url"], target_id="target-1")
                self.context.pages.append(page)
                return {"targetId": "target-1"}

            def detach(self):
                self.detached = True

        class FakeContext:
            def __init__(self, *, fail_closed=False):
                self.pages = []
                self.cdp_calls = []
                self.fail_closed = fail_closed
                self.new_page_calls = 0
                self.browser = types.SimpleNamespace(
                    new_browser_cdp_session=lambda: FakeSession(self, None)
                )

            def new_page(self):
                self.new_page_calls += 1
                page = FakePage()
                self.pages.append(page)
                return page

            def new_cdp_session(self, page):
                return FakeSession(self, page)

        first_context = FakeContext(fail_closed=True)
        second_context = FakeContext()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = first_context
        restarts = []

        async def restart():
            restarts.append(True)
            runtime.browser_or_context = second_context

        with patch.object(runtime, "_restart_closed_context", side_effect=restart):
            slot = runtime._run(
                runtime._new_page("thread", url="https://welcome.test/")
            )

        self.assertEqual(len(restarts), 1)
        self.assertEqual(slot.page.url, "https://welcome.test/")
        self.assertEqual(
            first_context.cdp_calls,
            [
                (
                    "Target.createTarget",
                    {
                        "url": "https://welcome.test/",
                        "newWindow": True,
                        "background": True,
                    },
                )
            ],
        )
        self.assertEqual(
            second_context.cdp_calls,
            [
                (
                    "Target.createTarget",
                    {
                        "url": "https://welcome.test/",
                        "newWindow": True,
                        "background": True,
                    },
                )
            ],
        )

    def test_patchright_open_recreates_closed_target_without_double_navigation(self):
        class DeadPage:
            url = "about:blank"

            def __init__(self):
                self.closed = False

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

            def goto(self, url, wait_until=None):
                raise RuntimeError(patchright_bridge.CLOSED_TARGET_MESSAGE)

        class CreatedPage:
            def __init__(self, url="about:blank", *, target_id="page-target"):
                self.url = url
                self.target_id = target_id
                self.closed = False
                self.goto_calls = []

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

            def goto(self, url, wait_until=None):
                self.goto_calls.append(url)
                raise AssertionError(
                    "CDP-created replacement page should not be navigated again"
                )

        class FakeSession:
            def __init__(self, context, page):
                self.context = context
                self.page = page
                self.detached = False

            def send(self, method, params=None):
                if method == "Target.getTargetInfo":
                    return {"targetInfo": {"targetId": self.page.target_id}}
                self.context.cdp_calls.append((method, params))
                page = CreatedPage(params["url"], target_id="target-1")
                self.context.pages.append(page)
                return {"targetId": "target-1"}

            def detach(self):
                self.detached = True

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages
                self.cdp_calls = []
                self.browser = types.SimpleNamespace(
                    new_browser_cdp_session=lambda: FakeSession(self, None)
                )

            def new_page(self):
                page = CreatedPage()
                self.pages.append(page)
                return page

            def new_cdp_session(self, page):
                return FakeSession(self, page)

        dead = DeadPage()
        context = FakeContext([dead])
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = context
        runtime.pages["thread"] = patchright_bridge.PageSlot(page=dead, page_id=1)

        self.assertEqual(
            runtime.call("open", {"thread": "thread", "url": "https://example.test/"}),
            "opened https://example.test/\n",
        )
        self.assertEqual(runtime.pages["thread"].page.url, "https://example.test/")
        self.assertEqual(runtime.pages["thread"].page.goto_calls, [])
        self.assertEqual(
            context.cdp_calls,
            [
                (
                    "Target.createTarget",
                    {
                        "url": "https://example.test/",
                        "newWindow": True,
                        "background": True,
                    },
                )
            ],
        )

    def test_patchright_runtime_open_snapshot_click_and_text(self):
        class FakeElement:
            def __init__(self):
                self.clicked = False

            def evaluate(self, script):
                if "tagName" in script:
                    return "button"
                raise AssertionError(f"unexpected evaluate script: {script}")

            def get_attribute(self, name):
                return {"role": "button", "aria-label": "Submit"}.get(name, "")

            def inner_text(self, timeout=None):
                return "Submit"

            def input_value(self, timeout=None):
                return ""

            def bounding_box(self):
                return {"x": 1, "y": 2, "width": 3, "height": 4}

            def is_visible(self, timeout=None):
                return True

            def click(self, timeout=None):
                self.clicked = True

            def fill(self, text, timeout=None):
                self.filled = text

        class FakeLocatorGroup:
            def __init__(self, items):
                self.items = items

            def count(self):
                return len(self.items)

            def nth(self, index):
                return self.items[index]

            @property
            def first(self):
                return self.items[0]

            def click(self, timeout=None):
                return self.first.click()

            def fill(self, text, timeout=None):
                return self.first.fill(text)

            def evaluate(self, script, timeout=None):
                if script != "element => element.tagName":
                    raise AssertionError(f"unexpected locator script: {script}")
                return "INPUT"

        class FakeBodyLocator:
            def inner_text(self, timeout=None):
                return "Body text"

        class FakePage:
            def __init__(self, url="about:blank", *, target_id="page-target"):
                self.url = url
                self.target_id = target_id
                self.closed = False
                self.title_value = "Example"
                self.actionable = FakeElement()
                self.keyboard = types.SimpleNamespace(
                    type=self._type, press=self._press
                )
                self.screenshot_calls = []

            def is_closed(self):
                return self.closed

            def goto(self, url, wait_until=None):
                self.url = url

            def locator(self, selector):
                if selector == "aria-ref=e2":
                    return FakeLocatorGroup([self.actionable])
                if selector == "button":
                    return FakeLocatorGroup([self.actionable])
                if selector == "body":
                    return FakeBodyLocator()
                raise AssertionError(f"unexpected selector: {selector}")

            def aria_snapshot(self, *args, **kwargs):
                return '- button "Submit" [ref=e2]'

            def title(self):
                return self.title_value

            def content(self):
                return "Body text"

            def evaluate(self, code):
                return {"code": code}

            def close(self):
                self.closed = True

            def screenshot(self, path, full_page=False):
                self.screenshot_calls.append({"path": path, "full_page": full_page})

            def bring_to_front(self):
                self.focused = True

            def _type(self, text):
                self.typed = text

            def _press(self, key):
                self.pressed = key

        class FakeSession:
            def __init__(self, context, page):
                self.context = context
                self.page = page
                self.detached = False

            def send(self, method, params=None):
                if method == "Target.getTargetInfo":
                    return {"targetInfo": {"targetId": self.page.target_id}}
                self.context.cdp_calls.append((method, params))
                page = FakePage(params["url"], target_id="target-1")
                self.context.pages.append(page)
                return {"targetId": "target-1"}

            def detach(self):
                self.detached = True

        class FakeContext:
            def __init__(self):
                self.pages = []
                self.cdp_calls = []
                self.browser = types.SimpleNamespace(
                    new_browser_cdp_session=lambda: FakeSession(self, None)
                )

            def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

            def new_cdp_session(self, page):
                return FakeSession(self, page)

        context = FakeContext()
        runtime = PatchrightRuntime(
            profile_dir=Path("/tmp/surf-patchright-test"),
            app_id="surf-agent-test",
            window_class="surf-agent-window",
        )
        runtime.browser_or_context = context

        self.assertEqual(
            runtime.call("open", {"thread": "thread", "url": "https://example.test/"}),
            "opened https://example.test/\n",
        )
        self.assertEqual(
            context.cdp_calls,
            [
                (
                    "Target.createTarget",
                    {
                        "url": "https://example.test/",
                        "newWindow": True,
                        "background": True,
                    },
                )
            ],
        )
        snapshot = runtime.call("snapshot", {"thread": "thread"})
        self.assertIn("[ref=e2]", snapshot)
        self.assertEqual(
            runtime.call("fill", {"thread": "thread", "uid": "@e2", "text": "hello"}),
            "filled\n",
        )
        self.assertEqual(runtime.pages["thread"].page.actionable.filled, "hello")
        snapshot = runtime.call("snapshot", {"thread": "thread"})
        self.assertIn("[ref=e2]", snapshot)
        self.assertEqual(
            runtime.call("click", {"thread": "thread", "uid": "e2"}), "clicked\n"
        )
        self.assertTrue(runtime.pages["thread"].page.actionable.clicked)
        self.assertEqual(
            runtime.call("type", {"thread": "thread", "text": "typed text"}), "typed\n"
        )
        self.assertEqual(runtime.pages["thread"].page.typed, "typed text")
        self.assertEqual(
            runtime.call(
                "screenshot", {"thread": "thread", "path": "/tmp/viewport.png"}
            ),
            "screenshot: /tmp/viewport.png\n",
        )
        self.assertEqual(
            runtime.pages["thread"].page.screenshot_calls[-1],
            {"path": "/tmp/viewport.png", "full_page": False},
        )
        self.assertEqual(
            runtime.call(
                "screenshot",
                {"thread": "thread", "path": "/tmp/full.png", "fullPage": True},
            ),
            "screenshot: /tmp/full.png\n",
        )
        self.assertEqual(
            runtime.pages["thread"].page.screenshot_calls[-1],
            {"path": "/tmp/full.png", "full_page": True},
        )
        self.assertEqual(runtime.call("text", {"thread": "thread"}), "Body text\n")

        state = json.loads(runtime.call("state", {"thread": "thread"}))
        self.assertEqual(
            state,
            {
                "backend": "patchright",
                "open": True,
                "thread": "thread",
                "page_id": 1,
                "url": "https://example.test/",
                "title": "Example",
            },
        )
        listing = json.loads(runtime.call("list", {}))
        self.assertEqual(
            listing,
            {
                "backend": "patchright",
                "pages": [
                    {
                        "thread": "thread",
                        "page_id": 1,
                        "url": "https://example.test/",
                        "title": "Example",
                    }
                ],
            },
        )

    def test_patchright_runtime_renames_a_thread_without_replacing_its_page(self):
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        live_page_slot = object()
        runtime.pages["temporary"] = live_page_slot  # type: ignore[assignment]

        result = runtime.call(
            "rename-thread",
            {"thread": "temporary", "destination_thread": "session"},
        )

        self.assertEqual(result, "renamed session\n")
        self.assertNotIn("temporary", runtime.pages)
        self.assertIs(runtime.pages["session"], live_page_slot)

    def test_patchright_actions_delegate_iframe_refs_and_reject_stale_refs(self):
        class FakeNativeRefLocator:
            def __init__(self, count):
                self.match_count = count
                self.clicked = False

            def count(self):
                return self.match_count

            def click(self, timeout=None):
                self.clicked = True

        class FakePage:
            def __init__(self):
                self.iframe_button = FakeNativeRefLocator(1)
                self.stale = FakeNativeRefLocator(0)
                self.selector_injection = FakeNativeRefLocator(1)

            def is_closed(self):
                return False

            def locator(self, selector):
                if selector == "aria-ref=f1e2":
                    return self.iframe_button
                if selector == "aria-ref=e404":
                    return self.stale
                if selector == "aria-ref=e2 >> css=body":
                    return self.selector_injection
                raise AssertionError(f"unexpected selector: {selector}")

        page = FakePage()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = object()
        runtime.pages["thread"] = PageSlot(page=page, page_id=1)

        self.assertEqual(
            runtime.call("click", {"thread": "thread", "uid": "f1e2"}), "clicked\n"
        )
        self.assertTrue(page.iframe_button.clicked)
        with self.assertRaisesRegex(RuntimeError, "Capture a new snapshot"):
            runtime.call("click", {"thread": "thread", "uid": "@e404"})
        with self.assertRaisesRegex(RuntimeError, "Capture a new snapshot"):
            runtime.call("click", {"thread": "thread", "uid": "@e2 >> css=body"})
        self.assertFalse(page.selector_injection.clicked)

    def test_patchright_bridge_screenshot_uses_viewport_by_default_and_full_page_when_requested(
        self,
    ):
        class FakePage:
            def __init__(self):
                self.screenshot_calls = []

            def is_closed(self):
                return False

            def screenshot(self, path, full_page=False):
                self.screenshot_calls.append({"path": path, "full_page": full_page})

        page = FakePage()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = object()
        runtime.pages["thread"] = patchright_bridge.PageSlot(page=page, page_id=1)

        self.assertEqual(
            runtime.call(
                "screenshot", {"thread": "thread", "path": "/tmp/viewport.png"}
            ),
            "screenshot: /tmp/viewport.png\n",
        )
        self.assertEqual(
            runtime.call(
                "screenshot",
                {
                    "thread": "thread",
                    "path": "/tmp/string-false.png",
                    "fullPage": "false",
                },
            ),
            "screenshot: /tmp/string-false.png\n",
        )
        self.assertEqual(
            runtime.call(
                "screenshot",
                {"thread": "thread", "path": "/tmp/full.png", "fullPage": True},
            ),
            "screenshot: /tmp/full.png\n",
        )
        self.assertEqual(
            page.screenshot_calls,
            [
                {"path": "/tmp/viewport.png", "full_page": False},
                {"path": "/tmp/string-false.png", "full_page": False},
                {"path": "/tmp/full.png", "full_page": True},
            ],
        )

    def test_patchright_snapshot_preserves_native_and_literal_refs(self):
        class FakePage:
            def aria_snapshot(self, *args, **kwargs):
                return '- button "[ref=e999]" [ref=e188]\n- paragraph [ref=e189]: Hello [ref=e777]'

        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        slot = patchright_bridge.PageSlot(page=FakePage(), page_id=1)

        snapshot = runtime._run(runtime._snapshot(slot))

        self.assertIn("[ref=e188]", snapshot)
        self.assertIn("[ref=e189]", snapshot)
        self.assertIn('"[ref=e999]"', snapshot)
        self.assertIn("Hello [ref=e777]", snapshot)

    def test_patchright_snapshot_passes_playwright_cli_aria_options(self):
        class FakePage:
            def __init__(self):
                self.calls = []

            def aria_snapshot(self, **kwargs):
                self.calls.append(kwargs)
                return '- page "Example"'

        page = FakePage()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        slot = patchright_bridge.PageSlot(page=page, page_id=1)

        with (
            patch.object(patchright_bridge, "SNAPSHOT_DEPTH", 4),
            patch.object(patchright_bridge, "SNAPSHOT_BOXES", True),
        ):
            snapshot = runtime._run(runtime._snapshot(slot))

        self.assertIn('- page "Example"', snapshot)
        self.assertEqual(
            page.calls,
            [
                {
                    "mode": "ai",
                    "timeout": patchright_bridge.SNAPSHOT_ARIA_TIMEOUT_MS,
                    "depth": 4,
                    "boxes": True,
                }
            ],
        )

    def test_patchright_snapshot_default_depth_and_boxes_are_explicit(self):
        class FakePage:
            def __init__(self):
                self.calls = []

            def aria_snapshot(self, **kwargs):
                self.calls.append(kwargs)
                return "snapshot body"

        page = FakePage()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime._run(
            runtime._snapshot(patchright_bridge.PageSlot(page=page, page_id=1))
        )

        self.assertEqual(
            page.calls,
            [
                {
                    "mode": "ai",
                    "timeout": patchright_bridge.SNAPSHOT_ARIA_TIMEOUT_MS,
                    "depth": None,
                    "boxes": False,
                }
            ],
        )

    def test_patchright_bridge_client_timeout_has_clear_error(self):
        from surf_agent.backends.patchright.backend import PatchrightBridgeClient

        client = PatchrightBridgeClient(
            timeout_s=1.0, port=9555, profile_dir=Path("/tmp/surf-patchright-profile")
        )
        with (
            patch.object(client, "_ensure_running", return_value=None),
            patch(
                "surf_agent.backends.local_bridge.urllib.request.urlopen",
                side_effect=TimeoutError("timed out"),
            ),
        ):
            with self.assertRaisesRegex(
                BridgeUnavailable, "Patchright bridge tool snapshot timed out after 1s"
            ):
                client.call_tool("snapshot", {"thread": "default"})

    def test_patchright_bridge_client_ensure_running_spawns_bridge_module_with_profile_and_port(
        self,
    ):
        from surf_agent.backends.patchright.backend import PatchrightBridgeClient

        profile_dir = Path("/tmp/surf-patchright-profile")
        client = PatchrightBridgeClient(
            timeout_s=1.0, port=9555, profile_dir=profile_dir
        )
        pops = []

        with (
            patch.object(client, "_health_ok", side_effect=[False, True]),
            patch(
                "surf_agent.backends.local_bridge.subprocess.Popen",
                side_effect=lambda *a, **kw: pops.append((a, kw)) or object(),
            ),
            patch(
                "surf_agent.backends.local_bridge.time.monotonic",
                side_effect=[0.0, 0.0, 0.1],
            ),
            patch("surf_agent.backends.local_bridge.time.sleep", return_value=None),
        ):
            client._ensure_running()

        command = pops[0][0][0]
        self.assertEqual(
            command[:6],
            [
                sys.executable,
                "-m",
                "surf_agent.backends.patchright.bridge",
                "--port",
                "9555",
                "--profile-dir",
            ],
        )
        self.assertEqual(command[6], str(profile_dir))

    def test_snapshot_diff_gates_fall_back_for_large_small_savings_and_many_hunks(self):
        cases = [
            (
                snapshot_text({i: f"old {i}" for i in range(120)}, line_count=120),
                snapshot_text({i: f"new {i}" for i in range(120)}, line_count=120),
                "diff too large",
            ),
            (
                "snapshot:\n" + "\n".join(f"L{i}" for i in range(80)) + "\n",
                "snapshot:\n"
                + "\n".join("CHANGED" if i == 40 else f"L{i}" for i in range(80))
                + "\n",
                "saved chars < 250",
            ),
            (
                snapshot_text(line_count=260),
                snapshot_text(
                    {i * 20: f"change {i}" for i in range(9)}, line_count=260
                ),
                "hunks > 8",
            ),
        ]
        for before_text, after_text, reason in cases:
            with self.subTest(reason=reason):
                decision = choose_snapshot_diff(
                    snapshot_capture(before_text), snapshot_capture(after_text),
                    before_label="observation 1", after_label="observation 2",
                )

                self.assertFalse(decision.used_diff)
                self.assertIn(f"# snapshot fallback: {reason}", decision.output)
                self.assertIn("snapshot:", decision.output)

    def test_snapshot_diff_no_changes_emits_compact_header(self):
        capture = snapshot_capture(snapshot_text())
        decision = choose_snapshot_diff(capture, capture, before_label="observation 1", after_label="observation 2")

        self.assertTrue(decision.used_diff)
        self.assertEqual(decision.output, "--- observation 1\n+++ observation 2\n# snapshot-diff: no changes\n")

    def test_snapshot_diff_metadata_vetoes_only_identity_changes(self):
        before = snapshot_capture(
            snapshot_text({20: "old"}), url="https://example.test/path#old"
        )
        useful_after = snapshot_text({20: "new"})

        origin_change = choose_snapshot_diff(
            before,
            snapshot_capture(
                useful_after,
                url="https://other.test/path#old",
                origin="https://other.test",
                url_without_fragment="https://other.test/path",
            ),
            before_label="observation 1", after_label="observation 2",
        )
        self.assertFalse(origin_change.used_diff)
        self.assertIn("origin changed", origin_change.output)

        page_change = choose_snapshot_diff(
            before, snapshot_capture(useful_after, page_id=23),
            before_label="observation 1", after_label="observation 2",
        )
        self.assertFalse(page_change.used_diff)
        self.assertIn("page changed", page_change.output)

        hash_only = choose_snapshot_diff(
            before,
            snapshot_capture(
                useful_after,
                url="https://example.test/path#new",
                url_without_fragment="https://example.test/path",
            ),
            before_label="observation 1", after_label="observation 2",
        )
        self.assertTrue(hash_only.used_diff)

        path_and_title = choose_snapshot_diff(
            before,
            snapshot_capture(
                useful_after,
                url="https://example.test/other",
                url_without_fragment="https://example.test/other",
                title="Other",
            ),
            before_label="observation 1", after_label="observation 2",
        )
        self.assertTrue(path_and_title.used_diff)


if __name__ == "__main__":
    unittest.main()


class LaunchPreflightTests(unittest.TestCase):
    def test_patchright_profile_open_runs_under_lifecycle_launch_guard(self):
        class Guard:
            def __init__(self):
                self.events = []

            def launch_guard(self, *, health_check):
                from contextlib import contextmanager

                @contextmanager
                def guard():
                    self.events.append("entered")
                    assert health_check() is False
                    yield
                    self.events.append("exited")

                return guard()

        with TemporaryDirectory() as tmp:
            agent = make_agent(tmp, patchright_profile_dir=Path(tmp) / "profile")
            guard = Guard()
            agent.lifecycle = guard
            with (
                patch.object(agent.patchright_client, "_health_ok", return_value=False),
                patch(
                    "surf_agent.backends.patchright.backend.subprocess.Popen",
                    return_value=object(),
                ),
            ):
                self.assertEqual(agent.profile_open(), 0)
        self.assertEqual(guard.events, ["entered", "exited"])


class LaunchFailureTests(unittest.TestCase):
    def test_profile_open_import_failure_prevents_browser_launch(self):
        class FailingImporter:
            def run(self, force):
                raise SurfAgentError("import failed")

        with TemporaryDirectory() as tmp:
            agent = make_agent(tmp, patchright_profile_dir=Path(tmp) / "profile")
            from surf_agent.chrome_lifecycle import ChromeLifecycleCoordinator

            agent.lifecycle = ChromeLifecycleCoordinator(
                destination_root=agent.patchright_profile_dir,
                state_root=Path(tmp) / "state",
                importer=FailingImporter(),
                process_inspector=lambda _path: False,
            )
            with (
                patch.object(agent.patchright_client, "_health_ok", return_value=False),
                patch("surf_agent.backends.patchright.backend.subprocess.Popen") as popen,
                self.assertRaisesRegex(SurfAgentError, "import failed"),
            ):
                agent.profile_open()
        popen.assert_not_called()


class PatchrightExecutableTests(unittest.TestCase):
    def test_patchright_profile_open_rejects_non_chrome_executable(self):
        with TemporaryDirectory() as tmp:
            agent = make_agent(tmp, patchright_profile_dir=Path(tmp) / "profile")
            agent.chrome_bin = "chromium"
            with (
                patch.object(agent.patchright_client, "_health_ok", return_value=False),
                patch("surf_agent.backends.patchright.backend.subprocess.Popen") as popen,
                self.assertRaisesRegex(SurfAgentError, "Google Chrome"),
            ):
                agent.profile_open()
        popen.assert_not_called()


def test_find_chrome_bin_finds_the_macos_app_bundle_as_one_command_word() -> None:
    bundle = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    with patch("surf_agent.runtime.shutil.which", side_effect=lambda name: name if name == bundle else None):
        found = find_chrome_bin()
    # Callers shlex-split the configured executable, so the space must survive.
    assert found is not None and shlex.split(found) == [bundle]
