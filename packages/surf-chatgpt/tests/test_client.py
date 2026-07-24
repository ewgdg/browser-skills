import unittest
from unittest.mock import patch

from surf_chatgpt.client import AskOptions, ask_chatgpt


class ClientTests(unittest.TestCase):
    def test_default_ephemeral_uses_controlled_browser_path(self):
        raw = {
            "response": "answer",
            "model": "current",
            "messageId": "m1",
            "tookMs": 12,
            "session": {"policy": "ephemeral", "id": "s1", "url": "https://chatgpt.com/c/s1", "reused": False},
        }
        with patch("surf_chatgpt.client.ask_reusable_session", return_value=raw) as mocked:
            result = ask_chatgpt("question\n", AskOptions())
        self.assertEqual(result["answer"], "answer")
        self.assertEqual(mocked.call_args.args[0], "question\n")
        self.assertNotIn("mode", result)
        reusable_options = mocked.call_args.args[1]
        self.assertEqual(reusable_options.session_policy, "ephemeral")
        self.assertEqual(reusable_options.pace, "natural")
        self.assertEqual(result["session"]["policy"], "ephemeral")
        self.assertEqual(result["session"]["id"], "s1")

    def test_pacing_opt_out_is_passed_to_browser_path_without_changing_output(self):
        raw = {"response": "answer", "model": "current", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.client.ask_reusable_session", return_value=raw) as mocked:
            result = ask_chatgpt("question", AskOptions(pace="none"))

        self.assertEqual(mocked.call_args.args[1].pace, "none")
        self.assertNotIn("pace", result)

    def test_model_query_is_passed_to_browser_path(self):
        raw = {"response": "answer", "model": "GPT-5.5 Pro", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.client.ask_reusable_session", return_value=raw) as mocked:
            result = ask_chatgpt("question", AskOptions(model_query="pro", requested_model="pro"))
        self.assertEqual(result["model"], "GPT-5.5 Pro")
        self.assertEqual(mocked.call_args.args[1].model_query, "pro")


if __name__ == "__main__":
    unittest.main()
