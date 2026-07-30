import io
import json
import os
import shutil
import subprocess
import tempfile
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import surf_agent.backends.patchright.bridge as patchright_bridge
from surf_agent.backends.patchright.bridge import PageSlot, PatchrightRuntime
from surf_agent.backends.axi import AxiBackend
from surf_agent.errors import BridgeUnavailable
from surf_agent.cli import (
    APP_DIRS,
    AgentPage,
    ScreenshotOptions,
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
    surf_agent_config_dir,
    surf_agent_data_dir,
    surf_agent_state_dir,
    main,
    skill_data_dir,
    map_axi_cli_args_to_bridge,
    parse_agent_args,
    parse_eval_code,
    parse_screenshot_output,
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
        self._surf_agent_home_tmp = None
        if "SURF_AGENT_HOME" not in os.environ:
            self._surf_agent_home_tmp = tempfile.mkdtemp(prefix="surf-agent-test-home-")
            with patch.dict("os.environ", {"SURF_AGENT_HOME": self._surf_agent_home_tmp}, clear=False):
                super().__init__(axi_bin="axi", chrome_bin="chrome", command_timeout_s=1, *args, **kwargs)
        else:
            super().__init__(axi_bin="axi", chrome_bin="chrome", command_timeout_s=1, *args, **kwargs)
        self.responses = list(responses)
        self.calls = []
        self.bridge_client = FakeBridgeClient(self)
        # Keep tests isolated from a developer's persisted surf-agent backend config.
        # Tests that intentionally cover Patchright set SURF_AGENT_BACKEND explicitly.
        if os.environ.get("SURF_AGENT_BACKEND") != "patchright":
            self.backend = "axi"
            self.browser_backend = AxiBackend(self)

    def next_response(self, command):
        if not self.responses:
            raise AssertionError(f"unexpected call: {command}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def _ensure_dedicated_chrome_running(self):
        return None

    def __del__(self):
        tmp = getattr(self, "_surf_agent_home_tmp", None)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
            self._surf_agent_home_tmp = None

    def _chrome_debug_endpoint_ready(self):
        return True

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
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_HOME": tmp}, clear=True):
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.axi_bin, "npx -y chrome-devtools-axi")

    def test_parse_screenshot_output_defaults_to_viewport_and_accepts_full_page(self):
        self.assertEqual(parse_screenshot_output(["/tmp/shot.png"]), ScreenshotOptions(path="/tmp/shot.png"))
        self.assertEqual(parse_screenshot_output(["--output", "/tmp/shot.png"]), ScreenshotOptions(path="/tmp/shot.png"))
        self.assertEqual(parse_screenshot_output(["--full-page", "/tmp/full.png"]), ScreenshotOptions(path="/tmp/full.png", full_page=True))
        self.assertEqual(parse_screenshot_output(["--full-page", "--output", "/tmp/full.png"]), ScreenshotOptions(path="/tmp/full.png", full_page=True))
        self.assertEqual(parse_screenshot_output(["--output", "/tmp/full.png", "--full-page"]), ScreenshotOptions(path="/tmp/full.png", full_page=True))

    def test_parse_screenshot_output_rejects_bad_forms(self):
        for values in ([], ["--output"], ["--bad", "/tmp/shot.png"], ["/tmp/a.png", "/tmp/b.png"], ["--full-page", "--full-page", "/tmp/a.png"]):
            with self.subTest(values=values):
                with self.assertRaises(SurfAgentError):
                    parse_screenshot_output(values)

    def test_axi_screenshot_mapping_defaults_to_viewport_and_full_page_uses_cli_fallback(self):
        mapped = map_axi_cli_args_to_bridge(["screenshot", "/tmp/shot.png"])
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped[0], "take_screenshot")
        self.assertEqual(mapped[1], {"filePath": "/tmp/shot.png"})

        self.assertIsNone(map_axi_cli_args_to_bridge(["screenshot", "--full-page", "/tmp/full.png"]))

    def test_axi_screenshot_command_defaults_to_viewport_and_accepts_full_page(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["selected\n", "screenshot: /tmp/shot.png\n", "selected\n", "screenshot: /tmp/full.png\n"], state_file=state_file)

            self.assertEqual(agent.browser_backend.screenshot(ScreenshotOptions(path="/tmp/shot.png")), "screenshot: /tmp/shot.png\n")
            self.assertEqual(agent.browser_backend.screenshot(ScreenshotOptions(path="/tmp/full.png", full_page=True)), "screenshot: /tmp/full.png\n")

        self.assertEqual([call[0] for call in agent.calls], [
            ["bridge", "select_page", {"pageId": 22}],
            ["bridge", "take_screenshot", {"filePath": "/tmp/shot.png"}],
            ["bridge", "select_page", {"pageId": 22}],
            ["axi", "screenshot", "--full-page", "/tmp/full.png"],
        ])

    def test_surf_agent_home_overrides_all_default_roots(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_HOME": tmp}, clear=True):
            home = Path(tmp)
            self.assertEqual(surf_agent_config_dir(), home)
            self.assertEqual(surf_agent_state_dir(), home)
            self.assertEqual(surf_agent_data_dir(), home)
            self.assertEqual(backend_config_file(), home / "config.json")
            self.assertEqual(default_state_dir(), home / "threads")
            self.assertEqual(default_chrome_profile_dir(), home / "profiles" / "chrome")

    def test_platformdirs_fallback_replaces_package_local_data_dir(self):
        with patch.dict("os.environ", {}, clear=True):
            package_local = Path(__file__).resolve().parents[1] / ".surf-agent"
            self.assertEqual(backend_config_file(), Path(APP_DIRS.user_config_dir) / "config.json")
            self.assertEqual(default_state_dir(), Path(APP_DIRS.user_state_dir) / "threads")
            self.assertEqual(default_chrome_profile_dir(), Path(APP_DIRS.user_data_dir) / "profiles" / "chrome")
            self.assertNotEqual(default_chrome_profile_dir(), package_local / "profiles" / "chrome")

    def test_keyboard_interrupt_exits_without_traceback(self):
        class InterruptingAgent:
            def __init__(self, *args, **kwargs):
                pass

            def run_in_window(self, argv):
                raise KeyboardInterrupt()

        error = io.StringIO()
        with patch("surf_agent.cli.SurfAgent", InterruptingAgent), redirect_stderr(error):
            exit_code = main(["snapshot"])

        self.assertEqual(exit_code, 130)
        self.assertEqual(error.getvalue(), "surf-agent: interrupted\n")
        self.assertNotIn("Traceback", error.getvalue())

    def test_help_lists_only_supported_backend_commands(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["help"]), 0)

        backend_lines = [line.strip() for line in output.getvalue().splitlines() if line.strip().startswith("surf-agent backend set")]
        setup_lines = [line.strip() for line in output.getvalue().splitlines() if line.strip().startswith("surf-agent setup")]
        self.assertEqual([line.split()[:4] for line in backend_lines], [["surf-agent", "backend", "set", "axi|patchright"]])
        self.assertEqual([line.split()[:3] for line in setup_lines], [["surf-agent", "setup", "patchright"]])

    def test_backend_config_commands_and_priority(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch("surf_agent.cli.cleanup_backend_runtime"), patch.dict("os.environ", {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "show"]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"backend": "patchright", "source": "default", "config_file": str(Path(tmp) / "config.json")})

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "axi"]), 0)
            self.assertEqual(json.loads((Path(tmp) / "config.json").read_text()), {"backend": "axi"})

            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.backend, "axi")

            with patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright"}, clear=True):
                self.assertEqual(SurfAgent(state_file=Path(tmp) / "thread2.json").backend, "patchright")
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

    def test_backend_set_cleans_previous_runtime_when_backend_changes(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch("surf_agent.cli.cleanup_backend_runtime") as cleanup, patch.dict("os.environ", {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "axi"]), 0)
            cleanup.assert_called_once_with("patchright")

    def test_backend_set_skips_runtime_cleanup_when_backend_unchanged(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch("surf_agent.cli.cleanup_backend_runtime") as cleanup, patch.dict("os.environ", {}, clear=True):
            (Path(tmp) / "config.json").write_text(json.dumps({"backend": "patchright"}) + "\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "patchright"]), 0)
            cleanup.assert_not_called()

    def test_backend_set_cleanup_ignores_temporary_env_override(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch("surf_agent.cli.cleanup_backend_runtime") as cleanup, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright"}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backend", "set", "axi"]), 0)
            cleanup.assert_called_once_with("patchright")

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
            self.assertEqual(agent.browser_backend._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertNotIn("CHROME_DEVTOOLS_AXI_AUTO_CONNECT", env)
        self.assertNotIn("CHROME_DEVTOOLS_AXI_USER_DATA_DIR", env)
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_BROWSER_URL"], "http://127.0.0.1:9336")
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_PORT"], "9335")

    def test_auto_connect_env_explicitly_overrides_dedicated_profile(self):
        with patch.dict("os.environ", {"CHROME_DEVTOOLS_AXI_AUTO_CONNECT": "1"}, clear=True):
            agent = FakeAxiAgent(["ok\n"])
            self.assertEqual(agent.browser_backend._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_AUTO_CONNECT"], "1")
        self.assertNotIn("CHROME_DEVTOOLS_AXI_USER_DATA_DIR", env)

    def test_axi_user_data_dir_env_overrides_default_profile_dir(self):
        with patch.dict("os.environ", {"CHROME_DEVTOOLS_AXI_USER_DATA_DIR": "/tmp/custom-surf-profile"}, clear=True):
            agent = FakeAxiAgent(["ok\n"])
            self.assertEqual(agent.browser_backend._run_axi_cli_text(["start"]), "ok\n")

        env = agent.calls[0][1]["env"]
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_USER_DATA_DIR"], "/tmp/custom-surf-profile")
        self.assertEqual(env["CHROME_DEVTOOLS_AXI_BROWSER_URL"], "http://127.0.0.1:9336")
        self.assertEqual(agent.chrome_profile_dir, Path("/tmp/custom-surf-profile"))

    def test_patchright_defaults_to_chrome_profile_family(self):
        with TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"SURF_AGENT_BACKEND": "patchright", "SURF_AGENT_CHROME_PROFILE_DIR": str(Path(tmp) / "chrome-profile")},
            clear=True,
        ):
            agent = FakeAxiAgent([], state_file=Path(tmp) / "thread.json")

        self.assertEqual(agent.patchright_profile_dir, Path(tmp) / "chrome-profile")

    def test_default_profiles_live_under_surf_agent_data_dir(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_HOME": tmp}, clear=True):
            self.assertEqual(default_chrome_profile_dir(), skill_data_dir() / "profiles" / "chrome")

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

    def test_profile_command_dispatch(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(SurfAgent, "_chrome_debug_endpoint_ready", return_value=False):
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["profile", "show"]), 0)
            self.assertEqual(json.loads(output.getvalue())["chrome_debug_port"], 9336)

    def test_backend_config_accepts_patchright_and_resolves_backend(self):
        with TemporaryDirectory() as tmp, patch("surf_agent.cli.backend_config_file", return_value=Path(tmp) / "config.json"), patch("surf_agent.cli.cleanup_backend_runtime"), patch.dict("os.environ", {}, clear=True):
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

    def test_setup_patchright_prints_manual_install_instructions_without_running_installer(self):
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.importlib.util.find_spec", return_value=None), patch("surf_agent.cli.find_chrome_bin", return_value=None), patch("surf_agent.cli.subprocess.run") as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["setup", "patchright"]), 0)

        run.assert_not_called()
        self.assertIn("Patchright setup is manual", output.getvalue())
        self.assertIn("Install Google Chrome yourself", output.getvalue())
        self.assertIn("surf-agent[patchright] @ git+https://github.com/ewgdg/browser-skills.git", output.getvalue())
        self.assertEqual(error.getvalue(), "")

    def test_setup_patchright_reports_already_setup_when_package_and_chrome_exist(self):
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.importlib.util.find_spec", return_value=object()), patch("surf_agent.cli.find_chrome_bin", return_value="/usr/bin/google-chrome"), patch("surf_agent.cli.subprocess.run") as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["setup", "patchright"]), 0)

        run.assert_not_called()
        self.assertIn("Patchright appears set up", output.getvalue())
        self.assertIn("/usr/bin/google-chrome", output.getvalue())
        self.assertEqual(error.getvalue(), "")

    def test_patchright_setup_alias_prints_same_manual_instructions(self):
        with patch.dict("os.environ", {}, clear=True), patch("surf_agent.cli.importlib.util.find_spec", return_value=None), patch("surf_agent.cli.find_chrome_bin", return_value=None), patch("surf_agent.cli.subprocess.run") as run:
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["patchright", "setup"]), 0)

        run.assert_not_called()
        self.assertIn("Patchright setup is manual", output.getvalue())
        self.assertIn("Install Google Chrome yourself", output.getvalue())
        self.assertEqual(error.getvalue(), "")

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
            self.assertEqual(agent.execute_in_window(["fill", "@e2", "hello", "world"]), "fill ok\n")
            self.assertEqual(agent.execute_in_window(["screenshot", "/tmp/viewport.png"]), "screenshot ok\n")
            self.assertEqual(agent.execute_in_window(["screenshot", "--full-page", "--output", "/tmp/full.png"]), "screenshot ok\n")
            with self.assertRaisesRegex(SurfAgentError, "scroll requires direction"):
                agent.execute_in_window(["scroll", "sideways"])

        self.assertEqual(
            client.calls,
            [
                ("open", {"thread": "thread", "url": "https://example.test/"}),
                ("fill", {"thread": "thread", "uid": "@e2", "text": "hello world"}),
                ("screenshot", {"thread": "thread", "path": "/tmp/viewport.png", "fullPage": False}),
                ("screenshot", {"thread": "thread", "path": "/tmp/full.png", "fullPage": True}),
            ],
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
            with patch("surf_agent.backends.local_bridge.subprocess.Popen", side_effect=lambda *a, **kw: pops.append((a, kw)) or object()):
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
        try:
            with patch("surf_agent.backends.patchright.bridge.async_playwright", return_value=FakeManager()):
                runtime.start()

            self.assertEqual(calls[0]["user_data_dir"], "/tmp/surf-patchright-test")
            self.assertEqual(calls[0]["channel"], "chrome")
            self.assertFalse(calls[0]["headless"])
            self.assertTrue(calls[0]["no_viewport"])
            self.assertTrue(calls[0]["chromium_sandbox"])
            self.assertEqual(calls[0]["args"], ["--class=surf-agent-window", "--name=surf-agent-test"])
        finally:
            runtime.stop()


    def test_patchright_new_page_uses_cdp_target_create_window_with_owned_anchor(self):
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
                page = FakePage(params["url"], target_id="target-1")
                self.context.pages.append(page)
                return {"targetId": "target-1"}

            def detach(self):
                self.detached = True

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages
                self.cdp_calls = []
                self.new_page_calls = 0

            def new_page(self):
                self.new_page_calls += 1
                page = FakePage()
                self.pages.append(page)
                return page

            def new_cdp_session(self, page):
                self.anchor = page
                return FakeSession(self, page)

        anchor = FakePage("https://owned.test/")
        context = FakeContext([anchor])
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = context
        runtime.pages["owned"] = patchright_bridge.PageSlot(page=anchor, page_token=1)
        runtime._next_page_token = 2

        slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

        self.assertIsNot(slot.page, anchor)
        self.assertEqual(slot.page.url, "https://welcome.test/")
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(context.cdp_calls, [("Target.createTarget", {"url": "https://welcome.test/", "newWindow": True, "background": False})])

    def test_patchright_new_page_adopts_clean_startup_page(self):
        class FakePage:
            def __init__(self, url, *, target_id="page-target"):
                self.url = url
                self.target_id = target_id
                self.closed = False
                self.goto_calls = []

            def is_closed(self):
                return self.closed

            def close(self):
                self.closed = True

            def goto(self, url, wait_until=None):
                self.goto_calls.append((url, wait_until))
                self.url = url

        class FakeContext:
            def __init__(self, pages):
                self.pages = pages
                self.cdp_calls = []
                self.new_page_calls = 0

            def new_page(self):
                self.new_page_calls += 1
                page = FakePage("about:blank")
                self.pages.append(page)
                return page

        restored = FakePage("https://restored.test/")
        newtab = FakePage("about:blank")
        context = FakeContext([restored, newtab])
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime.browser_or_context = context

        slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

        self.assertIs(slot.page, newtab)
        self.assertEqual(slot.page.url, "https://welcome.test/")
        self.assertEqual(newtab.goto_calls, [("https://welcome.test/", "domcontentloaded")])
        self.assertTrue(restored.closed)
        self.assertFalse(newtab.closed)
        self.assertEqual(context.new_page_calls, 0)
        self.assertEqual(context.cdp_calls, [])

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
            slot = runtime._run(runtime._new_page("thread", url="https://welcome.test/"))

        self.assertEqual(len(restarts), 1)
        self.assertEqual(slot.page.url, "https://welcome.test/")
        self.assertEqual(first_context.cdp_calls, [("Target.createTarget", {"url": "https://welcome.test/", "newWindow": True, "background": False})])
        self.assertEqual(second_context.cdp_calls, [("Target.createTarget", {"url": "https://welcome.test/", "newWindow": True, "background": False})])

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
                raise AssertionError("CDP-created replacement page should not be navigated again")

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
        runtime.pages["thread"] = patchright_bridge.PageSlot(page=dead, page_token=1)

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        self.assertEqual(runtime.pages["thread"].page.url, "https://example.test/")
        self.assertEqual(runtime.pages["thread"].page.goto_calls, [])
        self.assertEqual(context.cdp_calls, [("Target.createTarget", {"url": "https://example.test/", "newWindow": True, "background": False})])

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

            def fill(self, text):
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

            def click(self):
                return self.first.click()

            def fill(self, text):
                return self.first.fill(text)

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
                self.keyboard = types.SimpleNamespace(type=self._type, press=self._press)
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

            def new_page(self):
                page = FakePage()
                self.pages.append(page)
                return page

            def new_cdp_session(self, page):
                return FakeSession(self, page)

        context = FakeContext()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"), app_id="surf-agent-test", window_class="surf-agent-window")
        runtime.browser_or_context = context

        self.assertEqual(runtime.call("open", {"thread": "thread", "url": "https://example.test/"}), "opened https://example.test/\n")
        self.assertEqual(context.cdp_calls, [("Target.createTarget", {"url": "https://example.test/", "newWindow": True, "background": False})])
        snapshot = runtime.call("snapshot", {"thread": "thread"})
        self.assertIn("[ref=e2]", snapshot)
        self.assertEqual(runtime.call("fill", {"thread": "thread", "uid": "@e2", "text": "hello"}), "filled\n")
        self.assertEqual(runtime.pages["thread"].page.actionable.filled, "hello")
        snapshot = runtime.call("snapshot", {"thread": "thread"})
        self.assertIn("[ref=e2]", snapshot)
        self.assertEqual(runtime.call("click", {"thread": "thread", "uid": "e2"}), "clicked\n")
        self.assertTrue(runtime.pages["thread"].page.actionable.clicked)
        self.assertEqual(runtime.call("type", {"thread": "thread", "text": "typed text"}), "typed\n")
        self.assertEqual(runtime.pages["thread"].page.typed, "typed text")
        self.assertEqual(runtime.call("screenshot", {"thread": "thread", "path": "/tmp/viewport.png"}), "screenshot: /tmp/viewport.png\n")
        self.assertEqual(runtime.pages["thread"].page.screenshot_calls[-1], {"path": "/tmp/viewport.png", "full_page": False})
        self.assertEqual(runtime.call("screenshot", {"thread": "thread", "path": "/tmp/full.png", "fullPage": True}), "screenshot: /tmp/full.png\n")
        self.assertEqual(runtime.pages["thread"].page.screenshot_calls[-1], {"path": "/tmp/full.png", "full_page": True})
        self.assertEqual(runtime.call("text", {"thread": "thread"}), "Body text\n")

        state = json.loads(runtime.call("state", {"thread": "thread"}))
        self.assertEqual(state, {"backend": "patchright", "open": True, "thread": "thread", "page_id": 1, "url": "https://example.test/", "title": "Example"})
        listing = json.loads(runtime.call("list", {}))
        self.assertEqual(listing, {"backend": "patchright", "pages": [{"thread": "thread", "page_id": 1, "url": "https://example.test/", "title": "Example"}]})

    def test_patchright_actions_delegate_iframe_refs_and_reject_stale_refs(self):
        class FakeNativeRefLocator:
            def __init__(self, count):
                self.match_count = count
                self.clicked = False

            def count(self):
                return self.match_count

            def click(self):
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
        runtime.pages["thread"] = PageSlot(page=page, page_token=1)

        self.assertEqual(runtime.call("click", {"thread": "thread", "uid": "f1e2"}), "clicked\n")
        self.assertTrue(page.iframe_button.clicked)
        with self.assertRaisesRegex(RuntimeError, "Capture a new snapshot"):
            runtime.call("click", {"thread": "thread", "uid": "@e404"})
        with self.assertRaisesRegex(RuntimeError, "Capture a new snapshot"):
            runtime.call("click", {"thread": "thread", "uid": "@e2 >> css=body"})
        self.assertFalse(page.selector_injection.clicked)

    def test_patchright_bridge_screenshot_uses_viewport_by_default_and_full_page_when_requested(self):
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
        runtime.pages["thread"] = patchright_bridge.PageSlot(page=page, page_token=1)

        self.assertEqual(runtime.call("screenshot", {"thread": "thread", "path": "/tmp/viewport.png"}), "screenshot: /tmp/viewport.png\n")
        self.assertEqual(runtime.call("screenshot", {"thread": "thread", "path": "/tmp/string-false.png", "fullPage": "false"}), "screenshot: /tmp/string-false.png\n")
        self.assertEqual(runtime.call("screenshot", {"thread": "thread", "path": "/tmp/full.png", "fullPage": True}), "screenshot: /tmp/full.png\n")
        self.assertEqual(page.screenshot_calls, [
            {"path": "/tmp/viewport.png", "full_page": False},
            {"path": "/tmp/string-false.png", "full_page": False},
            {"path": "/tmp/full.png", "full_page": True},
        ])

    def test_patchright_snapshot_preserves_native_and_literal_refs(self):
        class FakePage:
            def aria_snapshot(self, *args, **kwargs):
                return '- button "[ref=e999]" [ref=e188]\n- paragraph [ref=e189]: Hello [ref=e777]'

        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        slot = patchright_bridge.PageSlot(page=FakePage(), page_token=1)

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
        slot = patchright_bridge.PageSlot(page=page, page_token=1)

        with patch.object(patchright_bridge, "SNAPSHOT_DEPTH", 4), patch.object(patchright_bridge, "SNAPSHOT_BOXES", True):
            snapshot = runtime._run(runtime._snapshot(slot))

        self.assertIn('- page "Example"', snapshot)
        self.assertEqual(page.calls, [{"mode": "ai", "timeout": patchright_bridge.SNAPSHOT_ARIA_TIMEOUT_MS, "depth": 4, "boxes": True}])

    def test_patchright_snapshot_default_depth_and_boxes_are_explicit(self):
        class FakePage:
            def __init__(self):
                self.calls = []

            def aria_snapshot(self, **kwargs):
                self.calls.append(kwargs)
                return "snapshot body"

        page = FakePage()
        runtime = PatchrightRuntime(profile_dir=Path("/tmp/surf-patchright-test"))
        runtime._run(runtime._snapshot(patchright_bridge.PageSlot(page=page, page_token=1)))

        self.assertEqual(page.calls, [{"mode": "ai", "timeout": patchright_bridge.SNAPSHOT_ARIA_TIMEOUT_MS, "depth": None, "boxes": False}])

    def test_patchright_bridge_client_timeout_has_clear_error(self):
        from surf_agent.backends.patchright.backend import PatchrightBridgeClient

        client = PatchrightBridgeClient(timeout_s=1.0, port=9555, profile_dir=Path("/tmp/surf-patchright-profile"))
        with (
            patch.object(client, "_ensure_running", return_value=None),
            patch("surf_agent.backends.local_bridge.urllib.request.urlopen", side_effect=TimeoutError("timed out")),
        ):
            with self.assertRaisesRegex(BridgeUnavailable, "Patchright bridge tool snapshot timed out after 1s"):
                client.call_tool("snapshot", {"thread": "default"})

    def test_patchright_bridge_client_ensure_running_spawns_bridge_module_with_profile_and_port(self):
        from surf_agent.backends.patchright.backend import PatchrightBridgeClient

        profile_dir = Path("/tmp/surf-patchright-profile")
        client = PatchrightBridgeClient(timeout_s=1.0, port=9555, profile_dir=profile_dir)
        pops = []

        with (
            patch.object(client, "_health_ok", side_effect=[False, True]),
            patch("surf_agent.backends.local_bridge.subprocess.Popen", side_effect=lambda *a, **kw: pops.append((a, kw)) or object()),
            patch("surf_agent.backends.local_bridge.time.monotonic", side_effect=[0.0, 0.0, 0.1]),
            patch("surf_agent.backends.local_bridge.time.sleep", return_value=None),
        ):
            client._ensure_running()

        command = pops[0][0][0]
        self.assertEqual(command[:6], [sys.executable, "-m", "surf_agent.backends.patchright.bridge", "--port", "9555", "--profile-dir"])
        self.assertEqual(command[6], str(profile_dir))

    def test_bridge_unavailable_starts_once_then_uses_http(self):
        agent = FakeAxiAgent([AxiBridgeUnavailable("down"), "started\n", "## Pages\n1: Example (https://example.test/)\n"])

        self.assertEqual(agent.browser_backend._run_axi_text(["pages"]), "## Pages\n1: Example (https://example.test/)\n")
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
            self.assertEqual(commands[1][2], f"--user-data-dir={agent.chrome_profile_dir}")
            self.assertEqual(commands[1][3], "--new-window")
            self.assertEqual(commands[1][4], "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent")
            self.assertEqual(commands[2:], [["bridge", "list_pages", {}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "navigate_page", {"type": "url", "url": "https://example.test/"}]])
            self.assertEqual(output.getvalue(), "Successfully navigated to https://example.test/.\n")
            self.assertFalse(any(call[0][0] == "axi" for call in agent.calls))

    def test_open_discards_stale_state_when_select_cannot_confirm_page(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(7, url="https://stale.test/")))
            agent = FakeAxiAgent(
                [
                    "Error: No page found",
                    "24 Existing https://existing.test/\n",
                    "",
                    "24,Existing,false\n22,Surf Agent,false\n",
                    "selected\n",
                    axi_identity_result(),
                    "selected\n",
                    "Successfully navigated to https://example.test/.\n",
                ],
                state_file=state_file,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["open", "https://example.test/"]), 0)

            state = json.loads(state_file.read_text())
            self.assertEqual(state["page_id"], 22)
            self.assertEqual(state["url"], "https://example.test/")
            self.assertEqual([call[0] for call in agent.calls], [
                ["bridge", "select_page", {"pageId": 7}],
                ["bridge", "list_pages", {}],
                ["chrome", "--class=surf-agent", f"--user-data-dir={agent.chrome_profile_dir}", "--new-window", "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent"],
                ["bridge", "list_pages", {}],
                ["bridge", "select_page", {"pageId": 22}],
                ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}],
                ["bridge", "select_page", {"pageId": 22}],
                ["bridge", "navigate_page", {"type": "url", "url": "https://example.test/"}],
            ])
            self.assertEqual(output.getvalue(), "Successfully navigated to https://example.test/.\n")

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
            self.assertEqual(commands[1], ["chrome", "--class=surf-agent", f"--user-data-dir={agent.chrome_profile_dir}", "--new-window", "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent"])
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
            self.assertEqual(commands[1][2], f"--user-data-dir={agent.chrome_profile_dir}")
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

    def test_eval_stdin_preserves_multiline_code(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/")))
            source = "() => {\n  return 1;\n}"
            agent = FakeAxiAgent(["selected\n", "1\n"], state_file=state_file)
            output = io.StringIO()
            with patch("sys.stdin", io.StringIO(source)), redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["eval", "--stdin"]), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": source}]])
            self.assertEqual(output.getvalue(), "result: 1\n")

    def test_eval_file_reads_code(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22, url="https://example.test/")))
            script = Path(tmp) / "script.js"
            source = "() => {\n  return document.title;\n}\n"
            script.write_text(source, encoding="utf-8")
            agent = FakeAxiAgent(["selected\n", '"Example"\n'], state_file=state_file)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["eval", "--file", str(script)]), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": source.strip()}]])
            self.assertEqual(output.getvalue(), 'result: "Example"\n')

    def test_eval_rejects_conflicting_sources(self):
        cases = [
            ["--stdin", "--file", "x"],
            ["--stdin", "1"],
            ["--file", "x", "1"],
            ["--stdin", "--stdin"],
            ["--file", "x", "--file", "y"],
        ]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaisesRegex(SurfAgentError, "exactly one source"):
                    parse_eval_code(values, stdin=io.StringIO("1"))

    def test_eval_rejects_unknown_option_and_missing_file_path(self):
        with self.assertRaisesRegex(SurfAgentError, "unsupported eval option: --bad"):
            parse_eval_code(["--bad"], stdin=io.StringIO("1"))
        with self.assertRaisesRegex(SurfAgentError, "eval --file requires a path"):
            parse_eval_code(["--file"], stdin=io.StringIO("1"))

    def test_eval_file_missing_is_usage_error(self):
        with self.assertRaisesRegex(SurfAgentError, "could not read eval file"):
            parse_eval_code(["--file", "/tmp/definitely-missing-surf-agent-eval.js"])

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

    def test_close_starts_idle_shutdown_recheck(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["closed\n"], state_file=state_file)
            with patch.object(agent.browser_backend, "_stop_idle_bridge_if_needed") as idle:
                self.assertEqual(agent.close(), 0)

        self.assertEqual([call[0] for call in agent.calls], [["bridge", "close_page", {"pageId": 22}]])
        idle.assert_called_once_with()
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
            with patch.object(agent.browser_backend, "_stop_idle_bridge_if_needed") as idle, redirect_stdout(output):
                exit_code = agent.close_matching("agent-a-*")

            payload = json.loads(output.getvalue())
            commands = [call[0] for call in agent.calls]
            self.assertEqual(exit_code, 0)
            self.assertEqual(commands, [["bridge", "close_page", {"pageId": 101}], ["bridge", "close_page", {"pageId": 102}], ["bridge", "close_page", {"pageId": 999}]])
            idle.assert_called_once_with()
            self.assertEqual(payload["stale"], [])
            self.assertEqual(payload["closed"], [{"thread": "agent-a-1", "page_id": 101}, {"thread": "agent-a-2", "page_id": 102}, {"thread": "agent-a-stale", "page_id": 999}])
            self.assertFalse((state_dir / "agent-a-1.json").exists())
            self.assertFalse((state_dir / "agent-a-2.json").exists())
            self.assertTrue((state_dir / "agent-b-1.json").exists())
            self.assertFalse((state_dir / "agent-a-stale.json").exists())

    def test_timeout_raises_clear_axi_error(self):
        agent = FakeAxiAgent([subprocess.TimeoutExpired(["axi", "eval", "1"], 1)])
        with self.assertRaisesRegex(Exception, "browser command timed out after 1s: eval 1.*browser bridge"):
            agent.browser_backend._run_axi_cli_text(["eval", "1"])

    def test_bridge_stop_is_explicit_only(self):
        agent = FakeAxiAgent(["stopped\n"])
        output = io.StringIO()
        with patch("surf_agent.cli.stop_axi_chrome_runtime") as stop_chrome, redirect_stdout(output):
            self.assertEqual(agent.bridge_stop(), 0)
        self.assertEqual([call[0] for call in agent.calls], [["axi", "stop"]])
        self.assertEqual(output.getvalue(), "stopped\n")
        stop_chrome.assert_called_once_with(agent.chrome_profile_dir, debug_port=agent.chrome_debug_port)

    def test_bridge_stop_command_is_canonical(self):
        agent = FakeAxiAgent(["stopped\n"])
        output = io.StringIO()
        with patch("surf_agent.cli.SurfAgent", return_value=agent), patch("surf_agent.cli.stop_axi_chrome_runtime"), redirect_stdout(output):
            self.assertEqual(main(["bridge", "stop"]), 0)
        self.assertEqual([call[0] for call in agent.calls], [["axi", "stop"]])
        self.assertEqual(output.getvalue(), "stopped\n")

    def test_stop_bridge_is_not_supported(self):
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(["stop", "bridge"]), 2)
        self.assertIn("unsupported browser command: stop", error.getvalue())

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

    def test_do_natural_pacing_runs_before_eligible_mutations_only(self):
        events = []

        class RecordingAgent:
            def execute_in_window(self, args):
                events.append(tuple(args))
                return f"{args[0]} output"

        class RecordingPacer:
            def pause(self):
                events.append("pace")

        exit_code = run_do(
            RecordingAgent(),
            thread="thread",
            argv=[
                "--pace",
                "natural",
                "text",
                "::",
                "fill",
                "@e1",
                "hello",
                "::",
                "wait",
                "1000",
                "::",
                "click",
                "@e2",
                "::",
                "eval",
                "1",
                "::",
                "scroll",
                "down",
                "::",
                "screenshot",
                "::",
                "back",
            ],
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            pacer=RecordingPacer(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            events,
            [
                ("text",),
                "pace",
                ("fill", "@e1", "hello"),
                ("wait", "1000"),
                ("click", "@e2"),
                ("eval", "1"),
                "pace",
                ("scroll", "down"),
                ("screenshot",),
                "pace",
                ("back",),
            ],
        )

    def test_do_natural_pacing_preserves_jsonl_output_for_stdin_scripts(self):
        events = []

        class RecordingAgent:
            def execute_in_window(self, args):
                events.append(tuple(args))
                return f"{args[0]} output"

        class RecordingPacer:
            def pause(self):
                events.append("pace")

        output = io.StringIO()
        exit_code = run_do(
            RecordingAgent(),
            thread="thread",
            argv=["--jsonl", "--pace", "natural"],
            stdin=io.StringIO("eval 1 --emit\nclick @e1\n"),
            stdout=output,
            stderr=io.StringIO(),
            pacer=RecordingPacer(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, [("eval", "1"), "pace", ("click", "@e1")])
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            records,
            [
                {"command": "eval", "output": "eval output", "status": "success", "step": 1},
                {"command": "click", "output": "click output", "status": "success", "step": 2},
            ],
        )

    def test_do_defaults_to_no_pacing_and_never_pauses_after_final_step(self):
        events = []

        class RecordingAgent:
            def execute_in_window(self, args):
                events.append(tuple(args))
                return "ok"

        class RecordingPacer:
            def pause(self):
                events.append("pace")

        exit_code = run_do(
            RecordingAgent(),
            thread="thread",
            argv=["open", "https://example.test", "::", "click", "@e1"],
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            pacer=RecordingPacer(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, [("open", "https://example.test"), ("click", "@e1")])

    def test_do_natural_pacing_does_not_sleep_before_invalid_mutation(self):
        events = []

        class RecordingAgent:
            def execute_in_window(self, args):
                events.append(tuple(args))
                if args == ["click"]:
                    raise SurfAgentError("click requires exactly one target", exit_code=2)
                return "ok"

        class RecordingPacer:
            def pause(self):
                events.append("pace")

        exit_code = run_do(
            RecordingAgent(),
            thread="thread",
            argv=["--pace", "natural", "text", "::", "click"],
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            pacer=RecordingPacer(),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(events, [("text",)])

    def test_do_natural_pacing_preserves_fail_fast_behavior(self):
        events = []

        class FailingAgent:
            def execute_in_window(self, args):
                events.append(tuple(args))
                if args == ["click", "@e2"]:
                    raise SurfAgentError("bad click", exit_code=1)
                return "ok"

        class RecordingPacer:
            def pause(self):
                events.append("pace")

        output = io.StringIO()
        error = io.StringIO()
        exit_code = run_do(
            FailingAgent(),
            thread="thread",
            argv=["--pace", "natural", "click", "@e1", "::", "click", "@e2", "::", "click", "@e3"],
            stdin=io.StringIO(),
            stdout=output,
            stderr=error,
            pacer=RecordingPacer(),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(events, [("click", "@e1"), "pace", ("click", "@e2")])
        self.assertEqual(output.getvalue(), "")
        self.assertIn("step 2 `click @e2` failed: bad click", error.getvalue())

    def test_do_rejects_missing_or_unknown_pacing_profile_before_execution(self):
        class RecordingAgent:
            def __init__(self):
                self.calls = []

            def execute_in_window(self, args):
                self.calls.append(args)
                return "ok"

        for argv in (["--pace"], ["--pace", "fast", "click", "@e1"]):
            with self.subTest(argv=argv):
                agent = RecordingAgent()
                error = io.StringIO()

                exit_code = run_do(agent, thread="thread", argv=argv, stdin=io.StringIO(), stdout=io.StringIO(), stderr=error)

                self.assertEqual(exit_code, 2)
                self.assertIn("--pace", error.getvalue())
                self.assertEqual(agent.calls, [])

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


class AxiIdleSafetyTests(unittest.TestCase):
    def test_idle_inventory_failure_is_unknown_and_background_targets_do_not_count(self):
        from surf_agent.backends.axi import parse_axi_user_visible_pages

        self.assertEqual(parse_axi_user_visible_pages('{"pages":[{"pageId":1,"type":"service_worker"}]}'), [])
        self.assertEqual([page.page_id for page in parse_axi_user_visible_pages('{"pages":[{"pageId":2,"type":"page"}]}')], [2])
        with self.assertRaises(SurfAgentError):
            parse_axi_user_visible_pages("unparseable bridge output")


class LaunchPreflightTests(unittest.TestCase):
    def test_axi_profile_open_runs_under_lifecycle_launch_guard(self):
        class Guard:
            def __init__(self):
                self.events = []

            def launch_guard(self, *, health_check):
                from contextlib import contextmanager

                @contextmanager
                def guard():
                    self.events.append("entered")
                    self.assert_false(health_check())
                    yield
                    self.events.append("exited")

                return guard()

            @staticmethod
            def assert_false(value):
                assert value is False

        with TemporaryDirectory() as tmp:
            agent = FakeAxiAgent([""], chrome_profile_dir=Path(tmp) / "profile")
            guard = Guard()
            agent.lifecycle = guard
            with patch.object(agent, "_chrome_debug_endpoint_ready", return_value=False):
                self.assertEqual(agent.profile_open(), 0)
        self.assertEqual(guard.events, ["entered", "exited"])

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

        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright"}, clear=True):
            agent = FakeAxiAgent([], patchright_profile_dir=Path(tmp) / "profile")
            guard = Guard()
            agent.lifecycle = guard
            with patch.object(agent.patchright_client, "_health_ok", return_value=False), patch("surf_agent.backends.patchright.backend.subprocess.Popen", return_value=object()):
                self.assertEqual(agent.profile_open(), 0)
        self.assertEqual(guard.events, ["entered", "exited"])


class LaunchFailureTests(unittest.TestCase):
    def test_axi_profile_open_import_failure_prevents_browser_launch(self):
        class FailingImporter:
            def run(self, force):
                raise SurfAgentError("import failed")

        with TemporaryDirectory() as tmp:
            agent = FakeAxiAgent([], chrome_profile_dir=Path(tmp) / "profile")
            from surf_agent.chrome_lifecycle import ChromeLifecycleCoordinator

            agent.lifecycle = ChromeLifecycleCoordinator(
                destination_root=agent.chrome_profile_dir,
                state_root=Path(tmp) / "state",
                importer=FailingImporter(),
                process_inspector=lambda _path: False,
            )
            with patch.object(agent, "_chrome_debug_endpoint_ready", return_value=False), self.assertRaisesRegex(SurfAgentError, "import failed"):
                agent.profile_open()
        self.assertEqual(agent.calls, [])


class PatchrightExecutableTests(unittest.TestCase):
    def test_patchright_profile_open_rejects_non_chrome_executable(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SURF_AGENT_BACKEND": "patchright"}, clear=True):
            agent = FakeAxiAgent([], patchright_profile_dir=Path(tmp) / "profile")
            agent.chrome_bin = "chromium"
            with patch.object(agent.patchright_client, "_health_ok", return_value=False), self.assertRaisesRegex(SurfAgentError, "Google Chrome"):
                agent.profile_open()
        self.assertEqual(agent.calls, [])
