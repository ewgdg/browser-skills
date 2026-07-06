import unittest
from pathlib import Path

import surf_chatgpt.browser_chatgpt as browser_chatgpt
from surf_chatgpt.browser_chatgpt import ReusableAskOptions, ask_reusable_session
from surf_chatgpt.errors import SkillError


class FakeTime:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeSurfRunner:
    def __init__(self):
        self.sent = False
        self.snapshot_calls = 0
        self.commands = []
        self.js_events = []
        self.current_url = "https://chatgpt.com/c/new-session-id"
        self.refreshes = 0

    def new(self, thread, timeout=30):
        self.commands.append((thread, ["new"]))
        return "created\n"

    def open(self, thread, url, timeout=30):
        self.commands.append((thread, ["open", url]))
        self.current_url = url if "/c/" in url else self.current_url
        return "opened\n"

    def close(self, thread, timeout=10):
        self.commands.append((thread, ["close"]))
        return "closed\n"

    def wait(self, thread, duration_or_text, timeout=35):
        self.commands.append((thread, ["wait", duration_or_text]))
        return "waited\n"

    def eval_file(self, thread, path, timeout=30):
        self.commands.append((thread, ["eval", "--file", path]))
        code = Path(path).read_text(encoding="utf-8")
        if not code.startswith("async () => {\nreturn"):
            raise AssertionError(code[:80])
        return self._handle_js(code)

    def _handle_js(self, code):
        if "hasPrompt" in code and "loginRequired" in code:
            self.js_events.append("status")
            return {"hasPrompt": True, "challenge": False, "loginRequired": False, "authenticated": True, "url": self.current_url}
        if "findModelButton" in code and ("desiredModelQuery" in code or "desiredThinking" in code):
            self.js_events.append("model")
            selected_model = None
            if 'const desiredModelQuery = "pro"' in code:
                selected_model = "GPT-5.5 Pro"
            if 'const desiredModelQuery = "latest"' in code:
                selected_model = "GPT-5.5"
            selected_thinking = None
            if 'const desiredThinking = "High"' in code:
                selected_thinking = "High"
            if 'const desiredThinking = "highest"' in code:
                selected_thinking = "Max"
            return {"ok": True, "selectedModel": selected_model, "selectedThinking": selected_thinking}
        if "composer_missing" in code:
            self.js_events.append("inject")
            return {"ok": True, "textLength": 12}
        if "status: 'clicked'" in code:
            self.js_events.append("send")
            self.sent = True
            return {"status": "clicked"}
        if "location.reload" in code:
            self.refreshes += 1
            return True
        if "location.href" in code:
            return self.current_url
        if "stopVisible" in code and "candidates" in code:
            self.snapshot_calls += 1
            self.js_events.append("snapshot")
            if not self.sent:
                return {"candidates": [], "stopVisible": False}
            return {
                "candidates": [
                    {
                        "isAssistant": True,
                        "text": "assistant answer",
                        "messageId": "m1",
                        "hasFinishedActions": True,
                    }
                ],
                "stopVisible": False,
            }
        raise AssertionError("unexpected JS script")


