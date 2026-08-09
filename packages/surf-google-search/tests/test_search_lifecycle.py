from __future__ import annotations

from dataclasses import dataclass, field

from surf_google_search.browser_port import (
    BrowserUnavailable,
    ObservedOrganicResult,
    SearchPageKind,
    SearchPageObservation,
)
from surf_google_search.contracts import SearchRequest
from surf_google_search.search_lifecycle import BrowserSearchLifecycle


@dataclass
class RecordingBrowser:
    observation: SearchPageObservation
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def open(self, thread: str, url: str) -> None:
        self.calls.append(("open", thread, url))

    def observe(self, thread: str) -> SearchPageObservation:
        self.calls.append(("observe", thread, None))
        return self.observation

    def close(self, thread: str) -> None:
        self.calls.append(("close", thread, None))


@dataclass
class RecordingPacer:
    pauses: int = 0

    def pause(self) -> None:
        self.pauses += 1


@dataclass
class SequenceBrowser:
    observations: list[SearchPageObservation]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def open(self, thread: str, url: str) -> None:
        self.calls.append(("open", thread, url))

    def observe(self, thread: str) -> SearchPageObservation:
        self.calls.append(("observe", thread, None))
        return self.observations.pop(0)

    def close(self, thread: str) -> None:
        self.calls.append(("close", thread, None))


def test_success_holds_the_serialization_lease_through_cleanup() -> None:
    events: list[str] = []

    class RecordingLease:
        def release(self) -> None:
            events.append("release")

    class RecordingCoordinator:
        def acquire(self) -> RecordingLease:
            events.append("acquire")
            return RecordingLease()

        def blocked_thread(self) -> str | None:
            return None

        def block(self, thread: str) -> None:
            raise AssertionError("unexpected challenge")

        def clear_block(self) -> None:
            raise AssertionError("unexpected clear")

    @dataclass
    class EventBrowser(RecordingBrowser):
        def close(self, thread: str) -> None:
            events.append("close")
            super().close(thread)

    browser = EventBrowser(SearchPageObservation(kind=SearchPageKind.EXHAUSTED))
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
        coordinator=RecordingCoordinator(),
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert events == ["acquire"]
    assert outcome.post_output_cleanup is not None
    outcome.post_output_cleanup()
    assert events == ["acquire", "close", "release"]


