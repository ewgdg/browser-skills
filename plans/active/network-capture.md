# Capture page API responses (issue #24)

## Goal

Let agents read the JSON/text API responses a page fetched, instead of scraping the rendered page: list captured responses with a structure preview, then pull one full body by key.

## Intention

Feeds, lists and dashboards render from JSON the page already received. Reading that JSON is cheaper (no snapshot/scroll rounds) and exact (no truncated text, rounded counts, virtualized lists).

## Scope & Constraints

- Patchright bridge only; bodies live in bridge memory, never on disk (bodies and headers can carry tokens).
- Always on per thread: an agent learns a page is API-driven only after opening it, and `_new_page` creates the target with its URL already set, so an opt-in could not run before the first load.
- Cleared on every `open()` and when the thread's page is replaced or closed.
- Read-only: agents trigger requests by driving the UI; never replay captured endpoints.
- Out of scope: interception/rewriting, HAR export, WebSocket frames, calling endpoints from page JS.

## Design

- One context-level `response` listener, registered when the persistent context starts. Context-level because a page created through raw CDP `Target.createTarget` exists before the bridge has its `Page` object; a page-level listener attached afterwards could miss the first load.
- Buffers keyed by `Page`; a thread reads the buffer of its slot's page.
- The bridge's asyncio loop runs only during a call, so responses are processed when the next call runs. Kept bodies are read eagerly in tasks spawned from the listener; the listing call awaits pending reads before answering.
- Keep: `xhr`/`fetch` resource types, and any response whose content type is JSON or text-like. Drop static resource types (image, font, stylesheet, script, media) and obvious telemetry URLs.
- Caps per page: entry count and total body bytes (evict oldest); a body over the per-body limit keeps metadata only.
- Detection: Patchright already sends `Network.enable` for every page session (driver `coreBundle.js`, network manager session init); the listener only adds a client-side subscription, and `Network.getResponseBody` is invisible to page scripts.

## Work Plan

1. Live test: fixture page fetches JSON during load; `open(url)` as the thread's first call; listing shows it and the body comes back by key. See it fail, then implement.
2. Listing preview (shape) and body retrieval through backend and `Thread`.
3. Filtering, caps, clear-on-open; unit tests with fake responses.
4. Skill guidance and Python API docs.
5. Commit runtime change, then pin `skills/surf/runtime-revision`.

## Validation

- `SURF_TEST_LIVE_PATCHRIGHT=1 uv run pytest packages/surf-agent/tests/test_patchright_network_capture.py`
- Unit suite for surf-agent.

## Progress

- [x] Step 1: live test red (`unsupported Patchright command: responses`), then green with the context-level listener; the first load of a raw-CDP window is captured.
- [x] Step 2: `Thread.responses()` / `Thread.response_body(key)`; verified end to end through a real bridge process with an isolated `SURF_AGENT_HOME`.
- [x] Step 3: unit tests with fake responses (filters, entry and byte caps, oversized body, stale-document drop; mutation-checked).
- [x] Step 4: skill guidance, Python API reference, runtime note, README acceptance command.
- [ ] Step 5

## Surprises & Discoveries

- The bridge has no background event loop; Playwright events queue until the next call.
- Clearing on `open()` races queued events from the previous document; entries whose request started before the clear (`request.timing.startTime`, wall-clock ms) are dropped.
- Patchright's driver sends `Network.enable` unconditionally in its network manager session init, which settles the issue's detection question without a live probe.

## Decisions

- See issue #24 for the capture-start, read-only and memory-only decisions.
