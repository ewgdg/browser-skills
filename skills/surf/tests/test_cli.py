import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from surf_agent.cli import AgentWindow, SurfAgent, extract_window_id


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

    def test_extract_window_id_accepts_surf_json_string_message(self):
        self.assertEqual(
            extract_window_id("Window 1009098599 (tab 1009098600)\nUse --window-id 1009098599 to target this window"),
            1009098599,
        )


if __name__ == "__main__":
    unittest.main()
