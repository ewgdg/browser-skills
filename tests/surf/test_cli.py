from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from surf_agent.cli import SurfAgent, SurfAgentError, default_state_file, first_command, parse_agent_args, safe_thread_name


class FakeSubprocess:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.windows: list[dict] = []
        self.fail_window_list = False
        self.missing_surf = False

    def run(self, command, text=False, capture_output=False, check=False):
        if self.missing_surf:
            raise FileNotFoundError("surf")
        self.calls.append(list(command))
        if command[:3] == ["surf", "window.list", "--tabs"]:
            if self.fail_window_list:
                return Completed(1, "", "browser unavailable")
            return Completed(0, json.dumps({"windows": self.windows}), "")
        if command[:2] == ["surf", "window.new"]:
            self.windows = [{"id": 101, "tabCount": 1, "tabs": [{"id": 501}]}]
            return Completed(0, json.dumps({"id": 101}), "")
        return Completed(0, "OK", "")


class Completed:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class SurfAgentTests(unittest.TestCase):
    def test_first_command_skips_options(self) -> None:
        self.assertEqual(first_command(["--json", "page.read"]), "page.read")

    def test_creates_blank_window_then_runs_with_window_id(self) -> None:
        fake = FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            agent = SurfAgent(state_file=Path(tmp) / "state.json")
            code = agent.run_in_window(["go", "https://example.com"])
        self.assertEqual(code, 0)
        self.assertIn(["surf", "window.new", "--json"], fake.calls)
        self.assertIn(["surf", "--window-id", "101", "go", "https://example.com"], fake.calls)

    def test_thread_selects_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(default_state_file(thread="docs", state_dir=Path(tmp)), Path(tmp) / "docs.json")
            self.assertEqual(default_state_file(thread="agent-1.docs", state_dir=Path(tmp)), Path(tmp) / "agent-1.docs.json")

    def test_parse_thread_option(self) -> None:
        config, rest = parse_agent_args(["--thread", "docs", "page.read", "--compact"])
        self.assertEqual(config.thread, "docs")
        self.assertEqual(rest, ["page.read", "--compact"])

    def test_safe_thread_name_rejects_unsafe_names(self) -> None:
        self.assertEqual(safe_thread_name("agent-1.docs"), "agent-1.docs")
        with self.assertRaises(SurfAgentError):
            safe_thread_name("../weird/name")

    def test_state_prints_current_page(self) -> None:
        fake = FakeSubprocess()
        fake.windows = [{"id": 101, "tabCount": 1, "tabs": [{"id": 501, "active": True, "url": "https://example.com", "title": "Example"}]}]
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_file=state)
            output = StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="main")
        self.assertEqual(json.loads(output.getvalue()), {"thread": "main", "open": True, "window_id": 101, "tab_id": 501, "url": "https://example.com", "title": "Example"})

    def test_state_does_not_create_when_not_opened(self) -> None:
        fake = FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            agent = SurfAgent(state_file=Path(tmp) / "state.json")
            output = StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="main")
        self.assertEqual(json.loads(output.getvalue()), {"thread": "main", "open": False})
        self.assertEqual(fake.calls, [])

    def test_state_treats_stale_cache_as_missing_and_removes_file(self) -> None:
        fake = FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_file=state)
            output = StringIO()
            with redirect_stdout(output):
                agent.print_state(thread="main")
            state_exists = state.exists()
        self.assertEqual(json.loads(output.getvalue()), {"thread": "main", "open": False})
        self.assertFalse(state_exists)
        self.assertEqual(fake.calls, [["surf", "window.list", "--tabs", "--json"]])

    def test_state_preserves_file_when_window_list_fails(self) -> None:
        fake = FakeSubprocess()
        fake.fail_window_list = True
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_file=state)
            with self.assertRaises(SurfAgentError):
                agent.print_state(thread="main")
            state_exists = state.exists()
        self.assertTrue(state_exists)

    def test_list_reports_open_threads_and_silently_removes_stale_files(self) -> None:
        fake = FakeSubprocess()
        fake.windows = [{"id": 101, "tabCount": 1, "tabs": [{"id": 501, "active": True, "url": "https://example.com", "title": "Example"}]}]
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state_dir = Path(tmp)
            (state_dir / "main.json").write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            stale = state_dir / "stale.json"
            stale.write_text(json.dumps({"window_id": 202, "tab_id": 601}))
            agent = SurfAgent(state_dir=state_dir)
            output = StringIO()
            with redirect_stdout(output):
                agent.print_list()
            payload = json.loads(output.getvalue())
            stale_exists = stale.exists()
        self.assertEqual(payload, {"threads": [{"thread": "main", "open": True, "window_id": 101, "tab_id": 501, "url": "https://example.com", "title": "Example"}]})
        self.assertFalse(stale_exists)

    def test_list_preserves_files_when_window_list_fails(self) -> None:
        fake = FakeSubprocess()
        fake.fail_window_list = True
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "main.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_dir=Path(tmp))
            with self.assertRaises(SurfAgentError):
                agent.print_list()
            state_exists = state.exists()
        self.assertTrue(state_exists)

    def test_state_preserves_file_when_surf_missing(self) -> None:
        fake = FakeSubprocess()
        fake.missing_surf = True
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_file=state)
            with self.assertRaises(SurfAgentError):
                agent.print_state(thread="main")
            state_exists = state.exists()
        self.assertTrue(state_exists)

    def test_rejects_tab_new(self) -> None:
        fake = FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            agent = SurfAgent(state_file=Path(tmp) / "state.json")
            with self.assertRaises(SurfAgentError):
                agent.run_in_window(["tab.new", "https://example.com"])
        self.assertEqual(fake.calls, [])

    def test_rejects_assistant_commands(self) -> None:
        fake = FakeSubprocess()
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            agent = SurfAgent(state_file=Path(tmp) / "state.json")
            with self.assertRaises(SurfAgentError):
                agent.run_in_window(["chatgpt", "hello"])
        self.assertEqual(fake.calls, [])

    def test_rejects_multi_tab_agent_window(self) -> None:
        fake = FakeSubprocess()
        fake.windows = [{"id": 101, "tabCount": 2, "tabs": [{"id": 501}, {"id": 502}]}]
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run", fake.run):
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"window_id": 101, "tab_id": 501}))
            agent = SurfAgent(state_file=state)
            with self.assertRaises(SurfAgentError):
                agent.run_in_window(["page.read"])


if __name__ == "__main__":
    unittest.main()
