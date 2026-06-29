# Cleanup code-review slop

## Goal

Fix review findings without changing CLI behavior:

- Root `uv run pytest` works.
- Dev tooling declared for pytest/ruff.
- Camoufox/Patchright backend client+adapter duplication reduced.
- Camoufox/Patchright bridge runtime duplication reduced where practical.
- `surf_agent/cli.py` AXI forwarding slop reduced or isolated.
- Temp JS file helper duplicated across surf-chatgpt reduced.
- Existing package builds and tests pass.

## Scope & Constraints

- Keep public CLI behavior and JSON output stable.
- One writer in shared worktree.
- Prefer small shared modules over large risky rewrites.
- Do not preserve legacy wrappers unless tests or public surface still require them.
- Validate with root pytest, scoped tests, ruff, builds, npm dry-run.

## Work Plan

1. Add root pytest config and dev dependency group.
2. Remove obvious dead locals/imports.
3. Extract shared local bridge client/backend support for Camoufox/Patchright backend adapters.
4. Extract shared bridge runtime data/ref helpers and HTTP handler where safe; keep sync/async browser calls separate.
5. Extract surf-chatgpt temp JS helper.
6. Trim or isolate AXI wrapper layer in `SurfAgent` if tests allow; otherwise document residual risk.
7. Run validation and review.

## Validation

- `uv run pytest -q`
- `uv run ruff check packages`
- `uv build --all --out-dir /tmp/browser-skills-build-all`
- `npm pack --dry-run --json`

## Progress

- Added root pytest importlib config and dev dependency group for pytest/ruff.
- Removed ruff-reported dead locals/imports.
- Extracted shared local bridge client/backend adapter logic to `surf_agent.backends.local_bridge` while keeping backend-specific profile launch and cleanup in Camoufox/Patchright modules.
- Extracted shared bridge runtime dataclasses, text/ref helpers, bbox normalization, and HTTP request handler to `surf_agent.backends.bridge_common`; kept sync Camoufox and async Patchright browser flows separate.
- Extracted surf-chatgpt temporary JS file creation/deletion to `surf_chatgpt.temp_js`.
- Removed the `SurfAgent` AXI forwarding wrapper layer, including `_axi_backend()` and the AXI-specific `ensure_page()` façade; tests that still exercise AXI internals now call the AXI backend directly.
- Retained backend-specific stable page-id aliases because the backend packages already export those names.
- Ran all required validation commands successfully.
- Parent review found the unittest `ResourceWarning` still present; fixed Patchright runner cleanup for idle helper calls and closed the started runtime in the launch-argument test.
- Re-ran surf-agent unittest with `PYTHONWARNINGS=default`; no ResourceWarnings emitted.

## Outcomes

- Root `uv run pytest -q`: passes.
- Root `uv run ruff check packages`: passes.
- `uv build --all --out-dir /tmp/browser-skills-build-all`: passes.
- `npm pack --dry-run --json`: passes.
- `PYTHONWARNINGS=default uv --directory packages/surf-agent run python -m unittest discover -s tests -q`: passes without ResourceWarnings.

## Residual risks

- Backend-specific `stable_*_page_id` aliases remain as public package exports; removal would be a behavior/API decision, not cleanup-only.
- Runtime bridge extraction intentionally avoided merging sync/async page-control code; remaining duplication there is safer than forcing one abstraction across different browser APIs.
