from __future__ import annotations

from typing import Callable, Protocol
from uuid import uuid4
from urllib.parse import urlencode, urlsplit

from surf_agent.cli import surf_agent_state_dir
from surf_agent.pacing import Pacer as SurfPacer

from .browser_port import (
    BrowserPagePort,
    BrowserUnavailable,
    PageObservationError,
    SearchPageKind,
    SurfBrowserPagePort,
    selected_surf_profile_path,
)
from .contracts import CommandOutcome, SearchRequest
from .coordination import (
    NoopSearchCoordinator,
    SearchCoordinator,
    coordinator_for_profile,
)
from .errors import PublicError, PublicErrorType
from .urls import InvalidDestinationUrl, clean_destination_url


GOOGLE_SEARCH_URL = "https://www.google.com/search"
GOOGLE_PAGE_SIZE = 10


class Pacer(Protocol):
    def pause(self) -> None: ...


def create_search_lifecycle() -> BrowserSearchLifecycle:
    return BrowserSearchLifecycle(
        SurfBrowserPagePort(),
        pacer=SurfPacer.for_name("natural"),
        thread_factory=lambda: f"surf-google-search-{uuid4().hex}",
        coordinator=coordinator_for_profile(
            selected_surf_profile_path(),
            state_root=surf_agent_state_dir(),
        ),
    )


class BrowserSearchLifecycle:
    def __init__(
        self,
        browser: BrowserPagePort,
        *,
        pacer: Pacer,
        thread_factory: Callable[[], str],
        coordinator: SearchCoordinator | None = None,
    ) -> None:
        self._browser = browser
        self._pacer = pacer
        self._thread_factory = thread_factory
        self._coordinator = coordinator or NoopSearchCoordinator()

    def search(self, request: SearchRequest) -> CommandOutcome:
        thread = request.thread or self._thread_factory()
        lease = self._coordinator.acquire()
        cleaned_up = False

        def cleanup() -> None:
            nonlocal cleaned_up
            if cleaned_up:
                return
            cleaned_up = True
            try:
                self._browser.close(thread)
            finally:
                lease.release()

        try:
            try:
                blocked_outcome = self._blocked_outcome(request)
            except BrowserUnavailable:
                return CommandOutcome.failure(
                    PublicError(PublicErrorType.BROWSER_UNAVAILABLE),
                    post_output_cleanup=lease.release,
                )
            except PageObservationError:
                return CommandOutcome.failure(
                    PublicError(PublicErrorType.UI_CHANGED),
                    post_output_cleanup=lease.release,
                )
            if blocked_outcome is not None:
                lease.release()
                return blocked_outcome
            outcome = self._search(request, thread, cleanup)
            if outcome.post_output_cleanup is None:
                lease.release()
            return outcome
        except BrowserUnavailable:
            return CommandOutcome.failure(
                PublicError(PublicErrorType.BROWSER_UNAVAILABLE),
                post_output_cleanup=cleanup,
            )
        except PageObservationError:
            return CommandOutcome.failure(
                PublicError(PublicErrorType.UI_CHANGED),
                post_output_cleanup=cleanup,
            )
        except PublicError as error:
            return CommandOutcome.failure(error, post_output_cleanup=cleanup)
        except BaseException:
            cleanup()
            raise

    def _blocked_outcome(self, request: SearchRequest) -> CommandOutcome | None:
        blocked_thread = self._coordinator.blocked_thread()
        if blocked_thread is None:
            return None
        if not self._browser.is_open(blocked_thread):
            self._coordinator.clear_block()
            return None
        try:
            observation = self._browser.observe(blocked_thread)
        except PageObservationError:
            return _human_intervention_outcome(blocked_thread)
        if observation.kind in {
            SearchPageKind.HUMAN_INTERVENTION,
            SearchPageKind.UNKNOWN,
        }:
            return _human_intervention_outcome(blocked_thread)
        self._coordinator.clear_block()
        if request.thread != blocked_thread:
            self._browser.close(blocked_thread)
        return None

    def _search(
        self,
        request: SearchRequest,
        thread: str,
        cleanup: Callable[[], None],
    ) -> CommandOutcome:
        next_url = _search_url(request.query, request.start_page)
        visited_pages = 0
        exhausted = False
        seen_urls: set[str] = set()
        results: list[dict[str, object]] = []

        for page_index in range(request.page_count):
            self._pacer.pause()
            self._browser.open(thread, next_url)
            observation = self._browser.observe(thread)
            visited_pages += 1
            if observation.kind is SearchPageKind.EXHAUSTED:
                exhausted = True
                break
            if observation.kind is SearchPageKind.HUMAN_INTERVENTION:
                self._coordinator.block(thread)
                return _human_intervention_outcome(thread)
            if observation.kind is not SearchPageKind.RESULTS:
                raise PublicError(PublicErrorType.UI_CHANGED)

            rank_base = (request.start_page + page_index - 1) * GOOGLE_PAGE_SIZE
            for index, result in enumerate(observation.results, start=1):
                try:
                    destination_url = clean_destination_url(result.url)
                except InvalidDestinationUrl as error:
                    raise PublicError(PublicErrorType.UI_CHANGED) from error
                if destination_url in seen_urls:
                    continue
                seen_urls.add(destination_url)
                results.append(
                    {
                        "rank": rank_base + index,
                        "title": result.title,
                        "url": destination_url,
                        "snippet": result.snippet,
                        "displayed_date": result.displayed_date,
                    }
                )

            if observation.next_url is None:
                exhausted = True
                break
            if page_index + 1 < request.page_count and not _is_google_search_url(
                observation.next_url
            ):
                raise PublicError(PublicErrorType.UI_CHANGED)
            next_url = observation.next_url

        return CommandOutcome.success(
            {
                "query": request.query,
                "pages": {
                    "start": request.start_page,
                    "requested": request.page_count,
                    "visited": visited_pages,
                },
                "results": results,
                "exhausted": exhausted,
            },
            post_output_cleanup=cleanup,
        )


def _human_intervention_outcome(thread: str) -> CommandOutcome:
    return CommandOutcome.failure(
        PublicError(PublicErrorType.HUMAN_INTERVENTION_REQUIRED),
        public_fields={
            "handoff": {
                "action": "complete_browser_challenge",
                "thread": thread,
            }
        },
    )


def _is_google_search_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"google.com", "www.google.com"}
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/search"
        )
    except ValueError:
        return False


def _search_url(query: str, page: int) -> str:
    parameters = urlencode(
        {
            "q": query,
            "start": (page - 1) * GOOGLE_PAGE_SIZE,
            "num": GOOGLE_PAGE_SIZE,
        }
    )
    return f"{GOOGLE_SEARCH_URL}?{parameters}"
