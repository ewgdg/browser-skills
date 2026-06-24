import unittest
from pathlib import Path

from surf_chatgpt.errors import SkillError
from surf_chatgpt.web_sessions import search_web_sessions


class FakeSurfRunner:
    def __init__(self, js_result):
        self.js_result = js_result
        self.commands = []
        self.js_code = ""

    def new(self, thread, timeout=30):
        self.commands.append((thread, ["new"]))
        return "created\n"

    def open(self, thread, url, timeout=30):
        self.commands.append((thread, ["open", url]))
        return "opened\n"

    def close(self, thread, timeout=10):
        self.commands.append((thread, ["close"]))
        return "closed\n"

    def wait(self, thread, duration_or_text, timeout=35):
        self.commands.append((thread, ["wait", duration_or_text]))
        return "waited\n"

    def eval_file(self, thread, path, timeout=30):
        self.commands.append((thread, ["eval", "--file", path]))
        self.js_code = Path(path).read_text(encoding="utf-8")
        return self.js_result


class WebSessionSearchTests(unittest.TestCase):
    def test_search_returns_compact_deduped_limited_sessions_and_closes_thread(self):
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
        thread = surf.commands[0][0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["query"], "alpha")
        self.assertEqual([item["id"] for item in result["sessions"]], ["a", "b"])
        self.assertEqual(surf.commands[0], (thread, ["new"]))
        self.assertEqual(surf.commands[1], (thread, ["open", "https://chatgpt.com/"]))
        self.assertEqual(surf.commands[-1], (thread, ["close"]))
        self.assertIn("async () =>", surf.js_code)
        self.assertIn("Search", surf.js_code)
        self.assertIn("/c/", surf.js_code)

    def test_search_no_results_returns_ok_with_warning(self):
        surf = FakeSurfRunner({"status": "ok", "sessions": []})
        result = search_web_sessions("not found", limit=5, surf=surf)
        self.assertEqual(result["sessions"], [])
        self.assertIn("no matching", result["warning"])
        self.assertEqual(surf.commands[-1][1], ["close"])

    def test_search_login_required_is_structured_and_closes_thread(self):
        surf = FakeSurfRunner({"status": "login_required"})
        with self.assertRaises(SkillError) as ctx:
            search_web_sessions("x", surf=surf)
        self.assertEqual(ctx.exception.type, "login_required")
        self.assertEqual(surf.commands[-1][1], ["close"])

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
