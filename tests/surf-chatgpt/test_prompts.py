import unittest

from surf_chatgpt.prompts import PromptContract, build_prompt


class PromptTests(unittest.TestCase):
    BRIDGE_LEAK_TERMS = (
        "pi",
        "surf",
        "local agent",
        "automation",
        "browser bridge",
        "external chatgpt",
        "chatgpt",
    )

    def assert_no_bridge_leakage(self, prompt: str):
        lowered = prompt.lower()
        for term in self.BRIDGE_LEAK_TERMS:
            self.assertNotIn(term, lowered)

    def test_ephemeral_prompt_is_fresh_and_mode_specific(self):
        prompt = build_prompt("Review this", PromptContract(mode="critique", session_policy="ephemeral", max_chars=500))
        self.assertIn("Treat this as a fresh request", prompt)
        self.assertIn("Critique the proposal", prompt)
        self.assertIn("Target <= 500 chars", prompt)
        self.assertIn("Review this", prompt)
        self.assert_no_bridge_leakage(prompt)

    def test_thread_prompt_allows_conversation_context_without_tooling_identity(self):
        prompt = build_prompt(
            "Follow up",
            PromptContract(mode="redteam", session_policy="thread", thread_name="research", max_chars=700, max_words=100),
        )
        self.assertIn("follow-up in this conversation", prompt)
        self.assertIn("earlier context", prompt)
        self.assertIn("<= 100 words", prompt)
        self.assertIn("Red-team", prompt)
        self.assertNotIn("research", prompt)
        self.assert_no_bridge_leakage(prompt)

    def test_all_modes_prompt_like_normal_user_without_bridge_identity(self):
        for mode in ("answer", "critique", "redteam", "plan-review"):
            with self.subTest(mode=mode):
                prompt = build_prompt("Need help", PromptContract(mode=mode, session_policy="ephemeral", max_chars=500))
                self.assertIn("Please help with the request below", prompt)
                self.assert_no_bridge_leakage(prompt)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            build_prompt("x", PromptContract(mode="bad", session_policy="ephemeral", max_chars=100))


if __name__ == "__main__":
    unittest.main()
