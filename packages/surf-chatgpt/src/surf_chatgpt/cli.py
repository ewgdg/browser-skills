from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any
from urllib.parse import urlparse

from . import SOURCE_LABEL
from .client import AskOptions, ask_chatgpt
from .errors import SkillError
from .models import normalize_model_choice
from .surf import SurfRunner
from .web_sessions import search_web_sessions


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # keep default --help behavior, structure parser failures
        raise SkillError("invalid_args", message, exit_code=2)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="surf-chatgpt",
        description="Compact external ChatGPT consultation through surf-agent browser automation.",
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=JsonArgumentParser)

    ask = subparsers.add_parser("ask", help="Forward stdin to ChatGPT through surf-agent. Defaults to ephemeral one-shot mode.")
    ask.add_argument("--ephemeral", action="store_true", help="Use a temporary controlled browser session. Default when no session option is given.")
    ask.add_argument("--session", help="Continue a ChatGPT session by conversation id or https://chatgpt.com/c/<id> URL.")
    ask.add_argument("--thread", help="Continue in an existing surf-agent thread returned by --keep-open.")
    ask.add_argument("--new", action="store_true", help="Start a new ChatGPT session and return its session id/url.")
    ask.add_argument("--current", action="store_true", help="Use the default surf-agent thread (main).")
    ask.add_argument("--keep-open", action="store_true", help="Keep the opened surf-agent thread open and return its thread for follow-up.")
    ask.add_argument("--model", help="ChatGPT model query, e.g. pro, gpt-5.5, gpt-5.5-pro, or gpt-5.4. Best available fuzzy match is selected from the web UI.")
    ask.add_argument("--thinking", choices=("low", "medium", "high"), help="ChatGPT thinking level. Maps low/medium/high to the web UI levels.")
    ask.add_argument("--timeout", type=int, default=2700, help="ChatGPT wait timeout in seconds. Default: 2700.")
    ask.add_argument("--format", choices=("json", "text"), default="json")

    session = subparsers.add_parser("session", help="Discover ChatGPT web sessions from the browser. No local alias state.")
    session_sub = session.add_subparsers(dest="session_command", parser_class=JsonArgumentParser)

    session_current = session_sub.add_parser("current", help="Return the ChatGPT conversation id/url for a surf-agent thread.")
    session_current.add_argument("--thread", default="main", help="surf-agent thread to inspect. Default: main.")
    session_current.add_argument("--format", choices=("json", "text"), default="json")

    session_search = session_sub.add_parser("search", help="Search real ChatGPT web sessions using ChatGPT's own search UI.")
    session_search.add_argument("query")
    session_search.add_argument("--limit", type=int, default=10, help="Maximum sessions to return. Default: 10.")
    session_search.add_argument("--format", choices=("json", "text"), default="json")

    return parser


