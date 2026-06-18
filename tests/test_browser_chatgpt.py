import unittest
from pathlib import Path

from surf_chatgpt.browser_chatgpt import ReusableAskOptions, _extract_int, ask_reusable_session
from surf_chatgpt.errors import SkillError


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
        if args[0] == "tab.new":
            tab_id = self.next_tab_id
            self.next_tab_id += 1
            url = args[1]
            self.tabs.append({"id": tab_id, "windowId": 1, "active": True, "url": url})
            return {"success": True, "tabId": tab_id, "windowId": 1}
        if args == ["window.new", "https://chatgpt.com/", "--unfocused"]:
            tab_id = self.next_tab_id
            self.next_tab_id += 1
            self.tabs.append({"id": tab_id, "windowId": 99, "active": True, "url": "https://chatgpt.com/"})
            return {"success": True, "tabId": tab_id, "windowId": 99}
        if args == ["window.close", "99"]:
            return {"success": True}
        if args[0] == "tab.list":
            return {"tabs": self.tabs}
        raise AssertionError(f"unexpected command: {args}")

    def run_json_on_tab(self, tab_id, args, timeout=30):
        self.commands.append((tab_id, list(args)))
        if args[0] == "wait.load":
            return {"success": True}
        if args[0] != "js":
            raise AssertionError(f"unexpected tab command: {args}")
        code = Path(args[2]).read_text(encoding="utf-8")
        if "hasPrompt" in code and "loginRequired" in code:
            self.js_events.append("status")
            return {"result": {"value": {"hasPrompt": True, "challenge": False, "loginRequired": False, "url": self.current_url}}}
        if "desiredNorm" in code and "modelButtonSelectors" in code:
            self.js_events.append("model")
            return {"result": {"value": {"ok": True, "selected": "High"}}}
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
            ReusableAskOptions(mode="answer", session_policy="ephemeral", timeout=5, thinking_label="High"),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["policy"], "ephemeral")
        self.assertEqual(result["session"]["id"], "new-session-id")
        self.assertEqual(result["model"], "High")
        self.assertEqual(surf.commands[0][1], ["window.new", "https://chatgpt.com/", "--unfocused"])
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])

    def test_ephemeral_closes_window_after_structured_failure(self):
        class LoginFake(FakeSurfRunner):
            def run_json_on_tab(self, tab_id, args, timeout=30):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": False, "challenge": False, "loginRequired": True}}}
                return super().run_json_on_tab(tab_id, args, timeout)

        surf = LoginFake()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(mode="answer", session_policy="ephemeral", timeout=5),
                surf=surf,
            )
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertEqual(surf.commands[-1][1], ["window.close", "99"])

    def test_new_session_opens_home_and_returns_resulting_id_url(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(mode="answer", session_policy="new", start_new=True, timeout=5),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertEqual(result["session"]["url"], "https://chatgpt.com/c/new-session-id")
        self.assertEqual(result["session"]["id"], "new-session-id")
        self.assertFalse(result["session"]["saved"])
        self.assertNotIn("name", result["session"])
        self.assertEqual(surf.commands[0][1], ["tab.new", "https://chatgpt.com/"])

    def test_session_url_opens_supplied_conversation_url(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(mode="critique", session_policy="session", session_url="https://chatgpt.com/c/existing", timeout=5),
            surf=surf,
        )
        self.assertEqual(result["response"], "assistant answer")
        self.assertTrue(result["session"]["reused"])
        self.assertIn((None, ["tab.new", "https://chatgpt.com/c/existing"]), surf.commands)

    def test_current_uses_active_chatgpt_tab(self):
        surf = FakeSurfRunner()
        surf.tabs.append({"id": 55, "windowId": 2, "active": True, "url": "https://chatgpt.com/c/current"})
        surf.current_url = "https://chatgpt.com/c/current"
        result = ask_reusable_session(
            "follow up",
            ReusableAskOptions(mode="answer", session_policy="current", timeout=5),
            surf=surf,
        )
        self.assertEqual(result["session"]["tab_id"], 55)
        self.assertEqual(result["session"]["id"], "current")
        self.assertFalse(result["session"]["saved"])

    def test_session_model_level_is_selected_before_prompt(self):
        surf = FakeSurfRunner()
        result = ask_reusable_session(
            "normal user prompt",
            ReusableAskOptions(mode="answer", session_policy="new", start_new=True, timeout=5, thinking_label="High"),
            surf=surf,
        )
        self.assertEqual(result["model"], "High")
        self.assertLess(surf.js_events.index("model"), surf.js_events.index("inject"))

    def test_session_model_unavailable_is_classified(self):
        class ModelMissingFake(FakeSurfRunner):
            def run_json_on_tab(self, tab_id, args, timeout=30):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": True, "challenge": False, "loginRequired": False, "url": self.current_url}}}
                if "desiredNorm" in code and "modelButtonSelectors" in code:
                    return {"result": {"value": {"ok": False, "reason": "level_missing", "available": ["Instant", "Medium"]}}}
                return super().run_json_on_tab(tab_id, args, timeout)

        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(mode="answer", session_policy="new", start_new=True, timeout=5, thinking_label="High"),
                surf=ModelMissingFake(),
            )
        self.assertEqual(ctx.exception.type, "model_unavailable")

    def test_session_mode_without_url_fails_before_browser_use(self):
        surf = FakeSurfRunner()
        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "follow up",
                ReusableAskOptions(mode="answer", session_policy="session", timeout=5),
                surf=surf,
            )
        self.assertEqual(ctx.exception.type, "invalid_args")
        self.assertEqual(surf.commands, [])

    def test_login_status_is_classified(self):
        class LoginFake(FakeSurfRunner):
            def run_json_on_tab(self, tab_id, args, timeout=30):
                if args[0] == "wait.load":
                    return {"success": True}
                code = Path(args[2]).read_text(encoding="utf-8")
                if "hasPrompt" in code and "loginRequired" in code:
                    return {"result": {"value": {"hasPrompt": False, "challenge": False, "loginRequired": True}}}
                return super().run_json_on_tab(tab_id, args, timeout)

        with self.assertRaises(SkillError) as ctx:
            ask_reusable_session(
                "x",
                ReusableAskOptions(mode="answer", session_policy="new", start_new=True, timeout=5),
                surf=LoginFake(),
            )
        self.assertEqual(ctx.exception.type, "login_required")


if __name__ == "__main__":
    unittest.main()
