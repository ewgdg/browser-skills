"""In-memory capture of the API responses each managed page receives.

Bodies stay in bridge memory only: bodies and headers can carry tokens.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from patchright.async_api import Error as PlaywrightError

MAX_ENTRIES_PER_PAGE = 200
MAX_BODY_BYTES_PER_PAGE = 32 * 1024 * 1024
# A larger body keeps its metadata only, so one huge response cannot evict the rest.
MAX_BODY_BYTES = 4 * 1024 * 1024
# Long-poll and streaming bodies complete late or never; list them as pending.
BODY_SETTLE_TIMEOUT_S = 2.0
SHAPE_MAX_DEPTH = 6
SHAPE_MAX_KEYS = 40

STATIC_RESOURCE_TYPES = frozenset({"document", "stylesheet", "image", "media", "font", "script", "texttrack", "manifest"})
# Same content and telemetry filters OpenCLI applies to its `browser network` listing.
API_CONTENT_TYPE = re.compile(r"json|xml|text/plain|javascript|text/x-component", re.IGNORECASE)
TELEMETRY_URL = re.compile(r"analytics|tracking|telemetry|beacon|pixel|gtag|fbevents", re.IGNORECASE)
JSON_CONTENT_TYPE = re.compile(r"[/+]json\b", re.IGNORECASE)
CHARSET = re.compile(r"charset=([\w.-]+)", re.IGNORECASE)


# eq=False: entries are looked up by identity in the page's deque.
@dataclass(eq=False)
class CapturedResponse:
    key: str
    method: str
    status: int
    url: str
    content_type: str
    size: int | None = None
    body: Any = None
    # Set when the body is not kept: "too_large" or "unavailable: <reason>".
    body_omitted: str | None = None
    body_settled: bool = False

    def summary(self) -> dict[str, Any]:
        row = {
            "key": self.key,
            "method": self.method,
            "status": self.status,
            "url": self.url,
            "content_type": self.content_type,
            "size": self.size,
        }
        if not self.body_settled:
            row["body_omitted"] = "pending"
        elif self.body_omitted:
            row["body_omitted"] = self.body_omitted
        else:
            row["shape"] = body_shape(self.body)
        return row


@dataclass
class PageCapture:
    entries: deque[CapturedResponse] = field(default_factory=deque)
    body_bytes: int = 0
    next_key: int = 1
    # Wall-clock ms; responses to requests started earlier belong to the previous document.
    started_at_ms: float = 0.0

    def clear(self) -> None:
        self.entries.clear()
        self.body_bytes = 0
        self.started_at_ms = time.time() * 1000

    def add(self, entry: CapturedResponse) -> None:
        self.entries.append(entry)
        while len(self.entries) > MAX_ENTRIES_PER_PAGE:
            self._evict_oldest()

    def account_body(self, size: int) -> None:
        self.body_bytes += size
        while self.body_bytes > MAX_BODY_BYTES_PER_PAGE and len(self.entries) > 1:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        evicted = self.entries.popleft()
        if not evicted.body_omitted and evicted.size:
            self.body_bytes -= evicted.size


class NetworkCapture:
    """Keeps recent API responses per page; attach once to the browser context."""

    def __init__(self) -> None:
        self._pages: dict[Any, PageCapture] = {}
        self._pending: set[asyncio.Task[None]] = set()

    def attach(self, context: Any) -> None:
        # Context-level: a page created through raw CDP starts loading before the
        # bridge holds its Page object, so a page-level listener would miss that load.
        context.on("response", self._on_response)

    def clear(self, page: Any) -> None:
        self._capture(page).clear()

    async def responses(self, page: Any) -> list[dict[str, Any]]:
        await self._settle(page)
        return [entry.summary() for entry in self._capture(page).entries]

    async def response(self, page: Any, key: str) -> CapturedResponse:
        await self._settle(page)
        for entry in self._capture(page).entries:
            if entry.key == key:
                return entry
        raise KeyError(key)

    def _on_response(self, response: Any) -> None:
        request = response.request
        content_type = response.headers.get("content-type", "")
        if (
            request.resource_type in STATIC_RESOURCE_TYPES
            or not API_CONTENT_TYPE.search(content_type)
            or TELEMETRY_URL.search(response.url)
            # Service worker requests have no page to attribute them to.
            or request.service_worker is not None
        ):
            return
        try:
            page = request.frame.page
        except PlaywrightError:
            # Responses of not-yet-initialized pages arrive without a page. A listener
            # exception would resurface on the next unrelated Patchright call.
            return
        capture = self._capture(page)
        if request.timing.get("startTime", 0) < capture.started_at_ms:
            return
        entry = CapturedResponse(
            key=f"r{capture.next_key}",
            method=request.method,
            status=response.status,
            url=response.url,
            content_type=content_type,
        )
        capture.next_key += 1
        capture.add(entry)
        # Read now: Chrome drops bodies once the page navigates away.
        task = asyncio.create_task(self._read_body(capture, entry, response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _read_body(self, capture: PageCapture, entry: CapturedResponse, response: Any) -> None:
        try:
            await self._read_body_into(capture, entry, response)
        finally:
            entry.body_settled = True

    async def _read_body_into(self, capture: PageCapture, entry: CapturedResponse, response: Any) -> None:
        if entry not in capture.entries:
            return  # evicted or cleared before its read started
        declared_size = response.headers.get("content-length")
        if declared_size and declared_size.isdigit() and int(declared_size) > MAX_BODY_BYTES:
            entry.size = int(declared_size)
            entry.body_omitted = "too_large"
            return
        try:
            raw = await response.body()
        except Exception as exc:
            # Redirects, aborted loads and evicted resources have no body to return.
            entry.body_omitted = f"unavailable: {exc}"
            return
        if len(raw) > MAX_BODY_BYTES:
            entry.size = len(raw)
            entry.body_omitted = "too_large"
            return
        entry.body = decode_body(raw, entry.content_type)
        entry.size = len(raw)
        if entry in capture.entries:
            capture.account_body(entry.size)

    async def _settle(self, page: Any) -> None:
        # The bridge loop is idle between calls, so response events queue in the
        # driver pipe. The pipe is FIFO: one round-trip dispatches every queued
        # event (spawning its body read) before the reply resolves.
        await page.title()
        if self._pending:
            await asyncio.wait(self._pending, timeout=BODY_SETTLE_TIMEOUT_S)

    def _capture(self, page: Any) -> PageCapture:
        for known in [known for known in self._pages if known.is_closed()]:
            del self._pages[known]
        return self._pages.setdefault(page, PageCapture())


def decode_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode(_charset(content_type), errors="replace")
    if JSON_CONTENT_TYPE.search(content_type):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _charset(content_type: str) -> str:
    match = CHARSET.search(content_type)
    if match:
        try:
            return codecs.lookup(match.group(1)).name
        except LookupError:
            pass  # servers send names Python lacks, such as x-user-defined
    return "utf-8"


def body_shape(value: Any, depth: int = 0) -> Any:
    """Compact structure preview: keys and value types, first list item as the sample."""
    if isinstance(value, dict):
        if depth >= SHAPE_MAX_DEPTH:
            return f"dict[{len(value)}]"
        keys = list(value)[:SHAPE_MAX_KEYS]
        shape = {key: body_shape(value[key], depth + 1) for key in keys}
        if len(value) > len(keys):
            shape["…"] = f"+{len(value) - len(keys)} keys"
        return shape
    if isinstance(value, list):
        if depth >= SHAPE_MAX_DEPTH or not value:
            return f"list[{len(value)}]"
        return [body_shape(value[0], depth + 1)]
    if value is None:
        return "null"
    if isinstance(value, str) and depth == 0:
        return f"text[{len(value)}]"
    return type(value).__name__