def main(argv: list[str] | None = None, *, stdin: IO[str] | None = None, stdout: IO[str] | None = None, stderr: IO[str] | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SkillError as exc:
        _emit_error(exc, "json", stdout)
        return exc.exit_code

    if args.command is None:
        parser.print_help(stderr)
        return 2

    try:
        if args.command == "ask":
            result = _handle_ask(args, stdin)
            _emit(result, args.format, stdout)
            return 0
        if args.command == "session":
            if args.session_command is None:
                raise SkillError("invalid_args", "session requires a subcommand: current or search", exit_code=2)
            result = _handle_session(args)
            _emit(result, args.format, stdout)
            return 0
    except SkillError as exc:
        fmt = getattr(args, "format", "json")
        _emit_error(exc, fmt, stdout)
        return exc.exit_code
    return 2


def _handle_ask(args: argparse.Namespace, stdin: IO[str]) -> dict[str, Any]:
    _validate_ask_args(args)
    user_prompt = stdin.read()
    if not user_prompt.strip():
        raise SkillError("empty_prompt", "stdin prompt is empty")

    session_policy = _session_policy(args)
    model_choice = normalize_model_choice(args.model, args.thinking)
    options = AskOptions(
        session_policy=session_policy,
        session_url=_normalize_session_url(args.session) if args.session else None,
        thread=args.thread,
        keep_open=args.keep_open,
        model_query=model_choice.model_query,
        thinking_label=model_choice.thinking_label,
        requested_model=args.model,
        requested_thinking=args.thinking,
        timeout=args.timeout,
        start_new=args.new,
    )
    return ask_chatgpt(user_prompt, options)


def _session_policy(args: argparse.Namespace) -> str:
    if args.ephemeral or not (args.session or args.current or args.new or args.thread):
        return "ephemeral"
    if args.thread:
        return "thread"
    if args.current:
        return "current"
    if args.new:
        return "new"
    return "session"


def _validate_ask_args(args: argparse.Namespace) -> None:
    explicit_session = bool(args.session or args.current or args.new or args.thread or args.keep_open)
    if args.ephemeral and explicit_session:
        raise SkillError("invalid_args", "--ephemeral cannot be combined with --session, --thread, --new, --current, or --keep-open")
    if args.keep_open and not (args.session or args.current or args.new or args.thread):
        raise SkillError("invalid_args", "--keep-open requires --session, --thread, --new, or --current")
    session_modes = [bool(args.session), bool(args.current), bool(args.new), bool(args.thread)]
    if sum(session_modes) > 1:
        raise SkillError("invalid_args", "choose only one of --session, --thread, --new, or --current")
    if args.thread is not None and not args.thread.strip():
        raise SkillError("invalid_args", "--thread cannot be empty")
    if args.timeout <= 0:
        raise SkillError("invalid_args", "--timeout must be positive")


def _normalize_session_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise SkillError("invalid_args", "--session cannot be empty")
    if raw.startswith("http://") or raw.startswith("https://"):
        _validate_chatgpt_url(raw)
        return raw
    if "/" in raw or raw.startswith("."):
        raise SkillError("invalid_args", "--session must be a ChatGPT conversation id or chatgpt.com URL")
    return f"https://chatgpt.com/c/{raw}"


def _handle_session(args: argparse.Namespace) -> dict[str, Any]:
    if args.session_command == "current":
        return _current_session_result(args.thread)
    if args.session_command == "search":
        return search_web_sessions(args.query, limit=args.limit)
    raise SkillError("invalid_args", "unknown session command")


def _current_session_result(thread: str) -> dict[str, Any]:
    clean_thread = thread.strip()
    if not clean_thread:
        raise SkillError("invalid_args", "--thread cannot be empty", exit_code=2)
    runner = SurfRunner()
    url = runner.eval_code(clean_thread, "() => location.href", timeout=10)
    if not isinstance(url, str) or not _is_chatgpt_url(url):
        return {
            "ok": True,
            "source": SOURCE_LABEL,
            "session": None,
            "warning": "surf-agent thread is not on ChatGPT",
            "thread": clean_thread,
        }
    title = runner.eval_code(clean_thread, "() => document.title", timeout=5)
    session_id = _conversation_id_from_url(url)
    if session_id is None:
        return {
            "ok": True,
            "source": SOURCE_LABEL,
            "session": None,
            "warning": "surf-agent thread is not a conversation URL",
            "active_url": url,
            "title": title if isinstance(title, str) else None,
            "thread": clean_thread,
        }

    return {
        "ok": True,
        "source": SOURCE_LABEL,
        "session": {
            "id": session_id,
            "url": url,
            "title": title if isinstance(title, str) else None,
            "thread": clean_thread,
            "thread_id": clean_thread,
        },
    }


def _validate_chatgpt_url(url: str | None) -> None:
    if not url or not _is_chatgpt_url(url):
        raise SkillError("invalid_args", "session URL must be a chatgpt.com URL")


def _is_chatgpt_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"chatgpt.com", "www.chatgpt.com"}


def _conversation_id_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.hostname not in {"chatgpt.com", "www.chatgpt.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "c" and parts[1]:
        return parts[1]
    return None


def _emit(result: dict[str, Any], fmt: str, stdout: IO[str]) -> None:
    if fmt == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=stdout)
        return
    if result.get("ok") is False:
        _emit_error_dict(result, stdout)
        return
    if "answer" in result:
        session = result.get("session") or {}
        thread_suffix = f" | thread={session.get('thread')}" if session.get("thread") is not None else ""
        print(f"external ChatGPT via surf-agent | session={session.get('policy')}{thread_suffix}", file=stdout)
        print("---", file=stdout)
        print(result.get("answer", ""), file=stdout)
        return
    if "sessions" in result:
        sessions = result.get("sessions") or []
        if not sessions:
            print(result.get("warning", "no matching ChatGPT sessions found"), file=stdout)
        for item in sessions:
            print(f"{item.get('id')}\t{item.get('title') or '-'}\t{item.get('url')}", file=stdout)
        return
    if "session" in result:
        session = result.get("session")
        if session:
            print(f"{session.get('id')}\t{session.get('title') or '-'}\t{session.get('url')}", file=stdout)
        else:
            print(result.get("warning", "no active ChatGPT conversation"), file=stdout)
        return
    print(json.dumps(result, ensure_ascii=False), file=stdout)


def _emit_error(exc: SkillError, fmt: str, stdout: IO[str]) -> None:
    result = {"ok": False, "source": SOURCE_LABEL, "error": exc.to_dict()}
    if fmt == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=stdout)
    else:
        _emit_error_dict(result, stdout)


def _emit_error_dict(result: dict[str, Any], stdout: IO[str]) -> None:
    error = result.get("error") or {}
    print(f"external ChatGPT via surf-agent error: {error.get('type', 'unknown')}", file=stdout)
    print(error.get("message", "unknown error"), file=stdout)
    if error.get("hint"):
        print(f"hint: {error['hint']}", file=stdout)


if __name__ == "__main__":
    raise SystemExit(main())
