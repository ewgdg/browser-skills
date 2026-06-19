import io
import json
import unittest
from unittest.mock import patch

from surf_chatgpt import cli


class CliValidationTests(unittest.TestCase):
    def test_empty_stdin_error_is_structured(self):
        out = io.StringIO()
        code = cli.main(["ask", "--format", "json"], stdin=io.StringIO(""), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "empty_prompt")

    def test_prompt_shaping_flags_are_removed(self):
        for flag in ("--thread", "--mode", "--max-chars", "--max-words"):
            with self.subTest(flag=flag):
                out = io.StringIO()
                code = cli.main(["ask", flag, "x"], stdin=io.StringIO("x"), stdout=out)
                self.assertNotEqual(code, 0)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["error"]["type"], "invalid_args")
                self.assertIn("unrecognized arguments", payload["error"]["message"])

    def test_keep_open_requires_explicit_session_mode(self):
        out = io.StringIO()
        code = cli.main(["ask", "--keep-open"], stdin=io.StringIO("x"), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")
        self.assertIn("--keep-open requires", payload["error"]["message"])

    def test_keep_open_is_passed_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "session", "window_id": 99}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "abc", "--keep-open"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertTrue(options.keep_open)

    def test_window_id_is_passed_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "window", "window_id": 99}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--window-id", "99"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_policy, "window")
        self.assertEqual(options.window_id, 99)

    def test_session_id_is_normalized_and_passed_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "session", "url": "https://chatgpt.com/c/abc", "id": "abc"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "abc"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_url, "https://chatgpt.com/c/abc")

    def test_session_url_is_passed_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "session", "url": "https://chatgpt.com/c/abc", "id": "abc"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "https://chatgpt.com/c/abc"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_url, "https://chatgpt.com/c/abc")

    def test_session_without_subcommand_is_structured_invalid_args(self):
        out = io.StringIO()
        err = io.StringIO()
        code = cli.main(["session"], stdout=out, stderr=err)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")
        self.assertIn("session requires a subcommand: current or search", payload["error"]["message"])
        self.assertEqual(err.getvalue(), "")

    def test_session_current_returns_active_conversation(self):
        class FakeSurfRunner:
            def run_json(self, args, timeout=30):
                if args != ["tab.list"]:
                    raise AssertionError(f"unexpected command: {args}")
                return {
                    "tabs": [
                        {
                            "id": 42,
                            "windowId": 7,
                            "active": True,
                            "title": "Research chat",
                            "url": "https://chatgpt.com/c/session-123?model=gpt-5.5",
                        }
                    ]
                }

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["session"]["id"], "session-123")
        self.assertEqual(payload["session"]["url"], "https://chatgpt.com/c/session-123?model=gpt-5.5")
        self.assertEqual(payload["session"]["title"], "Research chat")
        self.assertEqual(payload["session"]["tab_id"], 42)
        self.assertEqual(payload["session"]["window_id"], 7)

    def test_session_current_returns_null_for_active_chatgpt_home(self):
        class FakeSurfRunner:
            def run_json(self, args, timeout=30):
                return {"tabs": [{"id": 5, "active": True, "title": "ChatGPT", "url": "https://chatgpt.com/"}]}

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIsNone(payload["session"])
        self.assertIn("not a conversation", payload["warning"])

    def test_session_current_returns_null_when_no_active_chatgpt_tab(self):
        class FakeSurfRunner:
            def run_json(self, args, timeout=30):
                return {"tabs": [{"id": 5, "active": True, "title": "Example", "url": "https://example.com/"}]}

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIsNone(payload["session"])
        self.assertIn("no active ChatGPT tab", payload["warning"])

    def test_session_search_uses_web_search_and_returns_sessions(self):
        fake = {
            "ok": True,
            "source": "external-chatgpt-via-surf",
            "query": "rust async",
            "sessions": [{"id": "abc", "url": "https://chatgpt.com/c/abc", "title": "Rust async"}],
        }
        with patch("surf_chatgpt.cli.search_web_sessions", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["session", "search", "rust async", "--limit", "3"], stdout=out)
        self.assertEqual(code, 0)
        mocked.assert_called_once_with("rust async", limit=3)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["sessions"][0]["id"], "abc")

    def test_session_search_text_mode_lists_sessions(self):
        fake = {
            "ok": True,
            "source": "external-chatgpt-via-surf",
            "query": "rust async",
            "sessions": [{"id": "abc", "url": "https://chatgpt.com/c/abc", "title": "Rust async"}],
        }
        with patch("surf_chatgpt.cli.search_web_sessions", return_value=fake):
            out = io.StringIO()
            code = cli.main(["session", "search", "rust async", "--format", "text"], stdout=out)
        self.assertEqual(code, 0)
        self.assertIn("abc\tRust async\thttps://chatgpt.com/c/abc", out.getvalue())

    def test_ephemeral_ask_uses_client_and_returns_json(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake):
            out = io.StringIO()
            code = cli.main(["ask"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["answer"], "ok")

    def test_ephemeral_thinking_high_passes_high_label_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--thinking", "high"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_policy, "ephemeral")
        self.assertEqual(options.thinking_label, "High")

    def test_new_thinking_high_passes_high_label_to_client_without_session(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "new"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--new", "--thinking", "high"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertIsNone(options.model_query)
        self.assertEqual(options.session_policy, "new")
        self.assertEqual(options.thinking_label, "High")
        self.assertEqual(options.requested_thinking, "high")

    def test_model_query_is_passed_to_client(self):
        fake = {"ok": True, "source": "external-chatgpt-via-surf", "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--model", "pro"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.model_query, "pro")

    def test_model_suffix_thinking_conflict_is_structured(self):
        out = io.StringIO()
        code = cli.main(["ask", "--model", "gpt-5.5:high", "--thinking", "medium"], stdin=io.StringIO("x"), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")

    def test_text_mode_success_is_labeled_and_compact(self):
        fake = {
            "ok": True,
            "source": "external-chatgpt-via-surf",
            "answer": "ok",
            "session": {"policy": "ephemeral", "window_id": 99},
        }
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake):
            out = io.StringIO()
            code = cli.main(["ask", "--format", "text"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        rendered = out.getvalue()
        self.assertIn("external ChatGPT via surf", rendered)
        self.assertIn("window_id=99", rendered)
        self.assertIn("ok", rendered)

    def test_text_mode_error_is_labeled(self):
        out = io.StringIO()
        code = cli.main(["ask", "--format", "text"], stdin=io.StringIO(""), stdout=out)
        self.assertNotEqual(code, 0)
        self.assertIn("external ChatGPT via surf error: empty_prompt", out.getvalue())


if __name__ == "__main__":
    unittest.main()
