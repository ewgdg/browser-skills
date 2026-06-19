import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from surf_agent.cli import AgentWindow, SurfAgent, extract_window_id, main


class FakeAgent(SurfAgent):
    def __init__(self, responses, *args, **kwargs):
        super().__init__(surf_bin="surf", *args, **kwargs)
        self.responses = list(responses)
        self.calls = []

    def _run_json(self, args):
        self.calls.append(list(args))
        if not self.responses:
            raise AssertionError(f"unexpected call: {args}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CloseRun:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Window closed", stderr="")


class SurfAgentWindowListingTests(unittest.TestCase):
    def test_lists_windows_without_tabs_to_avoid_global_tab_payload(self):
        agent = FakeAgent([{"windows": [{"id": 123, "tabCount": 1}]}])

        self.assertEqual(agent._list_windows(allow_failure=False), [{"id": 123, "tabCount": 1}])

        self.assertEqual(agent.calls, [["window.list"]])

    def test_print_state_reads_page_state_only_for_owned_window(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps({"window_id": 123, "tab_id": 456}))
            agent = FakeAgent(
                [
                    {"windows": [{"id": 123, "tabCount": 1}]},
                    {"url": "https://example.test/", "title": "Example"},
                ],
                state_file=state_file,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="thread")

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["url"], "https://example.test/")
        self.assertEqual(payload["title"], "Example")
        self.assertEqual(agent.calls, [["window.list"], ["--window-id", "123", "page.state"]])

    def test_create_window_requests_unfocused_window(self):
        agent = FakeAgent(
            [
                {"windows": []},
                "Window 123 (tab 456)\nUse --window-id 123 to target this window",
                {"windows": [{"id": 123, "tabCount": 1, "tabs": [{"id": 456}]}]},
            ]
        )

        self.assertEqual(agent._create_window(), AgentWindow(123, 456))
        self.assertEqual(agent.calls[1], ["window.new", "--unfocused"])

    def test_focus_uses_remembered_window(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "thread.json"
            state_file.write_text(json.dumps({"window_id": 123}))
            fake_run = CloseRun()
            agent = FakeAgent([], state_file=state_file)
            agent._subprocess_run = fake_run

            self.assertEqual(agent.focus(), 0)
            self.assertEqual(fake_run.calls[0][0], ["surf", "window.focus", "123"])

    def test_close_matching_closes_only_matching_remembered_open_threads(self):
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "agent-a-1.json").write_text(json.dumps({"window_id": 101}))
            (state_dir / "agent-a-2.json").write_text(json.dumps({"window_id": 102}))
            (state_dir / "agent-b-1.json").write_text(json.dumps({"window_id": 201}))
            (state_dir / "agent-a-stale.json").write_text(json.dumps({"window_id": 999}))
            fake_run = CloseRun()
            agent = FakeAgent(
                [{"windows": [{"id": 101}, {"id": 102}, {"id": 201}]}],
                state_file=state_dir / "unused.json",
            )
            agent._subprocess_run = fake_run

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = agent.close_matching("agent-a-*")

            payload = json.loads(output.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertEqual([call[0] for call in fake_run.calls], [["surf", "window.close", "101"], ["surf", "window.close", "102"]])
            self.assertEqual(payload["closed"], [{"thread": "agent-a-1", "window_id": 101}, {"thread": "agent-a-2", "window_id": 102}])
            self.assertEqual(payload["stale"], [{"thread": "agent-a-stale", "window_id": 999}])
            self.assertFalse((state_dir / "agent-a-1.json").exists())
            self.assertFalse((state_dir / "agent-a-2.json").exists())
            self.assertTrue((state_dir / "agent-b-1.json").exists())
            self.assertFalse((state_dir / "agent-a-stale.json").exists())

    def test_close_matching_requires_pattern(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = main(["close-matching"])

        self.assertEqual(exit_code, 2)

    def test_extract_window_id_accepts_surf_json_string_message(self):
        self.assertEqual(
            extract_window_id("Window 1009098599 (tab 1009098600)\nUse --window-id 1009098599 to target this window"),
            1009098599,
        )


if __name__ == "__main__":
    unittest.main()
