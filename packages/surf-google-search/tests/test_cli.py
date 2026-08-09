from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from surf_google_search import cli
from surf_google_search.contracts import CommandOutcome, SearchRequest


SUCCESS = CommandOutcome.success(
    {
        "query": "latest Patchright documentation",
        "pages": {"start": 1, "requested": 1, "visited": 1},
        "results": [
            {
                "rank": 1,
                "title": "patchright",
                "url": "https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python",
                "snippet": "Undetected Python version of Playwright.",
                "displayed_date": None,
            }
        ],
        "exhausted": False,
    }
)


@dataclass
class RecordingLifecycle:
    outcome: CommandOutcome = SUCCESS
    calls: list[SearchRequest] = field(default_factory=list)

    def search(self, request: SearchRequest) -> CommandOutcome:
        self.calls.append(request)
        return self.outcome


def invoke(
    argv: list[str],
    *,
    lifecycle: RecordingLifecycle | None = None,
) -> tuple[int, dict[str, Any], str, RecordingLifecycle]:
    active_lifecycle = lifecycle or RecordingLifecycle()
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cli.main(
        argv,
        stdout=stdout,
        stderr=stderr,
        lifecycle=active_lifecycle,
    )
    return code, json.loads(stdout.getvalue()), stderr.getvalue(), active_lifecycle


def test_default_runtime_constructs_the_production_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle = RecordingLifecycle()
    monkeypatch.setattr(cli, "create_search_lifecycle", lambda: lifecycle)
    stdout = io.StringIO()

    code = cli.main(["query"], stdout=stdout)

    assert code == 0
    assert lifecycle.calls == [SearchRequest(query="query")]


def test_default_search_dispatches_one_typed_request_and_one_compact_json_object() -> None:
    code, payload, stderr, lifecycle = invoke(["latest Patchright documentation"])

    assert code == 0
    assert payload == SUCCESS.to_public_json()
    assert stderr == ""
    assert lifecycle.calls == [
        SearchRequest(
            query="latest Patchright documentation",
            start_page=1,
            page_count=1,
            thread=None,
        )
    ]

    output = io.StringIO()
    cli.main(
        ["latest Patchright documentation"],
        stdout=output,
        lifecycle=RecordingLifecycle(),
    )
    assert output.getvalue() == json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def test_unexpected_failure_returns_one_compact_internal_error() -> None:
    class RaisingLifecycle:
        def search(self, request: SearchRequest) -> CommandOutcome:
            raise RuntimeError("private detail")

    stdout = io.StringIO()
    code = cli.main(["query"], stdout=stdout, lifecycle=RaisingLifecycle())

    assert code == 1
    assert json.loads(stdout.getvalue()) == {
        "ok": False,
        "error": {
            "type": "internal_error",
            "message": "An internal surf-google-search error occurred.",
            "hint": "Retry once; if the failure persists, update surf-google-search.",
        },
    }
    assert "private detail" not in stdout.getvalue()


def test_cleanup_runs_after_success_json_is_flushed() -> None:
    events: list[str] = []

    class RecordingStream(io.StringIO):
        def flush(self) -> None:
            events.append("flush")
            super().flush()

    lifecycle = RecordingLifecycle(
        outcome=CommandOutcome.success(
            {"query": "q", "pages": {"start": 1, "requested": 1, "visited": 1}, "results": [], "exhausted": True},
            post_output_cleanup=lambda: events.append("cleanup"),
        )
    )

    cli.main(["q"], stdout=RecordingStream(), lifecycle=lifecycle)

    assert events == ["flush", "cleanup"]


def test_cleanup_runs_when_output_cannot_be_committed() -> None:
    events: list[str] = []

    class BrokenOutput(io.StringIO):
        def write(self, value: str) -> int:
            raise BrokenPipeError

    lifecycle = RecordingLifecycle(
        outcome=CommandOutcome.success(
            {
                "query": "q",
                "pages": {"start": 1, "requested": 1, "visited": 1},
                "results": [],
                "exhausted": True,
            },
            post_output_cleanup=lambda: events.append("cleanup"),
        )
    )

    with pytest.raises(BrokenPipeError):
        cli.main(["q"], stdout=BrokenOutput(), lifecycle=lifecycle)

    assert events == ["cleanup"]


def test_search_dispatches_the_requested_page_span_and_resume_thread() -> None:
    _, _, _, lifecycle = invoke(
        [
            "--page",
            "2",
            "--page-count",
            "3",
            "--thread",
            "surf-google-search-safe123",
            "site:github.com patchright",
        ]
    )

    assert lifecycle.calls == [
        SearchRequest(
            query="site:github.com patchright",
            start_page=2,
            page_count=3,
            thread="surf-google-search-safe123",
        )
    ]


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["   "],
        ["--page", "0", "query"],
        ["--page-count", "0", "query"],
        ["--page-count", "4", "query"],
        ["--thread", "../other", "query"],
    ),
)
def test_invalid_search_requests_return_one_content_free_json_error(argv: list[str]) -> None:
    code, payload, stderr, lifecycle = invoke(argv)

    assert code == 2
    assert payload == {
        "ok": False,
        "error": {
            "type": "invalid_request",
            "message": "The search request is invalid.",
            "hint": "Use --help to inspect the supported command grammar.",
        },
    }
    assert stderr == ""
    assert lifecycle.calls == []
