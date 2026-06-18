import unittest
from unittest.mock import patch

from surf_chatgpt.client import AskOptions, ask_chatgpt
from surf_chatgpt.errors import SkillError


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
            result = ask_chatgpt("question", AskOptions())
        self.assertEqual(result["answer"], "answer")
        reusable_options = mocked.call_args.args[1]
        self.assertEqual(reusable_options.session_policy, "ephemeral")
        self.assertEqual(result["session"]["policy"], "ephemeral")
        self.assertEqual(result["session"]["id"], "s1")

    def test_top_level_model_is_rejected_in_client_to_avoid_silent_ignore(self):
        with self.assertRaises(SkillError) as ctx:
            ask_chatgpt("question", AskOptions(model="pro", requested_model="pro"))
        self.assertEqual(ctx.exception.type, "invalid_args")


if __name__ == "__main__":
    unittest.main()