def test_one_page_search_returns_structured_results_and_closes_after_output() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(
            kind=SearchPageKind.RESULTS,
            results=(
                ObservedOrganicResult(
                    title="patchright",
                    url="https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python",
                    snippet="Undetected Python version of Playwright.",
                    displayed_date=None,
                ),
            ),
            next_url="https://www.google.com/search?q=patchright&start=10&num=10",
        )
    )
    pacer = RecordingPacer()
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=pacer,
        thread_factory=lambda: "surf-google-search-random123",
    )

    outcome = lifecycle.search(SearchRequest(query="latest Patchright documentation"))

    assert outcome.to_public_json() == {
        "ok": True,
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
    assert pacer.pauses == 1
    assert browser.calls == [
        (
            "open",
            "surf-google-search-random123",
            "https://www.google.com/search?q=latest+Patchright+documentation&start=0&num=10",
        ),
        ("observe", "surf-google-search-random123", None),
    ]

    assert outcome.post_output_cleanup is not None
    outcome.post_output_cleanup()
    assert browser.calls[-1] == ("close", "surf-google-search-random123", None)


def test_page_span_follows_google_next_and_deduplicates_without_compacting_rank_slots() -> None:
    duplicate = ObservedOrganicResult(
        title="A",
        url="https://example.com/a",
        snippet="first",
        displayed_date=None,
    )
    browser = SequenceBrowser(
        observations=[
            SearchPageObservation(
                kind=SearchPageKind.RESULTS,
                results=(
                    duplicate,
                    ObservedOrganicResult("B", "https://example.com/b", "second", None),
                ),
                next_url="https://www.google.com/search?q=query&start=10&num=10",
            ),
            SearchPageObservation(
                kind=SearchPageKind.RESULTS,
                results=(
                    ObservedOrganicResult("A repeated", "https://example.com/a", "different", None),
                    ObservedOrganicResult("C", "https://example.com/c", "third", None),
                ),
                next_url=None,
            ),
        ]
    )
    pacer = RecordingPacer()
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=pacer,
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query", page_count=2))

    assert outcome.to_public_json() == {
        "ok": True,
        "query": "query",
        "pages": {"start": 1, "requested": 2, "visited": 2},
        "results": [
            {"rank": 1, "title": "A", "url": "https://example.com/a", "snippet": "first", "displayed_date": None},
            {"rank": 2, "title": "B", "url": "https://example.com/b", "snippet": "second", "displayed_date": None},
            {"rank": 12, "title": "C", "url": "https://example.com/c", "snippet": "third", "displayed_date": None},
        ],
        "exhausted": True,
    }
    assert pacer.pauses == 2
    assert browser.calls == [
        ("open", "thread-1", "https://www.google.com/search?q=query&start=0&num=10"),
        ("observe", "thread-1", None),
        ("open", "thread-1", "https://www.google.com/search?q=query&start=10&num=10"),
        ("observe", "thread-1", None),
    ]


def test_interruption_closes_the_search_thread_and_releases_the_lease() -> None:
    events: list[str] = []

    class InterruptingPacer:
        def pause(self) -> None:
            raise KeyboardInterrupt

    class Lease:
        def release(self) -> None:
            events.append("release")

    class Coordinator:
        def acquire(self) -> Lease:
            return Lease()

        def blocked_thread(self) -> str | None:
            return None

        def block(self, thread: str) -> None:
            raise AssertionError("unexpected challenge")

        def clear_block(self) -> None:
            raise AssertionError("unexpected clear")

    @dataclass
    class Browser(RecordingBrowser):
        def close(self, thread: str) -> None:
            events.append(f"close:{thread}")

    lifecycle = BrowserSearchLifecycle(
        Browser(SearchPageObservation(kind=SearchPageKind.EXHAUSTED)),
        pacer=InterruptingPacer(),
        thread_factory=lambda: "thread-1",
        coordinator=Coordinator(),
    )

    try:
        lifecycle.search(SearchRequest(query="query"))
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected interruption")

    assert events == ["close:thread-1", "release"]


def test_browser_unavailability_returns_terminal_operational_failure() -> None:
    @dataclass
    class UnavailableBrowser:
        close_calls: list[str] = field(default_factory=list)

        def open(self, thread: str, url: str) -> None:
            raise BrowserUnavailable

        def observe(self, thread: str) -> SearchPageObservation:
            raise AssertionError("unreachable")

        def close(self, thread: str) -> None:
            self.close_calls.append(thread)

    browser = UnavailableBrowser()
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json()["error"]["type"] == "browser_unavailable"
    assert outcome.post_output_cleanup is not None
    outcome.post_output_cleanup()
    assert browser.close_calls == ["thread-1"]


def test_gone_challenge_page_clears_stale_marker_and_searches_normally() -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    class StaleCoordinator:
        def acquire(self) -> Lease:
            return Lease()

        def blocked_thread(self) -> str | None:
            return "gone-thread"

        def block(self, thread: str) -> None:
            raise AssertionError("unexpected challenge")

        def clear_block(self) -> None:
            events.append("clear")

    @dataclass
    class StaleBrowser(RecordingBrowser):
        def is_open(self, thread: str) -> bool:
            events.append(f"state:{thread}")
            return False

    browser = StaleBrowser(SearchPageObservation(kind=SearchPageKind.EXHAUSTED))
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "new-thread",
        coordinator=StaleCoordinator(),
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json()["ok"] is True
    assert events == ["state:gone-thread", "clear"]
    assert browser.calls[0][0] == "open"


def test_resolved_profile_challenge_is_cleared_before_queued_search_runs() -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    class ResolvedCoordinator:
        def acquire(self) -> Lease:
            return Lease()

        def blocked_thread(self) -> str | None:
            return "resolved-thread"

        def block(self, thread: str) -> None:
            raise AssertionError("unexpected challenge")

        def clear_block(self) -> None:
            events.append("clear")

    @dataclass
    class ResolvedBrowser(SequenceBrowser):
        def is_open(self, thread: str) -> bool:
            return True

        def close(self, thread: str) -> None:
            events.append(f"close:{thread}")

    browser = ResolvedBrowser(
        observations=[
            SearchPageObservation(
                kind=SearchPageKind.RESULTS,
                results=(ObservedOrganicResult("Resolved", "https://example.com/resolved", None, None),),
                next_url=None,
            ),
            SearchPageObservation(kind=SearchPageKind.EXHAUSTED),
        ]
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "queued-thread",
        coordinator=ResolvedCoordinator(),
    )

    outcome = lifecycle.search(SearchRequest(query="queued query"))

    assert outcome.to_public_json()["ok"] is True
    assert events == ["clear", "close:resolved-thread"]
    assert browser.calls[1][0:2] == ("open", "queued-thread")


def test_blocked_thread_inspection_failure_preserves_the_challenge_state() -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    class Coordinator:
        def acquire(self) -> Lease:
            return Lease()

        def blocked_thread(self) -> str | None:
            return "blocked-thread"

        def block(self, thread: str) -> None:
            raise AssertionError("unexpected challenge")

        def clear_block(self) -> None:
            events.append("clear")

    @dataclass
    class UnavailableBrowser(RecordingBrowser):
        def is_open(self, thread: str) -> bool:
            raise BrowserUnavailable

        def close(self, thread: str) -> None:
            events.append(f"close:{thread}")

    lifecycle = BrowserSearchLifecycle(
        UnavailableBrowser(SearchPageObservation(kind=SearchPageKind.EXHAUSTED)),
        pacer=RecordingPacer(),
        thread_factory=lambda: "unused-thread",
        coordinator=Coordinator(),
    )

    outcome = lifecycle.search(
        SearchRequest(query="query", thread="blocked-thread")
    )

    assert outcome.to_public_json()["error"]["type"] == "browser_unavailable"
    assert outcome.post_output_cleanup is not None
    outcome.post_output_cleanup()
    assert events == ["release"]


def test_existing_profile_challenge_returns_handoff_without_navigating() -> None:
    events: list[str] = []

    class RecordingLease:
        def release(self) -> None:
            events.append("release")

    class BlockedCoordinator:
        def acquire(self) -> RecordingLease:
            events.append("acquire")
            return RecordingLease()

        def blocked_thread(self) -> str | None:
            return "blocked-thread"

        def block(self, thread: str) -> None:
            events.append(f"block:{thread}")

        def clear_block(self) -> None:
            events.append("clear")

    @dataclass
    class BlockedBrowser(RecordingBrowser):
        def is_open(self, thread: str) -> bool:
            events.append(f"state:{thread}")
            return True

    browser = BlockedBrowser(
        SearchPageObservation(kind=SearchPageKind.HUMAN_INTERVENTION)
    )
    pacer = RecordingPacer()
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=pacer,
        thread_factory=lambda: "new-thread",
        coordinator=BlockedCoordinator(),
    )

    outcome = lifecycle.search(SearchRequest(query="queued query"))

    assert outcome.to_public_json()["handoff"]["thread"] == "blocked-thread"
    assert pacer.pauses == 0
    assert events == ["acquire", "state:blocked-thread", "release"]
    assert all(call[0] != "open" for call in browser.calls)


