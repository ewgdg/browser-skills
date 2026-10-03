from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from patchright.async_api import Error as PlaywrightError

from surf_agent.backends.patchright import network_capture
from surf_agent.backends.patchright.network_capture import NetworkCapture


class FakePage:
    def is_closed(self) -> bool:
        return False

    async def title(self) -> str:
        return ""


def fake_response(page, url, *, body=b"{}", content_type="application/json", resource_type="fetch", started_ms=None):
    request = SimpleNamespace(
        method="GET",
        resource_type=resource_type,
        service_worker=None,
        frame=SimpleNamespace(page=page),
        timing={"startTime": time.time() * 1000 if started_ms is None else started_ms},
    )

    async def read_body():
        return body

    return SimpleNamespace(
        request=request, url=url, status=200, headers={"content-type": content_type}, body=read_body
    )


def capture_urls(capture: NetworkCapture, page, responses) -> list[str]:
    async def run():
        for response in responses:
            capture._on_response(response)
        return [entry["url"] for entry in await capture.responses(page)]

    return asyncio.run(run())


def test_keeps_api_responses_and_drops_static_and_telemetry():
    page = FakePage()
    urls = capture_urls(NetworkCapture(), page, [
        fake_response(page, "https://site.test/api/feed"),
        fake_response(page, "https://site.test/api/notes.txt", content_type="text/plain", resource_type="xhr"),
        fake_response(page, "https://site.test/app.js", content_type="application/javascript", resource_type="script"),
        fake_response(page, "https://site.test/logo.svg", content_type="image/svg+xml", resource_type="image"),
        fake_response(page, "https://site.test/", content_type="text/html", resource_type="document"),
        fake_response(page, "https://www.google-analytics.com/g/collect"),
    ])
    assert urls == ["https://site.test/api/feed", "https://site.test/api/notes.txt"]


def test_entry_cap_evicts_oldest(monkeypatch):
    monkeypatch.setattr(network_capture, "MAX_ENTRIES_PER_PAGE", 2)
    page = FakePage()
    urls = capture_urls(NetworkCapture(), page, [fake_response(page, f"https://site.test/api/{n}") for n in range(3)])
    assert urls == ["https://site.test/api/1", "https://site.test/api/2"]


def test_byte_cap_evicts_oldest_bodies(monkeypatch):
    monkeypatch.setattr(network_capture, "MAX_BODY_BYTES_PER_PAGE", 25)
    page = FakePage()
    body = json.dumps({"text": "x" * 4}).encode()  # 16 bytes; two exceed the cap
    urls = capture_urls(NetworkCapture(), page, [fake_response(page, f"https://site.test/api/{n}", body=body) for n in range(2)])
    assert urls == ["https://site.test/api/1"]


def test_oversized_body_keeps_metadata_only(monkeypatch):
    monkeypatch.setattr(network_capture, "MAX_BODY_BYTES", 4)
    page = FakePage()
    capture = NetworkCapture()
    capture_urls(capture, page, [fake_response(page, "https://site.test/api/big", body=b'{"a": 12345}')])
    entry = asyncio.run(capture.response(page, "r1"))
    assert (entry.body, entry.body_omitted, entry.size) == (None, "too_large", 12)


def test_clear_drops_responses_to_requests_from_the_previous_document():
    page = FakePage()
    capture = NetworkCapture()
    before_clear = time.time() * 1000 - 1
    capture.clear(page)
    urls = capture_urls(capture, page, [
        # Arrived late: its event is processed only after open() cleared the buffer.
        fake_response(page, "https://old.test/api/feed", started_ms=before_clear),
        fake_response(page, "https://new.test/api/feed"),
    ])
    assert urls == ["https://new.test/api/feed"]


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        ({"items": [{"id": 1, "tags": []}], "next": None}, {"items": [{"id": "int", "tags": "list[0]"}], "next": "null"}),
        ("plain text body", "text[15]"),
    ],
)
def test_body_shape_previews_structure(value, shape):
    assert network_capture.body_shape(value) == shape


def test_listing_does_not_wait_for_a_body_that_never_finishes(monkeypatch):
    # Long-poll and streaming responses fire their response event long before the body completes.
    monkeypatch.setattr(network_capture, "BODY_SETTLE_TIMEOUT_S", 0.05)
    page = FakePage()
    stalled = fake_response(page, "https://site.test/api/poll")

    async def never_finishes():
        await asyncio.Event().wait()

    stalled.body = never_finishes

    async def run():
        capture = NetworkCapture()
        capture._on_response(stalled)
        return await asyncio.wait_for(capture.responses(page), timeout=1)

    [entry] = asyncio.run(run())
    assert entry["body_omitted"] == "pending"


def test_response_without_an_attributable_frame_is_skipped_without_raising():
    page = FakePage()
    orphan = fake_response(page, "https://site.test/api/orphan")

    class FramelessRequest(SimpleNamespace):
        @property
        def frame(self):
            raise PlaywrightError("Frame for this navigation request is not available")

    orphan.request = FramelessRequest(**{key: value for key, value in vars(orphan.request).items() if key != "frame"})
    urls = capture_urls(NetworkCapture(), page, [orphan, fake_response(page, "https://site.test/api/feed")])
    assert urls == ["https://site.test/api/feed"]


def test_unknown_charset_decodes_as_utf8():
    page = FakePage()
    capture = NetworkCapture()
    capture_urls(capture, page, [
        fake_response(page, "https://site.test/api/x", body=b'{"a": 1}', content_type="application/json; charset=x-user-defined"),
    ])
    assert asyncio.run(capture.response(page, "r1")).body == {"a": 1}