class BrowserChatGPTSessionTests(unittest.TestCase):
    def test_ephemeral_opens_thread_returns_session_and_closes(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="ephemeral", timeout=5, thinking_label="High"),
            surf=surf,
        )
        thread = result["session"]["thread"]
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["policy"], "ephemeral")
        self.assertEqual(result["session"]["id"], "new-session-id")
        self.assertEqual(result["thinking"], "High")
        self.assertEqual(surf.commands[0], (thread, ["new"]))
        self.assertEqual(surf.commands[1], (thread, ["open", "https://chatgpt.com/"]))
        self.assertEqual(surf.commands[-1], (thread, ["close"]))

    def test_ephemeral_closes_thread_after_structured_failure(self):
        class LoginFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"hasPrompt": False, "challenge": False, "loginRequired": True, "authenticated": False}
                return super()._handle_js(code)

        surf = LoginFake()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session("x", ReusableAskOptions(session_policy="ephemeral", timeout=5), surf=surf)
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertEqual(surf.commands[-1][1], ["close"])

    def test_logged_out_prompt_composer_still_requires_login_by_default(self):
        class LoggedOutComposerFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"hasPrompt": True, "challenge": False, "loginRequired": False, "authenticated": False, "loggedOut": True}
                return super()._handle_js(code)

        surf = LoggedOutComposerFake()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session("x", ReusableAskOptions(session_policy="ephemeral", timeout=5), surf=surf)
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertIn("logged-in", ctx.exception.message)
        self.assertEqual(surf.commands[-1][1], ["close"])

    def test_allow_logged_out_preserves_anonymous_chatgpt_path(self):
        class LoggedOutComposerFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"hasPrompt": True, "challenge": False, "loginRequired": False, "authenticated": False, "loggedOut": True}
                return super()._handle_js(code)

        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="ephemeral", timeout=5, allow_logged_out=True),
            surf=LoggedOutComposerFake(),
        )
        self.assertEqual(result["response"], "assistant answer")

    def test_allow_logged_out_cannot_select_account_models(self):
        surf = FakeSurfRunner()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(session_policy="ephemeral", timeout=5, allow_logged_out=True, model_query="pro"),
                surf=surf,
            )
        self.assertEqual(ctx.exception.type, "invalid_args")

    def test_new_session_closes_by_default_and_returns_thread(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5),
            surf=surf,
        )
        self.assertEqual(result["session"]["url"], "https://chatgpt.com/c/new-session-id")
        self.assertIn("thread", result["session"])
        self.assertEqual(result["session"]["thread"], result["session"]["thread_id"])
        self.assertEqual(surf.commands[-1][1], ["close"])

    def test_new_session_keep_open_returns_thread_without_closing(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5, keep_open=True),
            surf=surf,
        )
        self.assertIn("thread", result["session"])
        self.assertNotIn(["close"], [command[1] for command in surf.commands])

    def test_session_url_opens_new_thread_and_closes_by_default(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5),
            surf=surf,
        )
        self.assertTrue(result["session"]["reused"])
        thread = result["session"]["thread"]
        self.assertIn((thread, ["open", "https://chatgpt.com/c/existing"]), surf.commands)
        self.assertEqual(surf.commands[-1], (thread, ["close"]))

    def test_existing_thread_reuses_without_new_or_close(self):
        surf = FakeSurfRunner()
        surf.current_url = "https://chatgpt.com/c/existing"
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="thread", thread="chat-thread", timeout=5),
            surf=surf,
        )
        self.assertEqual(result["session"]["policy"], "thread")
        self.assertEqual(result["session"]["thread"], "chat-thread")
        self.assertNotIn(["new"], [command[1] for command in surf.commands])
        self.assertNotIn(["close"], [command[1] for command in surf.commands])

    def test_current_uses_default_main_thread(self):
        surf = FakeSurfRunner()
        surf.current_url = "https://chatgpt.com/c/current"
        result = ask_reusable_session("follow up", ReusableAskOptions(session_policy="current", timeout=5), surf=surf)
        self.assertEqual(result["session"]["thread"], "main")
        self.assertEqual(result["session"]["id"], "current")

    def test_response_timeout_tracks_inactivity_not_total_elapsed_time(self):
        class StreamingFake(FakeSurfRunner):
            def __init__(self):
                super().__init__()
                self.parts = ["a", "ab", "abc", "abcd"]

            def _handle_js(self, code):
                if "stopVisible" in code and "candidates" in code and self.sent:
                    text = self.parts.pop(0) if self.parts else "abcd"
                    return {"candidates": [{"isAssistant": True, "text": text, "messageId": "m1", "hasFinishedActions": text == "abcd"}], "stopVisible": text != "abcd"}
                return super()._handle_js(code)

        fake_time = FakeTime()
        original_time = browser_chatgpt.time
        browser_chatgpt.time = fake_time
        try:
            result = ask_reusable_session(
                "normal user prompt",
                ReusableAskOptions(session_policy="new", start_new=True, timeout=1),
                surf=StreamingFake(),
            )
        finally:
            browser_chatgpt.time = original_time
        self.assertEqual(result["response"], "abcd")
        self.assertGreater(fake_time.now - 1000.0, 1)

    def test_response_idle_timeout_refreshes_page_once(self):
        class IdleFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "stopVisible" in code and "candidates" in code and self.sent and self.refreshes == 0:
                    return {"candidates": [], "stopVisible": False}
                return super()._handle_js(code)

        fake_time = FakeTime()
        original_time = browser_chatgpt.time
        browser_chatgpt.time = fake_time
        try:
            result = ask_reusable_session(
                "normal user prompt",
                ReusableAskOptions(session_policy="new", start_new=True, timeout=2),
                surf=IdleFake(),
            )
        finally:
            browser_chatgpt.time = original_time
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["warnings"], ["response_poll_refresh:idle_timeout"])

    def test_response_polling_timeout_refreshes_page_once(self):
        class SnapshotTimeoutFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "stopVisible" in code and "candidates" in code and self.sent and self.refreshes == 0:
                    raise SkillError("timeout", "surf-agent command timed out after 15s")
                return super()._handle_js(code)

        surf = SnapshotTimeoutFake()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(surf.refreshes, 1)
        self.assertIn("response_poll_refresh:timeout", result["warnings"])

    def test_answer_survives_final_url_timeout(self):
        class UrlTimeoutFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "return location.href;" in code and "hasPrompt" not in code:
                    raise SkillError("timeout", "surf-agent command timed out after 10s")
                return super()._handle_js(code)

        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5),
            surf=UrlTimeoutFake(),
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["url"], "https://chatgpt.com/c/existing")
        self.assertIn("session_url_unavailable:timeout", result["warnings"])

    def test_session_model_and_thinking_are_selected_before_prompt(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5, model_query="pro", thinking_label="High"),
            surf=surf,
        )
        self.assertEqual(result["model"], "GPT-5.5 Pro")
        self.assertEqual(result["thinking"], "High")
        self.assertLess(surf.js_events.index("model"), surf.js_events.index("inject"))

    def test_latest_model_and_highest_thinking_request_reaches_selector(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5, model_query="latest", thinking_label="highest"),
            surf=surf,
        )
        self.assertEqual(result["model"], "GPT-5.5")
        self.assertEqual(result["thinking"], "Max")
        self.assertIn("model", surf.js_events)

    def test_highest_thinking_selector_does_not_rank_fixed_labels(self):
        source = browser_chatgpt._select_model_choice_js("latest", "highest")
        self.assertIn("firstAvailableThinkingItem", source)
        self.assertNotIn("thinkingRank", source)

    def test_status_script_checks_auth_session(self):
        source = browser_chatgpt._status_js()
        self.assertIn("/api/auth/session", source)
        self.assertIn("authenticated", source)
        self.assertIn("session.user || session.account", source)
        self.assertNotIn("session.expires", source)
        self.assertNotIn("hasConversationHistory", source)
        self.assertIn("const loginRequired = onLoginPage || (!hasPrompt && hasLoggedOutCta);", source)
        self.assertIn("loggedOut", source)
        self.assertIn(r"/\b(log in|sign up)\b/i", source)
        self.assertNotIn("\x08", source)

    def test_session_model_unavailable_is_classified(self):
        class ModelMissingFake(FakeSurfRunner):
            def _handle_js(self, code):
                if "findModelButton" in code and ("desiredModelQuery" in code or "desiredThinking" in code):
                    return {"ok": False, "reason": "thinking_missing", "available": ["Instant", "Medium"]}
                return super()._handle_js(code)

        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(session_policy="new", start_new=True, timeout=5, thinking_label="High"),
                surf=ModelMissingFake(),
            )
        self.assertEqual(ctx.exception.type, "model_unavailable")

    def test_session_mode_without_url_fails_before_browser_use(self):
        surf = FakeSurfRunner()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session("follow up", ReusableAskOptions(session_policy="session", timeout=5), surf=surf)
        self.assertEqual(ctx.exception.type, "invalid_args")
        self.assertEqual(surf.commands, [])


if __name__ == "__main__":
    unittest.main()
