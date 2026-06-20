import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from surf_agent.cli import AgentPage, AxiBridgeClient, AxiBridgeUnavailable, SurfAgent, SurfAgentError, default_chrome_profile_dir, main, parse_axi_pages, surf_agent_app_url


def page_state(page_id, **extra):
    payload = {"backend": "axi", "page_id": page_id}
    payload.update(extra)
    return payload


def legacy_extra_page_state(page_id, **extra):
    payload = {"backend": "axi", "page_id": page_id, "owner": "surf-agent", "token": "surf-agent:test-token"}
    payload.update(extra)
    return payload


def bridge_eval_raw(value):
    return "Script ran on page and returned:\n```json\n" + json.dumps(value) + "\n```\n"


def axi_identity_result(title="Surf Agent", href=None):
    return bridge_eval_raw({"title": title, "href": href or surf_agent_app_url()})


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


class AxiBackendTests(unittest.TestCase):
    def test_constructs_without_backend_env(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            agent = SurfAgent(state_file=Path(tmp) / "thread.json")
            self.assertEqual(agent.axi_bin, "npx -y chrome-devtools-axi")

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

    def test_profile_command_dispatch(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(SurfAgent, "_chrome_debug_endpoint_ready", return_value=False):
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(main(["profile", "show"]), 0)
            self.assertEqual(json.loads(output.getvalue())["chrome_debug_port"], 9336)

    def test_bridge_unavailable_starts_once_then_uses_http(self):
        agent = FakeAxiAgent([AxiBridgeUnavailable("down"), "started\n", "## Pages\n1: Example (https://example.test/)\n"])

        self.assertEqual(agent._run_axi_text(["pages"]), "## Pages\n1: Example (https://example.test/)\n")
        commands = [call[0] for call in agent.calls]
        self.assertEqual(commands, [["bridge", "list_pages", {}], ["axi", "start"], ["bridge", "list_pages", {}]])

    def test_go_creates_and_saves_axi_page_state(self):
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
                self.assertEqual(agent.run_in_window(["go", "https://example.test/"]), 0)

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
            self.assertIn("Successfully navigated", output.getvalue())
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
            self.assertIn("go%20%26lt%3Burl%26gt%3B", commands[6][2]["url"])

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

            with self.assertRaisesRegex(Exception, "could not find new AXI page titled"):
                agent.run_in_window(["go", "https://example.test/"])

            self.assertFalse(state_file.exists())
            commands = [call[0] for call in agent.calls]
            self.assertEqual(commands[0], ["bridge", "list_pages", {}])
            self.assertEqual(commands[1][0], "chrome")
            self.assertEqual(commands[1][1], "--class=surf-agent")
            self.assertEqual(commands[1][2], f"--user-data-dir={default_chrome_profile_dir()}")
            self.assertEqual(commands[1][3], "--new-window")
            self.assertEqual(commands[1][4], "data:text/html,%3Ctitle%3ESurf%20Agent%3C%2Ftitle%3ESurf%20Agent")
            self.assertEqual(commands[2:], [["bridge", "list_pages", {}], ["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (JSON.stringify({title:document.title,href:location.href}))"}]])

    def test_existing_go_selects_remembered_page_before_navigation(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(legacy_extra_page_state(22, url="https://old.test/")))
            agent = FakeAxiAgent(
                [
                    "selected\n",
                    'Successfully navigated to https://example.test/.\n## Pages\n22: Example (https://example.test/) [selected]\n',
                ],
                state_file=state_file,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(agent.run_in_window(["go", "https://example.test/"]), 0)

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
                self.assertEqual(agent.run_in_window(["js", "1"]), 0)

            self.assertEqual([call[0] for call in agent.calls], [["bridge", "select_page", {"pageId": 22}], ["bridge", "evaluate_script", {"function": "() => (1)"}]])
            self.assertEqual(output.getvalue(), "result: 1\n")

    def test_select_page_failure_stops_before_action(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            select_failed = subprocess.CompletedProcess(["axi", "selectpage", "22"], 1, stdout="", stderr="bad page")
            agent = FakeAxiAgent([select_failed], state_file=state_file)

            with self.assertRaisesRegex(Exception, "bad page"):
                agent.run_in_window(["js", "1"])

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

    def test_stale_page_state_is_forgotten_without_creating_page(self):
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
            failed_close = subprocess.CompletedProcess(["axi", "closepage", "22"], 1, stdout="", stderr="approval required")
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
        with self.assertRaisesRegex(Exception, "AXI command timed out after 1s: eval 1.*Chrome approval"):
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

    def test_extract_page_id_ignores_snapshot_uids(self):
        from surf_agent.cli import extract_page_id

        self.assertIsNone(extract_page_id('snapshot:\nuid=g24:3_0 RootWebArea "Example Domain"\n'))
        self.assertEqual(extract_page_id("pageId: 39\n"), 39)

    def test_unsupported_axi_command_fails_clearly(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps(page_state(22)))
            agent = FakeAxiAgent(["22 Example https://example.test/\n", "selected\n"], state_file=state_file)
            with self.assertRaisesRegex(Exception, "unsupported AXI backend command: forward"):
                agent.run_in_window(["forward"])

    def test_close_matching_requires_pattern(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(["close-matching"])

        self.assertEqual(exit_code, 2)

    def test_window_id_legacy_command_is_removed(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(["window-id"])

        self.assertEqual(exit_code, 2)
        self.assertIn("unsupported AXI backend command: window-id", error.getvalue())


if __name__ == "__main__":
    unittest.main()
