---
name: surf
description: Real browser control for web research, documentation lookup, browsing, testing, screenshots, forms, page inspection, and debugging. Use as a fallback when lightweight API-based tools are unavailable, or as the primary approach when you need high-fidelity browser access that won't be blocked.
---

# Surf

Use ordinary Python scripts through this skill's `scripts/run.py`. Each call starts a fresh Python interpreter; the browser and named thread persist, Python variables and emission baselines do not. Surf's action CLI has been removed. The separate Google Search CLI remains supported.

## Prepare

Set `SURF_SKILL` to the absolute directory containing this installed `SKILL.md`; resolve it from the skill location, not the working directory. The launcher needs Python 3 and `uv`, installs the Patchright extra, and selects Python 3.11. Install Google Chrome separately or set `SURF_AGENT_CHROME_BIN`.

**Release gate:** this checkout's `runtime-revision` is `UNRELEASED`. Default launch intentionally fails until an authorized push makes a tested commit reachable and its full commit ID is pinned. For local validation only, set `SURF_AGENT_DEPENDENCY` to an absolute built `surf-agent` wheel path. A local wheel passing tests is not a published release.

Run setup once, or after installation changes:

```bash
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Browser

browser = Browser()
browser.setup()
print(browser.backend())
print(browser.profile())
PY
```

Patchright is the default; AXI is an explicit alternative. Selection is environment `SURF_AGENT_BACKEND`, then persisted config, then Patchright. For selection or backend problems read [backends](docs/backends.md). Persistent config, thread records, and dedicated profiles use platform user directories; `SURF_AGENT_HOME` collects them under `config.json`, `threads/`, and `profiles/`.

## Browse, observe, decide

Use a unique thread name for each parallel task. A thread owns one remembered page in a dedicated, normal Chrome window with toolbar and extension controls, separate from the user's main profile. Reuse its name for the same browsing task.

```bash
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Thread

thread = Thread("research-42")
thread.open("https://example.com")  # creates its window when missing
thread.emit(thread.snapshot())
PY
```

Inspect that output before choosing targets. Batch known, deterministic actions in one script; end the batch where a new observation or human decision is needed. Every call imports and initializes its own handles:

```python
from surf_agent import Thread

thread = Thread("research-42")  # reattach; do not navigate again
thread.fill("@query", "browser skills")  # use a target from the observed snapshot
thread.press("Enter")
thread.emit(thread.snapshot())
```

Actions and observations return values without printing. Print only useful results. `snapshot()` returns a complete `Snapshot`; `.text` is always full text. `emit(snapshot)` prints full output on the handle's first emission, then useful diffs against its last successful emission, with full-output fallback when a diff is unsuitable. Capturing a snapshot alone does not set a baseline. `emit(snapshot, full=True)` forces full output; navigation resets the baseline. A fresh script's first emission is full even when reattaching to the same page.

For a small change within one script, emit the starting snapshot, act, then emit the next snapshot. Use separate handles for independent output consumers.

## Scripts and values

Pass a Python file as the first launcher argument, or `-` for stdin; subsequent arguments reach `sys.argv`. Keep large text and JavaScript in files rather than nesting shell/Python quoting:

```python
from pathlib import Path
import sys
from surf_agent import Thread

thread = Thread("research-42")
thread.fill("@editor", Path(sys.argv[1]).read_text())
print(thread.evaluate(Path(sys.argv[2]).read_text()))
thread.emit(thread.snapshot())
```

Run that saved script with:
```bash
python3 "$SURF_SKILL/scripts/run.py" /tmp/surf-step.py /tmp/body.txt /tmp/inspect.js
```

For signatures and return semantics read [Python API](docs/python-api.md). Backend-specific setup: [Patchright](docs/patchright-backend.md), [AXI](docs/axi-backend.md).

## Login and human unblock

For existing Chrome login state, read [cookie import](docs/cookie-import.md) before configuring access. For extension-based login, read [1Password setup](docs/1password-setup.md) once and [autofill](docs/1password-autofill.md) when logging in.

When blocked, tell the user: "Please complete the blocker in the Surf Agent window, then tell me when done." Wait for explicit confirmation. Then run a fresh script with the same `Thread(name)` and emit its snapshot. Preserve the page while waiting; reopening the URL may destroy completed human work.

For manual profile setup without automation/debugging, close the task's windows and use `Browser().open_profile(url)`; finish and close manual Chrome before resuming automation. `Thread(name).focus()` selects the remembered page only when requested.

## Recovery

After timeout or connection loss, an action may already have taken effect. Reattach by name and inspect state or snapshot first. Verify the intended result before deciding whether another action is necessary; never blindly repeat a submission, purchase, message, or other uncertain side effect.

If the page was closed externally, `thread.is_open()` can check without opening a new page; it returns false for an unavailable bridge too, so false alone is not proof an action failed. Reopen only once absence is established and navigation is safe. For a persistently unavailable bridge, `Browser().stop_bridge()` explicitly stops automation-owned runtime; the next browser action starts it again. Restarting runtime is not evidence that the previous action failed.

## Cleanup

Close every thread you opened when its work is complete, including after errors; retain a thread only for an explicit pending human handoff.

```python
from surf_agent import Browser, Thread

Thread("research-42").close()
Browser().close_matching("run-42-*")  # only a namespace owned by this task
```

`Browser().threads()` lists Surf-managed inventory, not all Chrome pages: Patchright's running bridge owns its inventory; AXI uses local records. Successful closing removes the remembered thread. AXI's `Thread(name).reset()` clears remembered state without closing the page; reserve it for intentional detachment. Patchright does not support reset; use close instead. `Browser().close_matching("*")` is global cleanup and requires ownership of all remembered threads.
