import json
import subprocess
import unittest

from surf_chatgpt.errors import SkillError
from surf_chatgpt.surf import SurfRunner


class SurfWrapperTests(unittest.TestCase):
    def test_run_json_parses_response(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command, ["surf", "tab.list", "--json"])
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"tabs": []}), stderr="")

        data = SurfRunner(runner=fake_run).run_json(["tab.list"])
        self.assertEqual(data["tabs"], [])

    def test_run_json_on_tab_places_global_tab_id_before_command(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command[:3], ["surf", "--tab-id", "123"])
            self.assertEqual(command[3:5], ["js", "--file"])
            self.assertIn("--json", command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"result": {"value": "ok"}}), stderr="")

        data = SurfRunner(runner=fake_run).run_json_on_tab(123, ["js", "--file", "/tmp/x.js"])
        self.assertEqual(data["result"]["value"], "ok")

    def test_run_json_on_window_places_global_window_id_before_command(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command[:3], ["surf", "--window-id", "456"])
            self.assertEqual(command[3:5], ["navigate", "https://chatgpt.com/"])
            self.assertIn("--json", command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"success": True}), stderr="")

        data = SurfRunner(runner=fake_run).run_json_on_window(456, ["navigate", "https://chatgpt.com/"])
        self.assertTrue(data["success"])

    def test_nonzero_login_classified(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: ChatGPT login required")

        with self.assertRaises(SkillError) as ctx:
            SurfRunner(runner=fake_run).run_json(["tab.list"])
        self.assertEqual(ctx.exception.type, "login_required")

    def test_bad_json_classified(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="not json", stderr="")

        with self.assertRaises(SkillError) as ctx:
            SurfRunner(runner=fake_run).run_json(["tab.list"])
        self.assertEqual(ctx.exception.type, "parse_error")

    def test_missing_surf_classified(self):
        def fake_run(command, **kwargs):
            raise FileNotFoundError("surf")

        with self.assertRaises(SkillError) as ctx:
            SurfRunner(runner=fake_run).run_json(["tab.list"])
        self.assertEqual(ctx.exception.type, "surf_unavailable")


if __name__ == "__main__":
    unittest.main()
