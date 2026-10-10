---
status: done
---

# Retire the AXI backend

## Goal

Remove the AXI (chrome-devtools-axi) backend and the backend-selection layer that only existed to choose between AXI and Patchright. Patchright becomes the sole runtime.

## Intention

AXI no longer earns its keep: new capabilities ship Patchright-only (`upload`, select-aware `fill`, actionability error codes), and AXI already rejected four of them. Keeping it costs maintenance and makes agent-facing docs branch for a path nobody selects.

## Decision

Weighted matrix (maintenance 30%, future-swap cost 25%, change risk 20%, API honesty 15%, doc clarity 10%): full retirement 4.35, AXI-only removal 3.55, full retirement plus collapsing the backend protocol 3.65. Chosen: full retirement.

- A future backend swap needs the seam (`BrowserBackend` protocol, `LocalBridgeBackend`), not the selector; keep the seam.
- Re-adding a selector later is ~70 lines recoverable from git history.

## Scope & Constraints

Remove:
- `backends/axi.py` and every re-export; `create_backend` dispatch.
- Selection: `SURF_AGENT_BACKEND`, persisted `backend` key handling, `Browser.backend()/set_backend()/reset_backend()`, `BackendInfo`, `SurfAgent(backend=...)`, `SurfAgent.backend`.
- AXI-only runtime: AXI bridge client, profile-open via debug port, `stop_axi_chrome_runtime`, `default_axi_env`, dedicated debug-port/class settings, `Thread.reset()`, AXI env vars (`SURF_AGENT_AXI_*`, `CHROME_DEVTOOLS_AXI_*`, `SURF_AGENT_CHROME_PROFILE_DIR`, `SURF_AGENT_CHROME_CLASS`, `SURF_AGENT_CHROME_DEBUG_PORT`).
- `ErrorCode.UNSUPPORTED` once nothing raises it.
- Docs: `skills/surf/docs/axi-backend.md`, `skills/surf/docs/backends.md`; links repointed to `patchright-backend.md`.

Keep:
- `BrowserBackend` protocol and `LocalBridgeBackend`/`LocalBridgeClient`.
- Profile location `profiles/chrome/` and `SURF_AGENT_PATCHRIGHT_*` overrides.
- Historical records (`plans/done/`, ADRs, benchmark reports) unchanged.

Stale config: a persisted `"backend"` key is ignored, not migrated. Patchright is the only runtime, so ignoring it changes nothing for `patchright` users; an AXI user whose AXI Chrome still holds the shared profile hits the existing profile-active startup error.

## Work Plan

1. Source: delete AXI module and selection layer; simplify runtime, browser, config, chrome_lifecycle, thread, constants.
2. Tests: delete AXI-only tests; rewrite selection tests that guarded shared behavior against Patchright.
3. Docs: skill docs, README, python-api.
4. Commit runtime change, then pin `skills/surf/runtime-revision`.

## Validation

- Full `uv run pytest` passes.
- `grep -ri axi` over `packages/`, `skills/`, `README.md` finds nothing beyond words like "maximum".
- One live Patchright open via the skill launcher (no Google search needed).

## Progress

- [x] Source
- [x] Tests
- [x] Docs
- [x] Commit + pin

## Surprises & Discoveries

- Patchright read its thread name through `agent.state_file.stem`, a leftover of AXI's per-thread state file; it now reads `agent.thread`.
- `AgentPage.backend` defaulted to `"axi"` and had no readers; removed.
- `browser_executable_family` stays: Patchright's manual profile open still proves the executable is Google Chrome.
- `backend_config_file()` renamed `config_file()`: it holds only the cookie source now.

## Outcomes & Retrospective

- `surf-agent` lost ~1,500 source lines; the suite went from 366 to 299 passing tests, the difference being AXI-only cases.
- Full `uv run pytest` green; one live Patchright open/text/close/stop run against an isolated `SURF_AGENT_HOME` passed.
