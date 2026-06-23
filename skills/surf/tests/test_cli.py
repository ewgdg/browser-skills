import io
import json
import subprocess
import sys
import threading
import types
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from surf_agent.backends.camoufox.bridge import ACTIONABLE_SELECTOR, CamoufoxRuntime, PageSlot, RequestHandler, TargetFingerprint
from surf_agent.backends.patchright.bridge import PatchrightRuntime
from surf_agent.cli import (
    AgentPage,
    AxiBridgeClient,
    AxiBridgeUnavailable,
    DEFAULT_THREAD,
    SnapshotCapture,
    SurfAgent,
    SurfAgentError,
    choose_snapshot_diff,
    backend_config_file,
    default_chrome_profile_dir,
    default_state_dir,
    main,
    parse_agent_args,
    parse_axi_pages,
    parse_do_argv_steps,
    parse_do_script,
    run_do,
    strip_axi_page_list,
    surf_agent_app_url,
)


def page_state(page_id, **extra):
    payload = {"backend": "axi", "page_id": page_id}
    payload.update(extra)
    return payload


def extra_page_state(page_id, **extra):
    payload = {"backend": "axi", "page_id": page_id, "owner": "surf-agent", "token": "surf-agent:test-token"}
    payload.update(extra)
    return payload


def bridge_eval_raw(value):
    return "Script ran on page and returned:\n```json\n" + json.dumps(value) + "\n```\n"


def axi_identity_result(title="Surf Agent", href=None):
    return bridge_eval_raw({"title": title, "href": href or surf_agent_app_url()})


def snapshot_text(changes=None, *, line_count=220):
    changes = changes or {}
    lines = ["snapshot:"]
    for index in range(line_count):
        text = changes.get(index, f"stable content line {index:03d}")
        lines.append(f"uid=g{index}: {text} {'x' * 30}")
    return "\n".join(lines) + "\n"


def page_metadata_result(url="https://example.test/", title="Example"):
    return json.dumps({"title": title, "url": url})


def page_metadata_call():
    return ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,url:location.href}))"}]


def snapshot_capture(text=None, **overrides):
    url = overrides.pop("url", "https://example.test/path#section")
    return SnapshotCapture(
        text=text if text is not None else snapshot_text(),
        page_id=overrides.pop("page_id", 22),
        url=url,
        title=overrides.pop("title", "Example"),
        origin=overrides.pop("origin", "https://example.test"),
        url_without_fragment=overrides.pop("url_without_fragment", "https://example.test/path"),
    )


class FakeBridgeClient:
    def __init__(self, agent):
        self.agent = agent

    def call_tool(self, name, args=None):
        self.agent.calls.append((["bridge", name, args or {}], {}))
        response = self.agent.next_response(["bridge", name, args or {}])
        if isinstance(response, subprocess.CompletedProcess):
            if response.returncode != 0:
                raise SurfAgentError(response.stderr or response.stdout or "bridge failed")
            return response.stdout or ""
        return response


