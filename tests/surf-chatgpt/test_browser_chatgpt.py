import unittest
from pathlib import Path

import surf_chatgpt.browser_chatgpt as browser_chatgpt
from surf_chatgpt.browser_chatgpt import ReusableAskOptions, _extract_int, ask_reusable_session
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
        self.tabs = []
        self.next_tab_id = 10
        self.sent = False
        self.snapshot_calls = 0
        self.commands = []
        self.js_events = []
        self.current_url = "https://chatgpt.com/c/new-session-id"

    def run_json(self, args, timeout=30, **kwargs):
        self.commands.append((None, list(args)))
        if args == ["window.new"]:
            tab_id = self.next_tab_id
            self.next_tab_id += 1
            self.tabs.append({"id": tab_id, "windowId": 99, "active": True, "url": "about:blank", "title": "surf-agent"})
            return {"success": True, "tabId": tab_id, "windowId": 99}
        if args == ["window.close", "99"]:
            return {"success": True}
        if args[0] == "tab.list":
            return {"tabs": self.tabs}
        raise AssertionError(f"unexpected command: {args}")

    def run_json_on_window(self, window_id, args, timeout=30):
        self.commands.append((f"window:{window_id}", list(args)))
        if args[0] == "navigate":
            return {"success": True}
        return self._handle_scoped_command(args)

    def run_json_on_tab(self, tab_id, args, timeout=30):
        self.commands.append((tab_id, list(args)))
        return self._handle_scoped_command(args)

    def _handle_scoped_command(self, args):
        if args[0] == "wait.load":
            return {"success": True}
        if args[0] != "js":
            raise AssertionError(f"unexpected scoped command: {args}")
        code = Path(args[2]).read_text(encoding="utf-8")
        if "hasPrompt" in code and "loginRequired" in code:
            self.js_events.append("status")
            return {"result": {"value": {"hasPrompt": True, "challenge": False, "loginRequired": False, "url": self.current_url}}}
        if "findModelButton" in code and ("desiredModelQuery" in code or "desiredThinking" in code):
            self.js_events.append("model")
            return {"result": {"value": {"ok": True, "selectedModel": "GPT-5.5 Pro" if 'const desiredModelQuery = "pro"' in code else None, "selectedThinking": "High" if 'const desiredThinking = "High"' in code else None}}}
        if "composer_missing" in code:
            self.js_events.append("inject")
            return {"result": {"value": {"ok": True, "textLength": 12}}}
        if "status: 'clicked'" in code:
            self.js_events.append("send")
            self.sent = True
            return {"result": {"value": {"status": "clicked"}}}
        if "location.href" in code:
            return {"result": {"value": self.current_url}}
        if "stopVisible" in code and "candidates" in code:
            self.snapshot_calls += 1
            self.js_events.append("snapshot")
            if not self.sent:
                return {"result": {"value": {"candidates": [], "stopVisible": False}}}
            return {
                "result": {
                    "value": {
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
                }
            }
        raise AssertionError("unexpected JS script")


class BrowserChatGPTSessionTests(unittest.TestCase):
    def test_extract_int_supports_surf_string_json_output(self):
        self.assertEqual(_extract_int("Created tab 1009095008: about:blank", "tabId"), 1009095008)
        self.assertEqual(_extract_int("Window 1009095011 (tab 1009095012)", "windowId"), 1009095011)
        self.assertEqual(_extract_int("Window 1009095011 (tab 1009095012)", "tabId"), 1009095012)

    def test_ephemeral_opens_unfocused_window_returns_session_and_closes(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="ephemeral", timeout=5, thinking_label="High"),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["policy"], "ephemeral")
        self.assertEqual(result["session"]["id"], "new-session-id")
        self.assertEqual(result["model"], "current")
        self.assertEqual(result["thinking"], "High")
        self.assertEqual(surf.commands[0][1], ["window.new"])
        self.assertEqual(surf.commands[1], ("window:99", ["navigate", "https://chatgpt.com/"]))
        self.assertNotIn("tab.new", [command[1][0] for command in surf.commands])
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])

    def test_ephemeral_closes_window_after_structured_failure(self):
        class LoginFake(FakeSurfRunner):
            def _handle_scoped_command(self, args):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": False, "challenge": False, "loginRequired": True}}}
                return super()._handle_scoped_command(args)

        surf = LoginFake()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(session_policy="ephemeral", timeout=5),
                surf=surf,
            )
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])

    def test_new_session_closes_by_default_and_returns_resulting_id_url(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["url"], "https://chatgpt.com/c/new-session-id")
        self.assertEqual(result["session"]["id"], "new-session-id")
        self.assertFalse(result["session"]["saved"])
        self.assertNotIn("name", result["session"])
        self.assertEqual(surf.commands[0][1], ["window.new"])
        self.assertEqual(surf.commands[1], ("window:99", ["navigate", "https://chatgpt.com/"]))
        self.assertEqual(result["session"]["window_id"], 99)
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])
        self.assertNotIn("tab.new", [command[1][0] for command in surf.commands])

    def test_new_session_keep_open_returns_window_id_without_closing(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(session_policy="new", start_new=True, timeout=5, keep_open=True),
            surf=surf,
        )
        self.assertEqual(result["session"]["window_id"], 99)
        self.assertNotIn(["window.close", "99"], [command[1] for command in surf.commands])

    def test_session_url_closes_by_default(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertTrue(result["session"]["reused"])
        self.assertIn(("window:99", ["navigate", "https://chatgpt.com/c/existing"]), surf.commands)
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])
        self.assertNotIn("tab.new", [command[1][0] for command in surf.commands])

    def test_session_url_keep_open_returns_window_id_without_closing(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5, keep_open=True),
            surf=surf,
        )
        self.assertEqual(result["session"]["window_id"], 99)
        self.assertNotIn(["window.close", "99"], [command[1] for command in surf.commands])

    def test_response_timeout_tracks_inactivity_not_total_elapsed_time(self):
        class StreamingFake(FakeSurfRunner):
            def __init__(self):
                super().__init__()
                self.parts = ["a", "ab", "abc", "abcd"]

            def _handle_scoped_command(self, args):
                if args[0] == "js":
                    code = Path(args[2]).read_text(encoding="utf-8")
                    if "stopVisible" in code and "candidates" in code and self.sent:
                        text = self.parts.pop(0) if self.parts else "abcd"
                        return {
                            "result": {
                                "value": {
                                    "candidates": [
                                        {
                                            "isAssistant": True,
                                            "text": text,
                                            "messageId": "m1",
                                            "hasFinishedActions": text == "abcd",
                                        }
                                    ],
                                    "stopVisible": text != "abcd",
                                }
                            }
                        }
                return super()._handle_scoped_command(args)

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
            def __init__(self):
                super().__init__()
                self.refreshes = 0

            def _handle_scoped_command(self, args):
                if args[0] == "tab.reload":
                    self.refreshes += 1
                    return {"success": True}
                if args[0] == "js":
                    code = Path(args[2]).read_text(encoding="utf-8")
                    if "stopVisible" in code and "candidates" in code and self.sent and self.refreshes == 0:
                        return {"result": {"value": {"candidates": [], "stopVisible": False}}}
                return super()._handle_scoped_command(args)

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
        self.assertIn("response_poll_refresh:idle_timeout", result["warnings"])

    def test_response_polling_timeout_refreshes_page_once(self):
        class SnapshotTimeoutFake(FakeSurfRunner):
            def __init__(self):
                super().__init__()
                self.refreshes = 0

            def _handle_scoped_command(self, args):
                if args[0] == "tab.reload":
                    self.refreshes += 1
                    return {"success": True}
                if args[0] == "js":
                    code = Path(args[2]).read_text(encoding="utf-8")
                    if "stopVisible" in code and "candidates" in code and self.sent and self.refreshes == 0:
                        raise SkillError("timeout", "surf command timed out after 15s")
                return super()._handle_scoped_command(args)

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
            def _handle_scoped_command(self, args):
                if args[0] == "js":
                    code = Path(args[2]).read_text(encoding="utf-8")
                    if code.strip() == "return location.href;":
                        raise SkillError("timeout", "surf command timed out after 10s")
                return super()._handle_scoped_command(args)

        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5),
            surf=UrlTimeoutFake(),
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["url"], "https://chatgpt.com/c/existing")
        self.assertIn("session_url_unavailable:timeout", result["warnings"])

    def test_window_id_reuses_existing_one_tab_window(self):
        surf = FakeSurfRunner()
        surf.tabs.append({"id": 77, "windowId": 99, "active": False, "url": "https://chatgpt.com/c/existing", "title": "ChatGPT"})
        surf.current_url = "https://chatgpt.com/c/existing"
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="window", window_id=99, timeout=5),
            surf=surf,
        )
        self.assertEqual(result["session"]["policy"], "window")
        self.assertEqual(result["session"]["tab_id"], 77)
        self.assertEqual(result["session"]["window_id"], 99)
        self.assertNotIn("window.new", [command[1][0] for command in surf.commands])
        self.assertNotIn(["window.close", "99"], [command[1] for command in surf.commands])

    def test_current_uses_active_chatgpt_tab(self):
        surf = FakeSurfRunner()
        surf.tabs.append({"id": 55, "windowId": 2, "active": True, "url": "https://chatgpt.com/c/current"})
        surf.current_url = "https://chatgpt.com/c/current"
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(session_policy="current", timeout=5),
            surf=surf,
        )
        self.assertEqual(result["session"]["tab_id"], 55)
        self.assertEqual(result["session"]["id"], "current")
        self.assertFalse(result["session"]["saved"])

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

    def test_session_model_unavailable_is_classified(self):
        class ModelMissingFake(FakeSurfRunner):
            def _handle_scoped_command(self, args):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": True, "challenge": False, "loginRequired": False, "url": self.current_url}}}
                if "findModelButton" in code and ("desiredModelQuery" in code or "desiredThinking" in code):
                    return {"result": {"value": {"ok": False, "reason": "thinking_missing", "available": ["Instant", "Medium"]}}}
                return super()._handle_scoped_command(args)

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
            ask_reusable_session(
                "follow up",
                ReusableAskOptions(session_policy="session", timeout=5),
                surf=surf,
            )
        self.assertEqual(ctx.exception.type, "invalid_args")
        self.assertEqual(surf.commands, [])

    def test_login_status_is_classified(self):
        class LoginFake(FakeSurfRunner):
            def _handle_scoped_command(self, args):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": False, "challenge": False, "loginRequired": True}}}
                return super()._handle_scoped_command(args)

        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(session_policy="new", start_new=True, timeout=5),
                surf=LoginFake(),
            )
        self.assertEqual(ctx.exception.type, "login_required")


if __name__ == "__main__":
    unittest.main()
