import unittest
from pathlib import Path

from surf_chatgpt.errors import SkillError
from surf_chatgpt.web_sessions import _extract_int, search_web_sessions


class FakeSurfRunner:
    def __init__(self, js_result):
        self.js_result = js_result
        self.commands = []
        self.tab_commands = []
        self.js_code = ""

    def run_json(self, args, timeout=30):
        self.commands.append(list(args))
        if args == ["window.new", "https://chatgpt.com/", "--unfocused"]:
            return {"success": True, "tabId": 10, "windowId": 20}
        if args == ["window.close", "20"]:
            return {"success": True}
        raise AssertionError(f"unexpected command: {args}")

    def run_json_on_tab(self, tab_id, args, timeout=30):
        self.tab_commands.append((tab_id, list(args)))
        if args[0] == "wait.load":
            return {"success": True}
        if args[0] == "js":
            self.js_code = Path(args[2]).read_text(encoding="utf-8")
            return {"result": {"value": self.js_result}}
        raise AssertionError(f"unexpected tab command: {args}")


class WebSessionSearchTests(unittest.TestCase):
    def test_extract_int_supports_surf_window_string_json_output(self):
        text = "Window 1009095011 (tab 1009095012)\nUse --window-id 1009095011 to target this window"
        self.assertEqual(_extract_int(text, "windowId"), 1009095011)
        self.assertEqual(_extract_int(text, "tabId"), 1009095012)

    def test_search_returns_compact_deduped_limited_sessions_and_closes_window(self):
        surf = FakeSurfRunner(
            {
                "status": "ok",
                "sessions": [
                    {"id": "a", "url": "https://chatgpt.com/c/a", "title": "Alpha research"},
                    {"id": "a", "url": "https://chatgpt.com/c/a", "title": "Duplicate"},
                    {"id": "b", "url": "https://chatgpt.com/c/b", "title": "Beta plan"},
                ],
            }
        )
        result = search_web_sessions("alpha", limit=2, surf=surf)
        self.assertTrue(result["ok"])
        self.assertEqual(result["query"], "alpha")
        self.assertEqual([item["id"] for item in result["sessions"]], ["a", "b"])
        self.assertEqual(surf.commands[-1], ["window.close", "20"])
        self.assertIn("Search", surf.js_code)
        self.assertIn("/c/", surf.js_code)

    def test_search_no_results_returns_ok_with_warning(self):
        surf = FakeSurfRunner({"status": "ok", "sessions": []})
        result = search_web_sessions("not found", limit=5, surf=surf)
        self.assertEqual(result["sessions"], [])
        self.assertIn("no matching", result["warning"])
        self.assertEqual(surf.commands[-1], ["window.close", "20"])

    def test_search_login_required_is_structured_and_closes_window(self):
        surf = FakeSurfRunner({"status": "login_required"})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("x", surf=surf)
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertEqual(surf.commands[-1], ["window.close", "20"])

    def test_search_captcha_is_structured(self):
        surf = FakeSurfRunner({"status": "captcha_or_cloudflare"})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("x", surf=surf)
        self.assertEqual(ctx.exception.type, "captcha_or_cloudflare")

    def test_search_ui_missing_is_structured_without_page_text(self):
        surf = FakeSurfRunner({"status": "ui_missing", "reason": "search_input_missing"})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("x", surf=surf)
        self.assertEqual(ctx.exception.type, "ui_changed")
        self.assertIn("search_input_missing", ctx.exception.message)
        self.assertNotIn("Log in", ctx.exception.message)

    def test_search_rejects_bad_limit_before_opening_browser(self):
        surf = FakeSurfRunner({"status": "ok", "sessions": []})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("x", limit=0, surf=surf)
        self.assertEqual(ctx.exception.type, "invalid_args")
        self.assertEqual(surf.commands, [])

    def test_search_rejects_empty_query_before_opening_browser(self):
        surf = FakeSurfRunner({"status": "ok", "sessions": []})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("   ", surf=surf)
        self.assertEqual(ctx.exception.type, "invalid_args")
        self.assertEqual(surf.commands, [])


if __name__ == "__main__":
    unittest.main()
