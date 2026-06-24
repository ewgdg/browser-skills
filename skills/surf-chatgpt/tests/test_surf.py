import subprocess
import unittest

from surf_chatgpt.errors import SkillError
from surf_chatgpt.surf import SurfRunner, unwrap_eval_text


class SurfWrapperTests(unittest.TestCase):
    def test_run_text_uses_surf_agent_thread(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command, ["surf-agent", "--thread", "chat", "open", "https://chatgpt.com/"])
            return subprocess.CompletedProcess(command, 0, stdout="opened\n", stderr="")

        output = SurfRunner(command_prefix=["surf-agent"], runner=fake_run).run_text(["open", "https://chatgpt.com/"], thread="chat")
        self.assertEqual(output, "opened\n")

    def test_eval_file_parses_result_prefix_json(self):
        def fake_run(command, **kwargs):
            self.assertEqual(command, ["surf-agent", "--thread", "chat", "eval", "--file", "/tmp/x.js"])
            return subprocess.CompletedProcess(command, 0, stdout='result: {"ok": true}\n', stderr="")

        data = SurfRunner(command_prefix=["surf-agent"], runner=fake_run).eval_file("chat", "/tmp/x.js")
        self.assertEqual(data, {"ok": True})

    def test_unwrap_eval_text_supports_strings_and_bare_text(self):
        self.assertEqual(unwrap_eval_text('result: "hello"\n'), "hello")
        self.assertEqual(unwrap_eval_text("result: plain text\n"), "plain text")
        self.assertIsNone(unwrap_eval_text(""))

    def test_nonzero_login_classified(self):
        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: ChatGPT login required")

        with self.assertRaises(SkillError) as ctx:
            SurfRunner(command_prefix=["surf-agent"], runner=fake_run).run_text(["state"])
        self.assertEqual(ctx.exception.type, "login_required")

    def test_invalid_json_like_eval_output_classified(self):
        with self.assertRaises(SkillError) as ctx:
            unwrap_eval_text("result: {not json}\n")
        self.assertEqual(ctx.exception.type, "parse_error")

    def test_missing_surf_agent_classified(self):
        def fake_run(command, **kwargs):
            raise FileNotFoundError("surf-agent")

        with self.assertRaises(SkillError) as ctx:
            SurfRunner(runner=fake_run).run_text(["state"])
        self.assertEqual(ctx.exception.type, "surf_unavailable")
        self.assertIn("surf-agent", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
