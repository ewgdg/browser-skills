from __future__ import annotations

import argparse
import json
import sys
from typing import IO, NoReturn, Protocol

from .contracts import CommandOutcome, ProcessExitCode, SearchRequest
from .errors import PublicError, PublicErrorType
from .search_lifecycle import create_search_lifecycle


class SearchLifecycle(Protocol):
    def search(self, request: SearchRequest) -> CommandOutcome: ...


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PublicError(PublicErrorType.INVALID_REQUEST)


def _positive_page(value: str) -> int:
    try:
        page = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("page must be positive") from error
    if page < 1:
        raise argparse.ArgumentTypeError("page must be positive")
    return page


def _page_count(value: str) -> int:
    count = _positive_page(value)
    if count > 3:
        raise argparse.ArgumentTypeError("page count must be between one and three")
    return count


def _thread(value: str) -> str:
    normalized = value.strip()
    allowed = normalized and all(
        character.isalnum() or character in {"-", "_", "."}
        for character in normalized
    )
    if not allowed or normalized in {".", ".."} or normalized.startswith("."):
        raise argparse.ArgumentTypeError("invalid Surf thread")
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="surf-google-search",
        description="Search rendered Google pages and return structured organic results.",
        allow_abbrev=False,
    )
    parser.add_argument("--page", type=_positive_page, default=1, metavar="N")
    parser.add_argument("--page-count", type=_page_count, default=1, metavar="N")
    parser.add_argument("--thread", type=_thread, metavar="SURF_THREAD")
    parser.add_argument(
        "query",
        metavar="QUERY",
        help="Search query. Use - to read stdin.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    lifecycle: SearchLifecycle | None = None,
) -> int:
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    _ = stderr
    try:
        args = build_parser().parse_args(argv)
        query_input = input_stream.read() if args.query == "-" else args.query
        query = query_input.strip()
        if not query:
            raise PublicError(PublicErrorType.INVALID_REQUEST)
        if lifecycle is None:
            lifecycle = create_search_lifecycle()
        outcome = lifecycle.search(
            SearchRequest(
                query=query,
                start_page=args.page,
                page_count=args.page_count,
                thread=args.thread,
            )
        )
    except PublicError as error:
        outcome = CommandOutcome.failure(
            error,
            exit_code=ProcessExitCode.INVALID_INPUT
            if error.type is PublicErrorType.INVALID_REQUEST
            else ProcessExitCode.OPERATIONAL_FAILURE,
        )
    except Exception:
        outcome = CommandOutcome.failure(PublicError(PublicErrorType.INTERNAL_ERROR))
    try:
        output_stream.write(
            json.dumps(
                outcome.to_public_json(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        output_stream.flush()
    finally:
        if outcome.post_output_cleanup is not None:
            try:
                outcome.post_output_cleanup()
            except Exception:
                # Cleanup cannot replace a committed public outcome or output error.
                pass
    return int(outcome.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