def test_browser_challenge_preserves_thread_and_returns_handoff() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(kind=SearchPageKind.HUMAN_INTERVENTION)
    )
    blocked_threads: list[str] = []

    class ChallengeCoordinator:
        def acquire(self):
            class Lease:
                def release(self) -> None:
                    blocked_threads.append("released")
            return Lease()

        def blocked_thread(self) -> str | None:
            return None

        def block(self, thread: str) -> None:
            blocked_threads.append(thread)

        def clear_block(self) -> None:
            pass

    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "challenge-thread",
        coordinator=ChallengeCoordinator(),
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json() == {
        "ok": False,
        "error": {
            "type": "human_intervention_required",
            "message": "Google requires user intervention.",
            "hint": "Complete the browser action, then retry using the preserved thread.",
        },
        "handoff": {
            "action": "complete_browser_challenge",
            "thread": "challenge-thread",
        },
    }
    assert outcome.post_output_cleanup is None
    assert blocked_threads == ["challenge-thread", "released"]
    assert all(call[0] != "close" for call in browser.calls)


def test_affirmed_no_results_is_a_successful_exhausted_search() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(kind=SearchPageKind.EXHAUSTED)
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="no such result", page_count=3))

    assert outcome.to_public_json() == {
        "ok": True,
        "query": "no such result",
        "pages": {"start": 1, "requested": 3, "visited": 1},
        "results": [],
        "exhausted": True,
    }


