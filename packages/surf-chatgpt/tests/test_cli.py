import io
import json
import unittest
from unittest.mock import patch

from surf_chatgpt import cli


SOURCE = "external-chatgpt-via-surf-agent"


class CliValidationTests(unittest.TestCase):
    def test_empty_stdin_error_is_structured(self):
        out = io.StringIO()
        code = cli.main(["ask", "--format", "json"], stdin=io.StringIO(""), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "empty_prompt")

    def test_keyboard_interrupt_is_structured_without_traceback(self):
        out = io.StringIO()
        err = io.StringIO()
        with patch("surf_chatgpt.cli.ask_chatgpt", side_effect=KeyboardInterrupt):
            code = cli.main(["ask"], stdin=io.StringIO("x"), stdout=out, stderr=err)

        self.assertEqual(code, 130)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "interrupted")
        self.assertEqual(err.getvalue(), "")
        self.assertNotIn("Traceback", out.getvalue())

    def test_positional_prompt_is_passed_to_client_and_ignores_stdin(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "argv prompt"], stdin=io.StringIO("stdin prompt"), stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(mocked.call_args.args[0], "argv prompt")

    def test_dash_prefixed_prompt_uses_argparse_separator(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--", "-dash prompt"], stdin=io.StringIO(""), stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(mocked.call_args.args[0], "-dash prompt")

    def test_prompt_shaping_flags_are_removed_except_thread_session_flag(self):
        for flag in ("--mode", "--max-chars", "--max-words"):
            with self.subTest(flag=flag):
                out = io.StringIO()
                code = cli.main(["ask", flag, "x"], stdin=io.StringIO("x"), stdout=out)
                self.assertNotEqual(code, 0)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["error"]["type"], "invalid_args")
                self.assertIn("unrecognized arguments", payload["error"]["message"])

    def test_allow_logged_out_cannot_be_combined_with_model_selection(self):
        out = io.StringIO()
        code = cli.main(["ask", "--allow-logged-out", "--model", "pro"], stdin=io.StringIO("x"), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")
        self.assertIn("--allow-logged-out", payload["error"]["message"])

    def test_keep_open_without_session_mode_implies_new_session(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "new", "thread": "t"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--keep-open"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_policy, "new")
        self.assertTrue(options.start_new)
        self.assertTrue(options.keep_open)

    def test_keep_open_is_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "session", "thread": "t"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "abc", "--keep-open"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertTrue(options.keep_open)

    def test_thread_is_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "thread", "thread": "chat"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--thread", "chat"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_policy, "thread")
        self.assertEqual(options.thread, "chat")

    def test_session_id_is_normalized_and_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "session", "url": "https://chatgpt.com/c/abc", "id": "abc"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "abc"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_url, "https://chatgpt.com/c/abc")

    def test_session_url_is_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "session", "url": "https://chatgpt.com/c/abc", "id": "abc"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--session", "https://chatgpt.com/c/abc"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_url, "https://chatgpt.com/c/abc")

    def test_window_id_is_not_retained(self):
        out = io.StringIO()
        code = cli.main(["ask", "--window-id", "99"], stdin=io.StringIO("x"), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")
        self.assertIn("unrecognized arguments", payload["error"]["message"])

    def test_session_without_subcommand_is_structured_invalid_args(self):
        out = io.StringIO()
        err = io.StringIO()
        code = cli.main(["session"], stdout=out, stderr=err)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")
        self.assertIn("session requires a subcommand: current or search", payload["error"]["message"])
        self.assertEqual(err.getvalue(), "")

    def test_session_current_returns_thread_conversation(self):
        class FakeSurfRunner:
            def eval_code(self, thread, code, timeout=30):
                if thread != "research":
                    raise AssertionError(thread)
                if "location.href" in code:
                    return "https://chatgpt.com/c/session-123?model=gpt-5.5"
                if "document.title" in code:
                    return "Research chat"
                raise AssertionError(code)

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current", "--thread", "research"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["session"]["id"], "session-123")
        self.assertEqual(payload["session"]["url"], "https://chatgpt.com/c/session-123?model=gpt-5.5")
        self.assertEqual(payload["session"]["title"], "Research chat")
        self.assertEqual(payload["session"]["thread"], "research")

    def test_session_current_returns_null_for_thread_chatgpt_home(self):
        class FakeSurfRunner:
            def eval_code(self, thread, code, timeout=30):
                return "https://chatgpt.com/" if "location.href" in code else "ChatGPT"

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIsNone(payload["session"])
        self.assertIn("not a conversation", payload["warning"])

    def test_session_current_returns_null_when_thread_not_chatgpt(self):
        class FakeSurfRunner:
            def eval_code(self, thread, code, timeout=30):
                return "https://example.com/"

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["session", "current"], stdout=out)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIsNone(payload["session"])
        self.assertIn("not on ChatGPT", payload["warning"])

    def test_session_search_uses_web_search_and_returns_sessions(self):
        fake = {"ok": True, "source": SOURCE, "query": "rust async", "sessions": [{"id": "abc", "url": "https://chatgpt.com/c/abc", "title": "Rust async"}]}
        with patch("surf_chatgpt.cli.search_web_sessions", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["session", "search", "rust async", "--limit", "3"], stdout=out)
        self.assertEqual(code, 0)
        mocked.assert_called_once_with("rust async", limit=3)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["sessions"][0]["id"], "abc")

    def test_session_search_text_mode_lists_sessions(self):
        fake = {"ok": True, "source": SOURCE, "query": "rust async", "sessions": [{"id": "abc", "url": "https://chatgpt.com/c/abc", "title": "Rust async"}]}
        with patch("surf_chatgpt.cli.search_web_sessions", return_value=fake):
            out = io.StringIO()
            code = cli.main(["session", "search", "rust async", "--format", "text"], stdout=out)
        self.assertEqual(code, 0)
        self.assertIn("abc\tRust async\thttps://chatgpt.com/c/abc", out.getvalue())

    def test_ephemeral_ask_uses_client_and_returns_json(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake):
            out = io.StringIO()
            code = cli.main(["ask"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["answer"], "ok")

    def test_ephemeral_thinking_high_passes_high_label_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--thinking", "high"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.session_policy, "ephemeral")
        self.assertEqual(options.thinking_label, "High")

    def test_new_thinking_high_passes_high_label_to_client_without_session(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "new"}}
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
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--model", "pro"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.model_query, "pro")

    def test_allow_logged_out_is_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--allow-logged-out"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertTrue(options.allow_logged_out)

    def test_login_opens_and_focuses_chatgpt_through_bridge(self):
        class FakeSurfRunner:
            def __init__(self):
                self.calls = []

            def run_text(self, args, timeout=30, thread=None):
                self.calls.append((list(args), timeout, thread))
                return "opened\n"

        fake_runner = FakeSurfRunner()
        with patch("surf_chatgpt.cli.SurfRunner", return_value=fake_runner):
            out = io.StringIO()
            code = cli.main(["login"], stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake_runner.calls,
            [
                (["open", "https://chatgpt.com/"], 30, "surf-chatgpt-login"),
                (["focus"], 10, "surf-chatgpt-login"),
            ],
        )
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "login_opened")

    def test_latest_model_and_highest_thinking_are_passed_to_client(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake) as mocked:
            out = io.StringIO()
            code = cli.main(["ask", "--model", "latest", "--thinking", "highest"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        options = mocked.call_args.args[1]
        self.assertEqual(options.model_query, "latest")
        self.assertEqual(options.thinking_label, "highest")
        self.assertEqual(options.requested_model, "latest")
        self.assertEqual(options.requested_thinking, "highest")

    def test_model_suffix_thinking_conflict_is_structured(self):
        out = io.StringIO()
        code = cli.main(["ask", "--model", "gpt-5.5:high", "--thinking", "medium"], stdin=io.StringIO("x"), stdout=out)
        self.assertNotEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["type"], "invalid_args")

    def test_text_mode_success_is_labeled_and_compact(self):
        fake = {"ok": True, "source": SOURCE, "answer": "ok", "session": {"policy": "ephemeral", "thread": "chat"}}
        with patch("surf_chatgpt.cli.ask_chatgpt", return_value=fake):
            out = io.StringIO()
            code = cli.main(["ask", "--format", "text"], stdin=io.StringIO("x"), stdout=out)
        self.assertEqual(code, 0)
        rendered = out.getvalue()
        self.assertIn("external ChatGPT via surf-agent", rendered)
        self.assertIn("thread=chat", rendered)
        self.assertIn("ok", rendered)

    def test_text_mode_error_is_labeled(self):
        out = io.StringIO()
        code = cli.main(["ask", "--format", "text"], stdin=io.StringIO(""), stdout=out)
        self.assertNotEqual(code, 0)
        self.assertIn("external ChatGPT via surf-agent error: empty_prompt", out.getvalue())

    def test_login_text_mode_tells_user_next_action(self):
        class FakeSurfRunner:
            def run_text(self, args, timeout=30, thread=None):
                return "opened\n"

        with patch("surf_chatgpt.cli.SurfRunner", return_value=FakeSurfRunner()):
            out = io.StringIO()
            code = cli.main(["login", "--format", "text"], stdout=out)
        self.assertEqual(code, 0)
        self.assertIn("Log in to ChatGPT", out.getvalue())

    def test_login_help_mentions_manual_login(self):
        out = io.StringIO()
        with patch("sys.stdout", out), self.assertRaises(SystemExit) as ctx:
            cli.main(["login", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("manual login", out.getvalue())


if __name__ == "__main__":
    unittest.main()
