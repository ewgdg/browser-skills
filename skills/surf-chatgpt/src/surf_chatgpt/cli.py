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
        description="Compact external ChatGPT consultation through surf browser automation.",
    )
    subparsers = parser.add_subparsers(dest="command", parser_class=JsonArgumentParser)

    ask = subparsers.add_parser("ask", help="Forward stdin to ChatGPT through surf. Defaults to ephemeral one-shot mode.")
    ask.add_argument("--ephemeral", action="store_true", help="Use a temporary controlled browser session. Default when no session option is given.")
    ask.add_argument("--session", help="Continue a ChatGPT session by conversation id or https://chatgpt.com/c/<id> URL.")
    ask.add_argument("--window-id", type=int, help="Continue in an existing one-tab surf window returned by --keep-open.")
    ask.add_argument("--new", action="store_true", help="Start a new ChatGPT session and return its session id/url.")
    ask.add_argument("--current", action="store_true", help="Use current active ChatGPT tab.")
    ask.add_argument("--keep-open", action="store_true", help="Keep the opened one-tab window open and return its window_id for follow-up.")
    ask.add_argument("--model", help="ChatGPT GPT-5.5 selector, e.g. gpt5.5:low, gpt5.5:medium, or gpt5.5:high. Top-level model tokens are rejected.")
    ask.add_argument("--thinking", choices=("low", "medium", "high"), help="ChatGPT GPT-5.5 thinking level. Maps low/medium/high to the web UI levels.")
    ask.add_argument("--timeout", type=int, default=2700, help="ChatGPT wait timeout in seconds. Default: 2700.")
    ask.add_argument("--format", choices=("json", "text"), default="json")

    session = subparsers.add_parser("session", help="Discover ChatGPT web sessions from the browser. No local alias state.")
    session_sub = session.add_subparsers(dest="session_command", parser_class=JsonArgumentParser)

    session_current = session_sub.add_parser("current", help="Return the active ChatGPT conversation id/url, if the active tab is a conversation.")
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
    if model_choice.surf_model_token:
        raise SkillError(
            "invalid_args",
            "top-level --model values are not supported by the controlled browser path; use --thinking low|medium|high or --model gpt5.5:<level>",
        )
    options = AskOptions(
        session_policy=session_policy,
        session_url=_normalize_session_url(args.session) if args.session else None,
        window_id=args.window_id,
        keep_open=args.keep_open,
        model=model_choice.surf_model_token,
        thinking_label=model_choice.thinking_label,
        requested_model=args.model,
        requested_thinking=args.thinking,
        timeout=args.timeout,
        start_new=args.new,
    )
    return ask_chatgpt(user_prompt, options)


def _session_policy(args: argparse.Namespace) -> str:
    if args.ephemeral or not (args.session or args.current or args.new or args.window_id):
        return "ephemeral"
    if args.window_id is not None:
        return "window"
    if args.current:
        return "current"
    if args.new:
        return "new"
    return "session"


def _validate_ask_args(args: argparse.Namespace) -> None:
    explicit_session = bool(args.session or args.current or args.new or args.window_id is not None or args.keep_open)
    if args.ephemeral and explicit_session:
        raise SkillError("invalid_args", "--ephemeral cannot be combined with --session, --window-id, --new, --current, or --keep-open")
    if args.keep_open and not (args.session or args.current or args.new or args.window_id is not None):
        raise SkillError("invalid_args", "--keep-open requires --session, --window-id, --new, or --current")
    session_modes = [bool(args.session), bool(args.current), bool(args.new), args.window_id is not None]
    if sum(session_modes) > 1:
        raise SkillError("invalid_args", "choose only one of --session, --window-id, --new, or --current")
    if args.window_id is not None and args.window_id <= 0:
        raise SkillError("invalid_args", "--window-id must be positive")
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
        return _current_session_result()
    if args.session_command == "search":
        return search_web_sessions(args.query, limit=args.limit)
    raise SkillError("invalid_args", "unknown session command")


def _current_session_result() -> dict[str, Any]:
    tabs = _tabs_from_surf(SurfRunner().run_json(["tab.list"], timeout=10))
    active_chatgpt = [tab for tab in tabs if tab.get("active") and _is_chatgpt_url(str(tab.get("url", "")))]
    if not active_chatgpt:
        return {
            "ok": True,
            "source": SOURCE_LABEL,
            "session": None,
            "warning": "no active ChatGPT tab found",
        }

    tab = active_chatgpt[0]
    url = str(tab.get("url", ""))
    session_id = _conversation_id_from_url(url)
    if session_id is None:
        return {
            "ok": True,
            "source": SOURCE_LABEL,
            "session": None,
            "warning": "active ChatGPT tab is not a conversation URL",
            "active_url": url,
            "title": tab.get("title"),
        }

    return {
        "ok": True,
        "source": SOURCE_LABEL,
        "session": {
            "id": session_id,
            "url": url,
            "title": tab.get("title"),
            "tab_id": _coerce_int(tab.get("id") or tab.get("tabId") or tab.get("tab_id")),
            "window_id": _coerce_int(tab.get("windowId") or tab.get("window_id")),
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


def _tabs_from_surf(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [tab for tab in data if isinstance(tab, dict)]
    if isinstance(data, dict):
        for key in ("tabs", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [tab for tab in value if isinstance(tab, dict)]
        nested = data.get("result")
        if isinstance(nested, dict):
            return _tabs_from_surf(nested)
    raise SkillError("parse_error", "surf tab.list JSON was not a tab list")


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
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
        window_suffix = f" | window_id={session.get('window_id')}" if session.get("window_id") is not None else ""
        print(f"external ChatGPT via surf | session={session.get('policy')}{window_suffix}", file=stdout)
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
    print(f"external ChatGPT via surf error: {error.get('type', 'unknown')}", file=stdout)
    print(error.get("message", "unknown error"), file=stdout)
    if error.get("hint"):
        print(f"hint: {error['hint']}", file=stdout)


if __name__ == "__main__":
    raise SystemExit(main())