def test_non_google_next_destination_fails_closed_and_closes_after_output() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(
            kind=SearchPageKind.RESULTS,
            results=(ObservedOrganicResult("A", "https://example.com/a", None, None),),
            next_url="https://attacker.example/search?q=query&start=10",
        )
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query", page_count=2))

    assert outcome.to_public_json() == {
        "ok": False,
        "error": {
            "type": "ui_changed",
            "message": "The required Google Search interface could not be identified.",
            "hint": "Update surf-google-search for the current Google interface before retrying.",
        },
    }
    assert outcome.post_output_cleanup is not None
    outcome.post_output_cleanup()
    assert browser.calls[-1] == ("close", "thread-1", None)


def test_destination_urls_are_cleaned_before_invocation_scoped_deduplication() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(
            kind=SearchPageKind.RESULTS,
            results=(
                ObservedOrganicResult(
                    "First",
                    "https://example.com/docs?topic=search&srsltid=tracking#install:~:text=long%20highlight",
                    "first",
                    None,
                ),
                ObservedOrganicResult(
                    "Duplicate through Google",
                    "https://www.google.com/url?sa=t&q=https%3A%2F%2Fexample.com%2Fdocs%3Ftopic%3Dsearch%23install&ved=abc",
                    "second",
                    None,
                ),
            ),
            next_url=None,
        )
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json()["results"] == [
        {
            "rank": 1,
            "title": "First",
            "url": "https://example.com/docs?topic=search#install",
            "snippet": "first",
            "displayed_date": None,
        }
    ]


def test_direct_google_navigation_destination_fails_closed() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(
            kind=SearchPageKind.RESULTS,
            results=(
                ObservedOrganicResult(
                    "Google navigation",
                    "https://www.google.com/search?q=other",
                    None,
                    None,
                ),
            ),
            next_url=None,
        )
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json()["error"]["type"] == "ui_changed"


def test_unusable_destination_url_fails_closed() -> None:
    browser = RecordingBrowser(
        SearchPageObservation(
            kind=SearchPageKind.RESULTS,
            results=(ObservedOrganicResult("Bad", "javascript:alert(1)", None, None),),
            next_url=None,
        )
    )
    lifecycle = BrowserSearchLifecycle(
        browser,
        pacer=RecordingPacer(),
        thread_factory=lambda: "thread-1",
    )

    outcome = lifecycle.search(SearchRequest(query="query"))

    assert outcome.to_public_json()["error"]["type"] == "ui_changed"