class FakeAxiAgent(SurfAgent):
    def __init__(self, responses, *args, **kwargs):
        super().__init__(axi_bin="axi", chrome_bin="chrome", command_timeout_s=1, *args, **kwargs)
        self.responses = list(responses)
        self.calls = []
        self.bridge_client = FakeBridgeClient(self)

    def next_response(self, command):
        if not self.responses:
            raise AssertionError(f"unexpected call: {command}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _ensure_dedicated_chrome_running(self):
        return None

    def _subprocess_run(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        response = self.next_response(command)
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(command, 0, stdout=response, stderr="")

    def _subprocess_popen(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return object()


class AxiBackendTests(unittest.TestCase):
    def test_constructs_without_backend_env(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.axi_bin, "npx -y chrome-devtools-axi")

    def test_default_state_dir_uses_surf_agent_data_dir(self):
        self.assertEqual(default_state_dir(), Path(__file__).resolve().parents[1] / ".surf-agent" / "state")

    def test_backend_config_commands_and_priority(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch.dict("os.environ", {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "show"]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"backend": "axi", "source": "default", "config_file": str(Path(tmp) / "config.json")})

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "camoufox"]), 0)
            self.assertEqual(json.loads((Path(tmp) / "config.json").read_text()), {"backend": "camoufox"})

            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.backend, "camoufox")

            with patch.dict("os.environ", {"SURF_AGENT_BACKEND": "axi"}, clear=True):
                self.assertEqual(SurfAgent(state_file=Path(tmp) / "thread2.json").backend, "axi")
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["backend", "show"]), 0)
                self.assertEqual(json.loads(output.getvalue())["source"], "env")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "reset"]), 0)
            self.assertFalse((Path(tmp) / "config.json").exists())
            self.assertEqual(json.loads(output.getvalue())["source"], "default")

    def test_backend_set_can_repair_invalid_config_without_constructing_agent(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch.dict("os.environ", {}, clear=True):
            (Path(tmp) / "config.json").write_text(json.dumps({"backend": "bad"}) + "\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "axi"]), 0)
            self.assertEqual(json.loads((Path(tmp) / "config.json").read_text()), {"backend": "axi"})

    def test_state_with_no_thread_does_not_create_or_query_page(self):
        with TemporaryDirectory() as tmp:
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json")
            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="thread")

        self.assertEqual(json.loads(output.getvalue()), {"backend": "axi", "open": False, "thread": "thread"})
        self.assertEqual(agent.calls, [])

    def test_axi_cli_start_embeds_dedicated_profile_env(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_CHROME_PROFILE_DIR": str(Path(tmp) / "profile")}, clear=True):
            agent = FakeAxiAgent(["ok\n"])
            self.assertEqual(agent._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertNotIn("CHROME_DEVTOOLS_AXI_AUTO_CONNECT", env)
        self.assertNotIn("CHROME_DEVTOOLS_AXI_USER_DATA_DIR", env)
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_BROWSER_URL"], "http://127.0.0.1:9336")
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_PORT"], "9335")

    def test_auto_connect_env_explicitly_overrides_dedicated_profile(self):
        with patch.dict("os.environ", {"CHROME_DEVTOOLS_AXI_AUTO_CONNECT": "1"}, clear=True):
            agent = FakeAxiAgent(["ok\n"])
            self.assertEqual(agent._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_AUTO_CONNECT"], "1")
        self.assertNotIn("CHROME_DEVTOOLS_AXI_USER_DATA_DIR", env)

    def test_axi_user_data_dir_env_overrides_default_profile_dir(self):
        with patch.dict("os.environ", {"CHROME_DEVTOOLS_AXI_USER_DATA_DIR": "/tmp/custom-surf-profile"}, clear=True):
            agent = FakeAxiAgent(["ok\n"])
            self.assertEqual(agent._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_USER_DATA_DIR"], "/tmp/custom-surf-profile")
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_BROWSER_URL"], "http://127.0.0.1:9336")
        self.assertEqual(agent.chrome_profile_dir, Path("/tmp/custom-surf-profile"))

    def test_bridge_profile_mismatch_rejects_old_auto_connect_bridge(self):
        client = AxiBridgeClient(timeout_s=1, expected_profile_dir=Path("/tmp/surf-profile"), expected_chrome_class="surf-agent")

        mismatch = client._bridge_env_mismatch({"CHROME_DEVTOOLS_AXI_AUTO_CONNECT": "1"})

        self.assertIn("explicit/user Chrome connection", mismatch)

    def test_bridge_profile_match_accepts_owned_browser_url(self):
        client = AxiBridgeClient(timeout_s=1, expected_profile_dir=Path("/tmp/surf-profile"), expected_chrome_class="surf-agent", expected_browser_url="http://127.0.0.1:9336")

        mismatch = client._bridge_env_mismatch({"CHROME_DEVTOOLS_AXI_BROWSER_URL": "http://127.0.0.1:9336"})

        self.assertIsNone(mismatch)

    def test_bridge_profile_mismatch_rejects_wrong_browser_url(self):
        client = AxiBridgeClient(timeout_s=1, expected_profile_dir=Path("/tmp/surf-profile"), expected_chrome_class="surf-agent", expected_browser_url="http://127.0.0.1:9336")

        mismatch = client._bridge_env_mismatch({"CHROME_DEVTOOLS_AXI_BROWSER_URL": "http://127.0.0.1:9222"})

        self.assertIn("expected 'http://127.0.0.1:9336'", mismatch)

    def test_profile_show_prints_dedicated_profile_config(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_CHROME_PROFILE_DIR": str(Path(tmp) / "profile")}, clear=True):
            agent = FakeAxiAgent([])
            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_profile_show()

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["profile_dir"], str(Path(tmp) / "profile"))
        self.assertEqual(payload["chrome_class"], "surf-agent")
        self.assertEqual(payload["chrome_debug_port"], 9336)
        self.assertEqual(payload["browser_url"], "http://127.0.0.1:9336")
        self.assertEqual(payload["axi_bridge_port"], 9335)

    def test_profile_open_uses_profile_without_debug_port(self):
        with TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            agent = FakeAxiAgent([""], chrome_profile_dir=profile)
            with patch.object(agent, "_chrome_debug_endpoint_ready", return_value=False):
                self.assertEqual(agent.profile_open("https://x.test"), 0)

        self.assertEqual([call[0] for call in agent.calls], [["chrome", "--class=surf-agent", f"--user-data-dir={profile}", "--new-window", "https://x.test"]])

    def test_profile_open_fails_when_automation_chrome_is_running(self):
        agent = FakeAxiAgent([])
        with patch.object(agent, "_chrome_debug_endpoint_ready", return_value=True):
            with self.assertRaisesRegex(SurfAgentError, "automated Surf Agent Chrome is running"):
                agent.profile_open()

        self.assertEqual(agent.calls, [])

    def test_camoufox_profile_open_calls_backend_with_app_id(self):
        backend_calls = []

        class FakeCamoufoxBackend:
            def profile_open(self, url, *, profile_dir, app_id):
                backend_calls.append((url, profile_dir, app_id))
                return 0

        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "camoufox", "SURF_AGENT_CAMOUFOX_APP_ID": "surf-agent-test"}, clear=True):
            profile = Path(tmp) / "camoufox-profile"
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json", camoufox_profile_dir=profile)
            agent.browser_backend = FakeCamoufoxBackend()
            self.assertEqual(agent.profile_open("https://x.test"), 0)

        self.assertEqual(backend_calls[0], ("https://x.test", str(profile), "surf-agent-test"))

    def test_profile_command_dispatch(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(SurfAgent, "_chrome_debug_endpoint_ready", return_value=False):
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["profile", "show"]), 0)
            self.assertEqual(json.loads(output.getvalue())["chrome_debug_port"], 9336)

    def test_setup_camoufox_runs_sync_set_fetch_with_current_python(self):
        responses = [
            subprocess.CompletedProcess(["ignored"], 0, stdout="synced\n", stderr=""),
            subprocess.CompletedProcess(["ignored"], 0, stdout="set\n", stderr=""),
            subprocess.CompletedProcess(["ignored"], 0, stdout="fetched\n", stderr=""),
        ]
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.subprocess.run", side_effect=responses) as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["setup", "camoufox"]), 0)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                [sys.executable, "-m", "camoufox", "sync"],
                [sys.executable, "-m", "camoufox", "set", "official/prerelease"],
                [sys.executable, "-m", "camoufox", "fetch"],
            ],
        )
        self.assertIn("running:", output.getvalue())
        self.assertIn("Camoufox setup complete.", output.getvalue())
        self.assertEqual(error.getvalue(), "")

    def test_camoufox_setup_alias_and_missing_module_hint(self):
        missing = subprocess.CompletedProcess(["ignored"], 1, stdout="", stderr="No module named camoufox\n")
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.subprocess.run", return_value=missing) as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["camoufox", "setup"]), 1)

        self.assertEqual([call.args[0] for call in run.call_args_list], [[sys.executable, "-m", "camoufox", "sync"]])
        self.assertIn("running:", output.getvalue())
        self.assertIn("uv sync --extra camoufox", error.getvalue())

    def test_backend_config_accepts_patchright_and_resolves_backend(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch.dict("os.environ", {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "patchright"]), 0)

            self.assertEqual(json.loads((Path(tmp) / "config.json").read_text()), {"backend": "patchright"})
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.backend, "patchright")
            show = io.StringIO()
            with redirect_stdout(show):
                self.assertEqual(main(["backend", "show"]), 0)
            self.assertEqual(json.loads(show.getvalue())["backend"], "patchright")

    def test_setup_patchright_runs_install_chrome_with_current_python(self):
        responses = [subprocess.CompletedProcess(["ignored"], 0, stdout="installed\n", stderr="")]
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.subprocess.run", side_effect=responses) as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["setup", "patchright"]), 0)

        self.assertEqual([call.args[0] for call in run.call_args_list], [[sys.executable, "-m", "patchright", "install", "chrome"]])
        self.assertIn("running:", output.getvalue())
        self.assertIn("Patchright setup complete.", output.getvalue())
        self.assertEqual(error.getvalue(), "")

    def test_patchright_setup_alias_and_missing_module_hint(self):
        missing = subprocess.CompletedProcess(["ignored"], 1, stdout="", stderr="No module named patchright\n")
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.subprocess.run", return_value=missing) as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["patchright", "setup"]), 1)

        self.assertEqual([call.args[0] for call in run.call_args_list], [[sys.executable, "-m", "patchright", "install", "chrome"]])
        self.assertIn("running:", output.getvalue())
        self.assertIn("uv sync --extra patchright", error.getvalue())

    def test_patchright_backend_translates_core_commands(self):
        class FakePatchrightClient:
            def __init__(self):
                self.calls = []

            def call_tool(self, name, args=None):
                self.calls.append((name, args or {}))
                return f"{name} ok\n"

        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright"}, clear=True):
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json")
            client = FakePatchrightClient()
            agent.patchright_client = client
            self.assertEqual(agent.execute_in_window(["open", "https://example.test/"]), "open ok\n")
            self.assertEqual(agent.execute_in_window(["fill", "@pr0", "hello", "world"]), "fill ok\n")
            with self.assertRaisesRegex(SurfAgentError, "scroll requires direction"):
                agent.execute_in_window(["scroll", "sideways"])

        self.assertEqual(
            client.calls,
            [("open", {"thread": "thread", "url": "https://example.test/"}), ("fill", {"thread": "thread", "uid": "@pr0", "text": "hello world"})],
        )

    def test_patchright_profile_show_prints_patchright_config(self):
        with TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "SURF_AGENT_BACKEND": "patchright",
                "SURF_AGENT_PATCHRIGHT_PROFILE_DIR": str(Path(tmp) / "patchright-profile"),
                "SURF_AGENT_PATCHRIGHT_APP_ID": "surf-agent-test",
                "SURF_AGENT_PATCHRIGHT_CLASS": "surf-agent-window",
            },
            clear=True,
        ):
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json")
            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_profile_show()

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["backend"], "patchright")
        self.assertEqual(payload["patchright_profile_dir"], str(Path(tmp) / "patchright-profile"))
        self.assertEqual(payload["patchright_app_id"], "surf-agent-test")
        self.assertEqual(payload["patchright_class"], "surf-agent-window")
        self.assertEqual(payload["patchright_bridge_port"], 9346)

    def test_patchright_profile_open_uses_patchright_profile_dir_and_class(self):
        with TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "SURF_AGENT_BACKEND": "patchright",
                "SURF_AGENT_PATCHRIGHT_APP_ID": "surf-agent-test",
                "SURF_AGENT_PATCHRIGHT_CLASS": "surf-agent-window",
            },
            clear=True,
        ):
            profile = Path(tmp) / "patchright-profile"
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json", patchright_profile_dir=profile)
            pops = []
            with patch("surf_agent.backends.patchright.backend.subprocess.Popen", side_effect=lambda *a, **kw: pops.append((a, kw)) or object()):
                with patch.object(agent.patchright_client, "_health_ok", return_value=False):
                    self.assertEqual(agent.profile_open("https://x.test"), 0)

        self.assertEqual(
            pops[0][0][0],
            ["chrome", "--class=surf-agent-window", f"--user-data-dir={profile}", "--new-window", "--name=surf-agent-test", "https://x.test"],
        )

    def test_patchright_runtime_launches_persistent_chrome_context(self):
        calls = []

        class FakeContext:
            def close(self):
                pass

        class FakePlaywright:
            def __init__(self):
                self.chromium = types.SimpleNamespace(launch_persistent_context=self.launch_persistent_context)

            def launch_persistent_context(self, **kwargs):
                calls.append(kwargs)
                return FakeContext()

        class FakeManager:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, exc_type, exc, traceback):
                pass

        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"), app_id="surf-agent-test", window_class="surf-agent-window")
        with patch("surf_agent.backends.patchright.bridge.sync_playwright", return_value=FakeManager()):
            runtime.start()

        self.assertEqual(calls[0]["user_data_dir"], "/tmp/surf-patchright-test")
        self.assertEqual(calls[0]["channel"], "chrome")
        self.assertFalse(calls[0]["headless"])
        self.assertTrue(calls[0]["no_viewport"])
        self.assertEqual(calls[0]["args"], ["--class=surf-agent-window", "--name=surf-agent-test"])

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

            def click(self):
                self.clicked = True

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

        class FakeBodyLocator:
            def inner_text(self, timeout=None):
                return "Body text"

        class FakePage:
            def __init__(self):
                self.url = "about:blank"
                self.title_value = "Example"
                self.actionable = FakeElement()
                self.keyboard = types.SimpleNamespace(type=self._type, press=self._press)

            def is_closed(self):
                return False

            def goto(self, url, wait_until=None):
                self.url = url

            def locator(self, selector):
                if selector == ACTIONABLE_SELECTOR:
                    return FakeLocatorGroup([self.actionable])
                if selector == "button":
                    return FakeLocatorGroup([self.actionable])
                if selector == "body":
                    return FakeBodyLocator()
                raise AssertionError(f"unexpected selector: {selector}")

            def aria_snapshot(self, *args, **kwargs):
                return '- button "Submit"'

            def title(self):
                return self.title_value

            def content(self):
                return "Body text"

            def evaluate(self, code):
                return {"code": code}

            def close(self):
                self.closed = True

            def bring_to_front(self):
                self.focused = True

            def _type(self, text):
                self.typed = text

            def _press(self, key):
                self.pressed = key

        class FakeContext:
            def __init__(self):
                self.pages = []

            def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"), app_id="surf-agent-test", window_class="surf-agent-window")
        runtime.browser_or_context = FakeContext()

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        snapshot = runtime.call("snapshot", {"thread": "thread"})
        self.assertIn("[ref=pr0]", snapshot)
        self.assertEqual(runtime.call("click", {"thread": "thread", "uid": "@pr0"}), "clicked\n")
        self.assertTrue(runtime.pages["thread"].page.actionable.clicked)
        self.assertEqual(runtime.call("text", {"thread": "thread"}), "Body text\n")

        state = json.loads(runtime.call("state", {"thread": "thread"}))
        self.assertEqual(state, {"backend": "patchright", "open": True, "thread": "thread", "page_id": 1, "url": "https://example.test/", "title": "Example"})
        listing = json.loads(runtime.call("list", {}))
        self.assertEqual(listing, {"backend": "patchright", "pages": [{"thread": "thread", "page_id": 1, "url": "https://example.test/", "title": "Example"}]})

    def test_patchright_bridge_client_ensure_running_spawns_bridge_module_with_profile_and_port(self):
        from surf_agent.backends.patchright.backend import PatchrightBridgeClient

        profile_dir = Path("/tmp/surf-patchright-profile")
        client = PatchrightBridgeClient(timeout_s=1.0, port=9555, profile_dir=profile_dir)
        pops = []

        with (
            patch.object(client, "_health_ok", side_effect=[False, True]),
            patch("surf_agent.backends.patchright.backend.subprocess.Popen", side_effect=lambda *a, **kw: pops.append((a, kw)) or object()),
            patch("surf_agent.backends.patchright.backend.time.monotonic", side_effect=[0.0, 0.0, 0.1]),
            patch("surf_agent.backends.patchright.backend.time.sleep", return_value=None),
        ):
            client._ensure_running()

        command = pops[0][0][0]
        self.assertEqual(command[:6], [sys.executable, "-m", "surf_agent.backends.patchright.bridge", "--port", "9555", "--profile-dir"])
        self.assertEqual(command[6], str(profile_dir))

    def test_bridge_unavailable_starts_once_then_uses_http(self):
        agent = FakeAxiAgent([AxiBridgeUnavailable("down"), "started\n", "## Pages\n1: Example (https://example.test/)\n"])

        self.assertEqual(agent._run_axi_text(["pages"]), "## Pages\n1: Example (https://example.test/)\n")
        commands = [call[0] for call in agent.calls]
        self.assertEqual(commands, [["bridge", "list_pages", {}], ["axi", "start"], ["bridge", "list_pages", {}]])

    def test_open_creates_and_saves_axi_page_state(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            agent = FakeAxiAgent(
                [
                    "24 Existing https://existing.test/\n",
                    "",
                    "24,Existing,false\n22,Surf Agent,false\n",
                    "selected\n",
                    axi_identity_result(),
                    "selected\n",
                    'Successfully navigated to https://example.test/.\n## Pages\n22: Example (https://example.test/) [selected]\n',
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["open", "https://example.test/"]), 0)

            state = json.loads(state_file.read_text())
            self.assertEqual(state["backend"], "axi")
            self.assertEqual(state["page_id"], 22)
            self.assertEqual(state["url"], "https://example.test/")
            self.assertEqual(state["title"], "Example")
            self.assertNotIn("owner", state)
            self.assertNotIn("token", state)
            commands = [call[0] for call in agent.calls]
            self.assertEqual(commands[0], ["bridge", "list_pages", {}])
            self.assertEqual(commands[1][0], "chrome")
            self.assertEqual(commands[1][1], "--class=surf-agent")
            self.assertEqual(commands[1][2], f"--user-data-dir={default_chrome_profile_dir()}")
            self.assertEqual(commands[1][3], "--new-window")
            self.assertEqual(commands[1][4], "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent")
            self.assertEqual(commands[2:], [["bridge", "list_pages", {}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "navigate_page", {"type": "url", "url": "https://example.test/"}]])
            self.assertEqual(output.getvalue(), "Successfully navigated to https://example.test/.\n")
            self.assertFalse(any(call[0][0] == "axi" for call in agent.calls))

    def test_new_command_opens_welcome_after_short_app_bootstrap(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            agent = FakeAxiAgent(
                [
                    "",
                    "",
                    "22,Surf Agent,false\n",
                    "selected\n",
                    axi_identity_result(),
                    "selected\n",
                    "opened welcome\n",
                    "22,Surf Agent,false\n",
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_page_id(force_new=True)

            self.assertEqual(output.getvalue(), "22\n")
            commands = [call[0] for call in agent.calls]
            self.assertEqual(commands[1], ["chrome", "--class=surf-agent", f"--user-data-dir={default_chrome_profile_dir()}", "--new-window", "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent"])
            self.assertEqual(commands[2:6], [["bridge", "list_pages", {}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}], ["bridge", "select_page", {"pageId": 22}]])
            self.assertEqual(commands[6][0:2], ["bridge", "navigate_page"])
            self.assertIn("open%20%26lt%3Burl%26gt%3B", commands[6][2]["url"])

    def test_state_without_axi_backend_is_ignored_and_not_closed(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps({"page_id": 22}))
            agent = FakeAxiAgent([], state_file=state_file)

            self.assertEqual(agent.close(), 0)
            self.assertEqual(agent.calls, [])

    def test_new_window_must_have_surf_agent_title_before_adoption(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            agent = FakeAxiAgent(
                [
                    "",
                    "",
                    "22,User Page,false\n",
                    "selected\n",
                    axi_identity_result(title="My Surf Agent Notes"),
                ],
                state_file=state_file,
            )

            with self.assertRaisesRegex(Exception, "could not find new browser page titled"):
                agent.run_in_window(["open", "https://example.test/"])

            self.assertFalse(state_file.exists())
            commands = [call[0] for call in agent.calls]
            self.assertEqual(commands[0], ["bridge", "list_pages", {}])
            self.assertEqual(commands[1][0], "chrome")
            self.assertEqual(commands[1][1], "--class=surf-agent")
            self.assertEqual(commands[1][2], f"--user-data-dir={default_chrome_profile_dir()}")
            self.assertEqual(commands[1][3], "--new-window")
            self.assertEqual(commands[1][4], "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent")
            self.assertEqual(commands[2:], [["bridge", "list_pages", {}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}]])

    def test_existing_open_selects_remembered_page_before_navigation(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(extra_page_state(22, url="https://old.test/")))
            agent = FakeAxiAgent(
                [
                    "selected\n",
                    'Successfully navigated to https://example.test/.\n## Pages\n22: Example (https://example.test/) [selected]\n',
                ],
                state_file=state_file,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["open", "https://example.test/"]), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "navigate_page", {"type": "url", "url": "https://example.test/"}]])
            saved = json.loads(state_file.read_text())
            self.assertEqual(saved["page_id"], 22)
            self.assertNotIn("owner", saved)
            self.assertNotIn("token", saved)

    def test_existing_command_selects_page_then_runs_mapped_eval(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/")))
            agent = FakeAxiAgent(
                [
                    "selected\n",
                    "1\n",
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["eval", "1"]), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (1)"}]])
            self.assertEqual(output.getvalue(), "result: 1\n")

    def test_select_page_failure_stops_before_action(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            select_failed = subprocess.CompletedProcess(["axi", "selectpage", "22"], 1, stdout="", stderr="bad page")
            agent = FakeAxiAgent([select_failed], state_file=state_file)

            with self.assertRaisesRegex(Exception, "bad page"):
                agent.run_in_window(["eval", "1"])

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}]])

    def test_focus_selects_page_and_brings_to_front(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", "focused\n"], state_file=state_file)

            self.assertEqual(agent.focus(), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "select_page", {"pageId": 22, "bringToFront": True}]])

    def test_close_closes_page_and_never_stops_bridge(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["closed\n"], state_file=state_file)

            self.assertEqual(agent.close(), 0)

        commands = [call[0] for call in agent.calls]
        self.assertEqual(commands, [["bridge", "close_page", {"pageId": 22}]])
        self.assertNotIn(["axi", "stop"], commands)
        self.assertFalse(state_file.exists())

    def test_stale_page_state_is_cleared_without_creating_page(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            failed_select = subprocess.CompletedProcess(["selectpage", "22"], 1, stdout="", stderr="bad page")
            agent = FakeAxiAgent([failed_select], state_file=state_file)
            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="thread")

            self.assertEqual(json.loads(output.getvalue()), {"backend": "axi", "open": False, "thread": "thread"})
            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}]])
            self.assertFalse(state_file.exists())

    def test_close_keeps_state_when_close_page_fails(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            failed_close = subprocess.CompletedProcess(["axi", "closepage", "22"], 1, stdout="", stderr="bridge unavailable")
            agent = FakeAxiAgent([failed_close], state_file=state_file)

            self.assertEqual(agent.close(), 1)

            self.assertTrue(state_file.exists())
            self.assertEqual([call[0] for call in agent.calls], [["bridge", "close_page", {"pageId": 22}]])

    def test_close_matching_closes_only_matching_remembered_axi_pages(self):
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "agent-a-1.json").write_text(json.dumps(page_state(101)))
            (state_dir / "agent-a-2.json").write_text(json.dumps(page_state(102)))
            (state_dir / "agent-b-1.json").write_text(json.dumps(page_state(201)))
            (state_dir / "agent-a-stale.json").write_text(json.dumps(page_state(999)))
            agent = FakeAxiAgent(
                ["closed\n", "closed\n", "closed\n"],
                state_file=state_dir / "unused.json",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = agent.close_matching("agent-a-*")

            payload = json.loads(output.getvalue())
            commands = [call[0] for call in agent.calls]
            self.assertEqual(exit_code, 0)
            self.assertEqual(commands, [["bridge", "close_page", {"pageId": 101}], ["bridge", "close_page", {"pageId": 102}], ["bridge", "close_page", {"pageId": 999}]])
            self.assertNotIn(["axi", "stop"], commands)
            self.assertEqual(payload["stale"], [])
            self.assertEqual(payload["closed"], [{"thread": "agent-a-1", "page_id": 101}, {"thread": "agent-a-2", "page_id": 102}, {"thread": "agent-a-stale", "page_id": 999}])
            self.assertFalse((state_dir / "agent-a-1.json").exists())
            self.assertFalse((state_dir / "agent-a-2.json").exists())
            self.assertTrue((state_dir / "agent-b-1.json").exists())
            self.assertFalse((state_dir / "agent-a-stale.json").exists())

    def test_timeout_raises_clear_axi_error(self):
        agent = FakeAxiAgent([subprocess.TimeoutExpired(["axi", "eval", "1"], 1)])
        with self.assertRaisesRegex(Exception, "browser command timed out after 1s: eval 1.*browser bridge"):
            agent._run_axi_cli_text(["eval", "1"])

    def test_bridge_stop_is_explicit_only(self):
        agent = FakeAxiAgent(["stopped\n"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(agent.bridge_stop(), 0)
        self.assertEqual([call[0] for call in agent.calls], [["axi", "stop"]])
        self.assertEqual(output.getvalue(), "stopped\n")

    def test_parse_axi_pages_accepts_json_human_lines_and_empty_message(self):
        self.assertEqual(parse_axi_pages('{"pages":[{"id":7,"url":"https://x.test/","title":"X"}]}'), [AgentPage(7, "https://x.test/", "X")])
        self.assertEqual(parse_axi_pages("* [8] Title https://y.test/\n"), [AgentPage(8, "https://y.test/", "Title")])
        self.assertEqual(parse_axi_pages("No pages open\n"), [])

    def test_parse_axi_pages_accepts_cli_csv_and_empty_header(self):
        output = "pages[2]{id,url,selected}:\n1,https://x.test/,false\n2,about:blank,true\nhelp[selectpage]...\n"
        self.assertEqual(parse_axi_pages(output), [AgentPage(1, "https://x.test/"), AgentPage(2, "about:blank")])
        self.assertEqual(parse_axi_pages("pages[0]{id,url,selected}:\n"), [])

    def test_parse_axi_pages_accepts_mcp_markdown(self):
        output = "## Pages\n1: Example Domain (https://example.test/) [selected]\n2: Surf Agent (data:text/html,%3Ctitle%3ESurf%20Agent)\n"
        self.assertEqual(parse_axi_pages(output), [AgentPage(1, "https://example.test/", "Example Domain"), AgentPage(2, "data:text/html,%3Ctitle%3ESurf%20Agent", "Surf Agent")])

    def test_navigation_output_strips_axi_page_list(self):
        output = "Successfully navigated to https://example.test/.\n## Pages\n22: Example (https://example.test/) [selected]\n"
        self.assertEqual(strip_axi_page_list(output), "Successfully navigated to https://example.test/.\n")

    def test_navigation_output_strips_axi_csv_page_list(self):
        output = "opened https://example.test/\npages[1]{id,url,selected}:\n22,https://example.test/,true\n"
        self.assertEqual(strip_axi_page_list(output), "opened https://example.test/\n")

    def test_extract_page_id_ignores_snapshot_uids(self):
        from surf_agent.cli import extract_page_id

        self.assertIsNone(extract_page_id('snapshot:\nuid=g24:3_0 RootWebArea "Example Domain"\n'))
        self.assertEqual(extract_page_id("pageId: 39\n"), 39)

    def test_unsupported_axi_command_fails_clearly(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["22 Example https://example.test/\n", "selected\n"], state_file=state_file)
            with self.assertRaisesRegex(Exception, "unsupported browser command: forward"):
                agent.run_in_window(["forward"])

    def test_removed_alias_commands_are_rejected(self):
        removed_commands = [
            "g" + "o",
            "read",
            "page" + ".read",
            "page" + ".text",
            "page" + ".state",
            "j" + "s",
            "key",
            "forget",
        ]
        for command in removed_commands:
            with self.subTest(command=command):
                output = io.StringIO()
                error = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    exit_code = main([command])

                self.assertEqual(exit_code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn(f"unsupported browser command: {command}", error.getvalue())

    def test_removed_thread_id_option_forms_are_rejected(self):
        flag = "--thread" + "-id"
        for argv in ([flag, "custom", "state"], [flag + "=custom", "state"]):
            with self.subTest(argv=argv):
                config, rest = parse_agent_args(argv)

                self.assertEqual(config.thread, DEFAULT_THREAD)
                self.assertEqual(rest, argv)

    def test_do_stdin_prints_only_final_step_by_default(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", "clicked\n", "selected\n", "snapshot:\nuid=g2:1 button Submit\n"], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("click @g1:1\nsnapshot\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "snapshot:\nuid=g2:1 button Submit\n")
        self.assertEqual(error.getvalue(), "")
        self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "click", {"uid": "g1:1"}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "take_snapshot", {}]])

    def test_do_snapshot_baseline_emits_nothing_and_keeps_state_file_clean(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/", title="Example")))
            original_state = state_file.read_text()
            agent = FakeAxiAgent(["selected\n", snapshot_text(), page_metadata_result()], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --baseline\n"), stdout=output, stderr=error)

            self.assertEqual(exit_code, 0)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(error.getvalue(), "")
            self.assertEqual(state_file.read_text(), original_state)
            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "take_snapshot", {}], page_metadata_call()])

    def test_do_snapshot_diff_emits_useful_unified_diff_and_updates_baseline(self):
        before = snapshot_text({20: "first old"})
        after_first = snapshot_text({20: "first new"})
        after_second = snapshot_text({20: "first new", 60: "second new"})
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/", title="Example")))
            agent = FakeAxiAgent(["selected\n", before, page_metadata_result(), "selected\n", after_first, page_metadata_result(), "selected\n", after_second, page_metadata_result()], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --baseline\nsnapshot --diff --emit\nsnapshot --diff\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("--- baseline", text)
        self.assertIn("+++ current", text)
        self.assertIn("@@", text)
        self.assertNotIn("stable content line 180", text)
        self.assertIn('~~~surf-step index=2 command="snapshot --diff"', text)
        sections = text.split('~~~surf-step index=3 command="snapshot --diff"')
        self.assertEqual(len(sections), 2)
        self.assertIn("first old", sections[0])
        self.assertNotIn("first old", sections[1])
        self.assertIn("second new", sections[1])
        self.assertEqual(error.getvalue(), "")

    def test_do_snapshot_diff_without_baseline_outputs_full_once_then_updates_baseline(self):
        first = snapshot_text({30: "first snapshot"})
        second = snapshot_text({30: "second snapshot"})
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/", title="Example")))
            agent = FakeAxiAgent(["selected\n", first, page_metadata_result(), "selected\n", second, page_metadata_result()], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --diff --emit\nsnapshot --diff\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("# snapshot fallback: no baseline", text)
        self.assertIn("stable content line 180", text)
        step2 = text.split('~~~surf-step index=2 command="snapshot --diff"')[1]
        self.assertIn("--- baseline", step2)
        self.assertNotIn("stable content line 180", step2)
        self.assertEqual(error.getvalue(), "")

    def test_snapshot_diff_gates_fall_back_for_large_small_savings_and_many_hunks(self):
        cases = [
            (snapshot_text({i: f"old {i}" for i in range(120)}, line_count=120), snapshot_text({i: f"new {i}" for i in range(120)}, line_count=120), "diff too large"),
            ("snapshot:\n" + "\n".join(f"L{i}" for i in range(80)) + "\n", "snapshot:\n" + "\n".join("CHANGED" if i == 40 else f"L{i}" for i in range(80)) + "\n", "saved chars < 250"),
            (snapshot_text(line_count=260), snapshot_text({i * 20: f"change {i}" for i in range(9)}, line_count=260), "hunks > 8"),
        ]
        for before_text, after_text, reason in cases:
            with self.subTest(reason=reason):
                decision = choose_snapshot_diff(snapshot_capture(before_text), snapshot_capture(after_text))

                self.assertFalse(decision.used_diff)
                self.assertIn(f"# snapshot fallback: {reason}", decision.output)
                self.assertIn("snapshot:", decision.output)

    def test_snapshot_diff_no_changes_emits_compact_header(self):
        capture = snapshot_capture(snapshot_text())
        decision = choose_snapshot_diff(capture, capture)

        self.assertTrue(decision.used_diff)
        self.assertEqual(decision.output, "# snapshot-diff: no changes\n")

    def test_snapshot_diff_metadata_vetoes_only_identity_changes(self):
        before = snapshot_capture(snapshot_text({20: "old"}), url="https://example.test/path#old")
        useful_after = snapshot_text({20: "new"})

        origin_change = choose_snapshot_diff(before, snapshot_capture(useful_after, url="https://other.test/path#old", origin="https://other.test", url_without_fragment="https://other.test/path"))
        self.assertFalse(origin_change.used_diff)
        self.assertIn("origin changed", origin_change.output)

        page_change = choose_snapshot_diff(before, snapshot_capture(useful_after, page_id=23))
        self.assertFalse(page_change.used_diff)
        self.assertIn("page changed", page_change.output)

        hash_only = choose_snapshot_diff(before, snapshot_capture(useful_after, url="https://example.test/path#new", url_without_fragment="https://example.test/path"))
        self.assertTrue(hash_only.used_diff)

        path_and_title = choose_snapshot_diff(before, snapshot_capture(useful_after, url="https://example.test/other", url_without_fragment="https://example.test/other", title="Other"))
        self.assertTrue(path_and_title.used_diff)

    def test_do_snapshot_diff_uses_fallback_metadata_when_auxiliary_eval_fails(self):
        before = snapshot_text({20: "old"})
        after = snapshot_text({20: "new"})
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/path", title="Example")))
            agent = FakeAxiAgent(
                [
                    "selected\n",
                    before,
                    SurfAgentError("metadata unavailable"),
                    "selected\n",
                    after,
                    SurfAgentError("metadata unavailable"),
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --baseline\nsnapshot --diff\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        self.assertIn("--- baseline", output.getvalue())
        self.assertEqual(error.getvalue(), "")

    def test_do_snapshot_diff_origin_gate_uses_live_page_url_without_persisting_state(self):
        before = snapshot_text({20: "old"})
        after = snapshot_text({20: "new"})
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/path", title="Example")))
            original_state = state_file.read_text()
            agent = FakeAxiAgent(
                [
                    "selected\n",
                    before,
                    page_metadata_result(url="https://example.test/path"),
                    "selected\n",
                    after,
                    page_metadata_result(url="https://other.test/path"),
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --baseline\nsnapshot --diff\n"), stdout=output, stderr=error)
            final_state = state_file.read_text()

        self.assertEqual(exit_code, 0)
        self.assertIn("# snapshot fallback: origin changed", output.getvalue())
        self.assertIn("stable content line 180", output.getvalue())
        self.assertNotIn("--- baseline", output.getvalue())
        self.assertEqual(final_state, original_state)
        self.assertEqual(error.getvalue(), "")

    def test_standalone_snapshot_diff_flags(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/", title="Example")))
            agent = FakeAxiAgent(["selected\n", snapshot_text(), page_metadata_result()], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()
            with patch("surf_agent.cli.SurfAgent", return_value=agent), redirect_stdout(output), redirect_stderr(error):
                exit_code = main(["snapshot", "--diff"])

        self.assertEqual(exit_code, 0)
        self.assertIn("# snapshot fallback: no baseline", output.getvalue())
        self.assertEqual(error.getvalue(), "")

        for argv in (["snapshot", "--baseline"], ["snapshot", "--baseline", "--diff"], ["snapshot", "--diff", "extra"]):
            with self.subTest(argv=argv):
                agent = FakeAxiAgent([])
                output = io.StringIO()
                error = io.StringIO()
                with patch("surf_agent.cli.SurfAgent", return_value=agent), redirect_stdout(output), redirect_stderr(error):
                    exit_code = main(list(argv))

                self.assertEqual(exit_code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("surf-agent:", error.getvalue())

    def test_do_plain_multi_output_uses_fence_longer_than_output_tilde_runs(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", bridge_eval_raw("a~~~b"), "selected\n", bridge_eval_raw(2)], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("eval 'a~~~b' --emit\neval 2\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn('~~~~surf-step index=1 command="eval', text)
        self.assertIn("a~~~b", text)
        self.assertEqual(error.getvalue(), "")

    def test_do_jsonl_uses_status_key_and_emits_requested_steps(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", "1\n", "selected\n", "2\n"], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=["--jsonl"], stdin=io.StringIO("eval 1 --emit\neval 2\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([record["status"] for record in records], ["success", "success"])
        self.assertEqual([record["command"] for record in records], ["eval", "eval"])
        self.assertNotIn("ok", records[0])
        self.assertEqual(error.getvalue(), "")

    def test_do_rejects_unknown_commands(self):
        agent = FakeAxiAgent([])
        output = io.StringIO()
        error = io.StringIO()

        exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("unknown https://example.test/\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("unsupported browser command: unknown", error.getvalue())

    def test_do_stdin_allows_literal_separator_tokens(self):
        steps = parse_do_script('type "::"\neval "location.href.includes(\'||\')"\n')

        self.assertEqual([step.args for step in steps], [["type", "::"], ["eval", "location.href.includes('||')"]])

    def test_do_script_keeps_url_fragments_and_literal_hashes(self):
        steps = parse_do_script("# full-line comment\nopen https://example.test/path#section\ntype literal#hash\n")

        self.assertEqual([step.args for step in steps], [["open", "https://example.test/path#section"], ["type", "literal#hash"]])

    def test_do_step_double_dash_makes_emit_and_quiet_literal_args(self):
        steps = parse_do_script("type -- --emit --quiet\n")

        self.assertEqual(steps[0].args, ["type", "--emit", "--quiet"])
        self.assertFalse(steps[0].emit)
        self.assertFalse(steps[0].quiet)

    def test_do_argv_double_dash_is_step_local(self):
        steps = parse_do_argv_steps(["type", "--", "--emit", "::", "snapshot"])

        self.assertEqual([step.args for step in steps], [["type", "--emit"], ["snapshot"]])
        self.assertFalse(steps[0].emit)
        self.assertFalse(steps[0].quiet)

    def test_do_stops_after_failed_step(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", "clicked\n", "selected\n", SurfAgentError("bad click", exit_code=1)], state_file=state_file)
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("click @g1:1\nclick @g1:2\nsnapshot\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("step 2 `click @g1:2` failed: bad click", error.getvalue())
        self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "click", {"uid": "g1:1"}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "click", {"uid": "g1:2"}]])

    def test_run_do_defaults_use_live_standard_streams(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            agent = FakeAxiAgent([], state_file=state_file)
            output = io.StringIO()
            with patch("sys.stdin", io.StringIO("state\n")), redirect_stdout(output):
                exit_code = run_do(agent, thread="thread", argv=[])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"backend": "axi", "open": False, "thread": "thread"})

    def test_text_outputs_raw_body_text_without_result_wrapper(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", bridge_eval_raw("Hello body")], state_file=state_file)
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = agent.run_in_window(["text"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "Hello body\n")
        self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (document.body.innerText)"}]])

    def test_camoufox_backend_translates_core_commands(self):
        class FakeCamoufoxClient:
            def __init__(self):
                self.calls = []

            def call_tool(self, name, args=None):
                self.calls.append((name, args or {}))
                return f"{name} ok\n"

        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "camoufox"}, clear=True):
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            client = FakeCamoufoxClient()
            agent.camoufox_client = client
            self.assertEqual(agent.execute_in_window(["open", "https://example.test/"]), "open ok\n")
            self.assertEqual(agent.execute_in_window(["fill", "@cf0", "hello", "world"]), "fill ok\n")
            with self.assertRaisesRegex(SurfAgentError, "scroll requires direction"):
                agent.execute_in_window(["scroll", "sideways"])

        self.assertEqual(client.calls, [("open", {"thread": "thread", "url": "https://example.test/"}), ("fill", {"thread": "thread", "uid": "@cf0", "text": "hello world"})])

    def test_camoufox_snapshot_diff_does_not_require_axi_state(self):
        class FakeCamoufoxClient:
            def __init__(self, responses):
                self.responses = list(responses)
                self.calls = []

            def call_tool(self, name, args=None):
                self.calls.append((name, args or {}))
                if not self.responses:
                    raise AssertionError(f"unexpected camoufox call: {name}")
                return self.responses.pop(0)

        before = snapshot_text({20: "camoufox old"})
        after = snapshot_text({20: "camoufox new"})
        state = json.dumps({"backend": "camoufox", "open": True, "thread": "thread", "page_id": 7, "url": "https://example.test/", "title": "Example"}) + "\n"
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "camoufox"}, clear=True):
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            client = FakeCamoufoxClient([before, state, after, state])
            agent.camoufox_client = client
            output = io.StringIO()
            error = io.StringIO()

            exit_code = run_do(agent, thread="thread", argv=[], stdin=io.StringIO("snapshot --baseline\nsnapshot --diff\n"), stdout=output, stderr=error)

        self.assertEqual(exit_code, 0)
        self.assertIn("--- baseline", output.getvalue())
        self.assertIn("camoufox old", output.getvalue())
        self.assertIn("camoufox new", output.getvalue())
        self.assertEqual(error.getvalue(), "")
        self.assertEqual([name for name, _args in client.calls], ["snapshot", "state", "snapshot", "state"])

    def test_camoufox_refs_verify_fingerprint_and_allow_selector_fallback(self):
        class FakeElement:
            def __init__(self, tag="button", text="Submit", role="", css_path="button:nth-of-type(1)"):
                self.tag = tag
                self.text = text
                self.role = role
                self.css_path = css_path
                self.clicked = False

            def is_visible(self, timeout=None):
                return True

            def evaluate(self, script):
                return self.tag if "tagName.toLowerCase" in script else self.css_path

            def get_attribute(self, name):
                return self.role if name == "role" else ""

            def inner_text(self, timeout=None):
                return self.text

            def input_value(self, timeout=None):
                return ""

            def bounding_box(self):
                return {"x": 1, "y": 2, "width": 3, "height": 4}

            def click(self):
                self.clicked = True

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

        class FakePage:
            url = "https://example.test/"

            def __init__(self, actionables, selectors):
                self.actionables = actionables
                self.selectors = selectors

            def locator(self, selector):
                if selector == ACTIONABLE_SELECTOR:
                    return FakeLocatorGroup(self.actionables)
                return FakeLocatorGroup(self.selectors.get(selector, []))

            def aria_snapshot(self, timeout=None):
                return '- button "Submit"'

            def title(self):
                return "Example"

        button = FakeElement(text="Submit", role="button")
        page = FakePage([button], {button.css_path: [button], "button.submit": [button]})
        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        slot = PageSlot(page=page, page_token=1)

        snapshot = runtime._snapshot(slot)
        self.assertIn('[ref=cf0]', snapshot)
        runtime._target_locator(slot, "button.submit").click()
        self.assertTrue(button.clicked)

        replacement = FakeElement(text="Delete", role="button", css_path=button.css_path)
        page.actionables = [replacement]
        page.selectors = {button.css_path: [replacement], "button.submit": [replacement]}
        with self.assertRaisesRegex(RuntimeError, "Capture a new snapshot"):
            runtime._target_locator(slot, "@cf0")

    def test_camoufox_fingerprint_matching_requires_label_when_available(self):
        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        expected = TargetFingerprint(tag="button", role="button", name="Submit", text="Submit")

        self.assertTrue(runtime._fingerprint_matches(expected, TargetFingerprint(tag="button", role="button", name="Submit", text="Submit")))
        self.assertFalse(runtime._fingerprint_matches(expected, TargetFingerprint(tag="button", role="button", name="Delete", text="Delete")))
        self.assertTrue(runtime._fingerprint_matches(TargetFingerprint(tag="button", role="button"), TargetFingerprint(tag="button", role="button")))
        self.assertFalse(runtime._fingerprint_matches(TargetFingerprint(tag="button", role="button"), TargetFingerprint(tag="button", role="link")))

    def test_camoufox_backend_profile_open_rejects_running_bridge(self):
        from surf_agent.backends import CamoufoxBackend

        class FakeClient:
            def _health_ok(self):
                return True

        agent = FakeAxiAgent([], state_file=Path("/tmp/thread.json"))
        agent.camoufox_client = FakeClient()
        backend = CamoufoxBackend(agent, client=FakeClient(), welcome_url=lambda: "about:blank")
        with self.assertRaisesRegex(SurfAgentError, "Camoufox bridge is running"):
            backend.profile_open("https://x.test", profile_dir="/tmp", app_id="test")

    def test_camoufox_backend_profile_open_launches_camoufox_subprocess(self):
        from surf_agent.backends.camoufox.backend import CamoufoxBackend, _camoufox_binary_path

        class FakeClient:
            def _health_ok(self):
                return False

        pops = []
        fake_bin = "/usr/bin/camoufox-bin"

        agent = FakeAxiAgent([], state_file=Path("/tmp/thread.json"))
        agent.camoufox_client = FakeClient()
        backend = CamoufoxBackend(agent, client=FakeClient(), welcome_url=lambda: "about:blank")
        with (
            patch("subprocess.Popen", side_effect=lambda *a, **kw: pops.append((a, kw)) or object()),
            patch("surf_agent.backends.camoufox.backend._camoufox_binary_path", return_value=fake_bin),
        ):
            self.assertEqual(backend.profile_open("https://x.test", profile_dir="/tmp/p", app_id="test"), 0)

        args = pops[0][0][0]
        self.assertEqual(args[0], fake_bin)
        self.assertEqual(args[1:], ["-profile", str(Path("/tmp/p")), "--class=test", "--name", "test", "https://x.test"])
        self.assertTrue(pops[0][1]["start_new_session"])

    def test_camoufox_runtime_passes_app_id_as_window_class(self):
        calls = []

        class FakeCamoufox:
            def __init__(self, **kwargs):
                calls.append(kwargs)

            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, traceback):
                pass

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"), app_id="surf-agent-test")
        fake_module = types.SimpleNamespace(Camoufox=FakeCamoufox)

        with patch.dict(sys.modules, {"camoufox": types.SimpleNamespace(sync_api=fake_module), "camoufox.sync_api": fake_module}):
            runtime.start()

        self.assertEqual(calls[0]["args"], ["--class=surf-agent-test", "--name", "surf-agent-test"])
        self.assertNotIn("no_viewport", calls[0])
        self.assertNotIn("env", calls[0])

    def test_camoufox_close_does_not_start_runtime(self):
        class FakePage:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        with patch.object(runtime, "start", side_effect=AssertionError("should not start")):
            self.assertEqual(runtime.call("close", {"thread": "missing"}), "closed\n")
            page = FakePage()
            runtime.pages["thread"] = PageSlot(page=page, page_token=1)
            self.assertEqual(runtime.call("close", {"thread": "thread"}), "closed\n")

        self.assertTrue(page.closed)
        self.assertNotIn("thread", runtime.pages)

    def test_camoufox_open_reuses_initial_blank_context_page(self):
        class BlankPage:
            def __init__(self):
                self.url = "about:blank"

            def is_closed(self):
                return False

            def goto(self, url, wait_until=None):
                self.url = url

        class ContextWithBlankPage:
            def __init__(self):
                self.pages = [BlankPage()]

            def new_page(self):
                raise AssertionError("should reuse initial blank page")

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        runtime.browser_or_context = ContextWithBlankPage()

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        self.assertIs(runtime.pages["thread"].page, runtime.browser_or_context.pages[0])
        self.assertEqual(len(runtime.browser_or_context.pages), 1)

    def test_camoufox_open_adopts_one_unowned_page_and_closes_restored_extras(self):
        class RestoredPage:
            def __init__(self, url):
                self.url = url
                self.closed = False

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

            def goto(self, url, wait_until=None):
                self.url = url

        class ContextWithRestoredPages:
            def __init__(self):
                self.pages = [RestoredPage("https://restore-a.test/"), RestoredPage("https://restore-b.test/")]

            def new_page(self):
                raise AssertionError("should reuse restored page")

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        runtime.browser_or_context = ContextWithRestoredPages()

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        self.assertIs(runtime.pages["thread"].page, runtime.browser_or_context.pages[0])
        self.assertFalse(runtime.browser_or_context.pages[0].closed)
        self.assertTrue(runtime.browser_or_context.pages[1].closed)

    def test_camoufox_runtime_restarts_context_after_manual_window_close(self):
        class ClosedPage:
            def is_closed(self):
                return True

        class ClosedContext:
            pages = []

            def new_page(self):
                raise RuntimeError("BrowserContext.new_page: Target page, context or browser has been closed")

        class OpenContext:
            def __init__(self):
                self.pages = []

            def new_page(self):
                page = ClosedPage()
                self.pages.append(page)
                return page

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        runtime.browser_or_context = ClosedContext()
        runtime.pages["thread"] = PageSlot(page=ClosedPage(), page_token=1)
        open_context = OpenContext()

        with patch.object(runtime, "start", side_effect=lambda: setattr(runtime, "browser_or_context", open_context)) as start:
            slot = runtime._new_page("thread")

        self.assertEqual(slot.page_token, 1)
        self.assertIs(slot.page, open_context.pages[0])
        self.assertEqual(start.call_count, 1)

    def test_camoufox_open_recreates_page_when_goto_finds_closed_target(self):
        class DeadPage:
            url = "about:blank"

            def is_closed(self):
                return False

            def close(self):
                pass

            def goto(self, url, wait_until=None):
                raise RuntimeError("Page.goto: Target page, context or browser has been closed")

        class OpenPage:
            def __init__(self):
                self.url = "about:blank"

            def is_closed(self):
                return False

            def goto(self, url, wait_until=None):
                self.url = url

        class OpenContext:
            pages = []

            def new_page(self):
                page = OpenPage()
                self.pages.append(page)
                return page

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        runtime.browser_or_context = OpenContext()
        runtime.pages["thread"] = PageSlot(page=DeadPage(), page_token=1)

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        self.assertIs(runtime.pages["thread"].page, runtime.browser_or_context.pages[0])

    def test_camoufox_runtime_rejects_invalid_scroll_direction(self):
        class FakePage:
            def is_closed(self):
                return False

        runtime = CamoufoxRuntime(profile_dir=Path("/tmp/surf-camoufox-test"))
        runtime.pages["thread"] = PageSlot(page=FakePage(), page_token=1)

        with self.assertRaisesRegex(RuntimeError, "scroll requires direction"):
            runtime.call("scroll", {"thread": "thread", "direction": "sideways"})

    def test_camoufox_stop_request_shuts_down_http_server(self):
        with TemporaryDirectory() as tmp:
            server = HTTPServer(("127.0.0.1", 0), RequestHandler)
            RequestHandler.runtime = CamoufoxRuntime(profile_dir=Path(tmp) / "profile")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/call"
            payload = json.dumps({"name": "stop", "args": {}}).encode()
            request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

            with urllib.request.urlopen(request, timeout=2) as response:
                body = json.loads(response.read().decode())
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(body, {"result": "stopped\n"})
        self.assertFalse(thread.is_alive())

    def test_close_matching_requires_pattern(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(["close-matching"])

        self.assertEqual(exit_code, 2)

    def test_window_id_command_is_removed(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(["window-id"])

        self.assertEqual(exit_code, 2)
        self.assertIn("unsupported browser command: window-id", error.getvalue())


if __name__ == "__main__":
    unittest.main()
