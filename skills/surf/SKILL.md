---
name: surf
description: Real browser control for web research, documentation lookup, testing, screenshots, forms, page inspection, and debugging. Use when lightweight API tools are unavailable or rendered pages, authenticated sessions, or browser interaction are required.
---

# Surf

Follow the workflow below; open linked `docs/` only when the stated task or problem applies.

Run Python through `scripts/run.py`. Named browser threads persist between calls; Python variables, handles and their emission baselines live with the interpreter that ran the code.

## Prepare

Set `SURF_SKILL` to the absolute directory containing this installed `SKILL.md`, not the working directory. Runs on Linux and macOS; Windows is unsupported. Requires Python 3, `uv`, and Google Chrome; the launcher manages Python dependencies.

The launcher installs the runtime pinned by this skill. If it reports a missing or invalid pin, the skill is incorrectly installed; report that instead of working around it. For dependency failures, read [launcher setup](docs/launcher.md).

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

For backend selection, profile configuration, or startup problems, read [backends](docs/backends.md) and its backend-specific guide.

## Sessions

Choose one mode per task:

- **Fresh interpreter** — `run.py FILE|-` starts a new interpreter for that call. Import and initialize handles each time; write intermediate data to files when a later call needs it.
- **Session** — `run.py --new-session -` creates a session interpreter and reports its id, and later cells pass `--session ID`. Multi-step work that repeatedly inspects the same page benefits from one; a one-shot script does not need it.

A session is one Python process kept alive between calls, as in a notebook: each call is a cell, so the handle, its emission baseline and earlier helpers stay in scope, and later cells use them directly instead of importing again. Create the session once and keep its id: `--new-session` prints it as the last stdout output of that call.

```bash
python3 "$SURF_SKILL/scripts/run.py" --new-session --name research - <<'PY'
from surf_agent import Thread

thread = Thread("research")
thread.open("https://example.com")
thread.emit(thread.snapshot())
PY
# … - link "Learn more" [ref=e6] …
# … --- BEGIN session metadata --- / session_id: research-1f3a9c02 / --- END session metadata ---
```

```bash
python3 "$SURF_SKILL/scripts/run.py" --session research-1f3a9c02 - <<'PY'
thread.click("@e6")  # ref from the snapshot above; the handle and its baseline survived
thread.emit(thread.snapshot())
PY
```

The id is the session's only handle: an unknown id is refused rather than started, so reuse only ids this task created. Another task's interpreter holds its own bindings and emission baseline, so attaching to it yields diffs against observations this task never received. If the id is lost, `--list-sessions` lists each live session with its working directory; a task that waits on a human should create its session with a longer `--ttl`. `--session ID --reset` discards bindings but keeps the interpreter and id.

Send a cell on stdin, or a file as `- < cell.py`. Cells run one at a time. For defaults, output limits, and an interpreter that stops answering, read [launcher sessions](docs/launcher.md#sessions).

### Cell timeout

A cell that exceeds `--timeout SECONDS` (default 300) ends the session: the call reports `session ID ended`, the id stops existing, its bindings are gone, and the cell's side effects are unknown. The browser thread survives. Recover in order:

1. Create a new session with `--new-session`.
2. Rebuild the handle with the task's thread name, `Thread("research")`, without calling `open()`.
3. Emit a full observation (the new handle has no baseline), then follow [Recovery](#recovery) before repeating any action.

If the call instead reports the interpreter was not stopped, it may still hold the bindings; read [launcher sessions](docs/launcher.md#sessions).

## Browse, observe, decide

Use a unique thread name per task. Surf owns a dedicated Chrome window/profile, separate from the user's main browser. Reuse the name to continue the task without navigating again.

```bash
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Thread

thread = Thread("research-42")
thread.open("https://example.com")  # creates its window when missing
thread.emit(thread.snapshot())
PY
```

Inspect the snapshot before choosing targets: use a ref the snapshot printed, so a line `- searchbox "Search" [ref=e12]` is targeted as `@e12` ([target forms](docs/python-api.md#thread)). Batch deterministic actions until a new observation or human decision is needed:

```python
from surf_agent import Thread

thread = Thread("research-42")
thread.fill("@e12", "browser skills")  # ref from the observed snapshot
thread.press("Enter")
thread.wait(url="*/search*")  # confirm the effect instead of sleeping
thread.emit(thread.snapshot())
```

Confirm an action's effect with `wait(text)`, `wait(gone=...)` or `wait(url=...)` rather than a fixed sleep. Read one region with `thread.text("@e5")` or `thread.text("main")` instead of the whole body. When a call fails, branch on `error.code` ([codes](docs/python-api.md#errors)): `intercepted` names the covering element, `outcome_unknown` means the action may already have happened.

Actions and observations are silent; print only useful results. `snapshot().text` is complete. `emit(snapshot)` outputs a numbered observation with explicit BEGIN/END boundaries: full text first, then useful diffs; `full=True` forces full output. Multiple emissions appear in order in the same script output, not separate agent turns. End the script when the next action requires a decision. Read [snapshot semantics](docs/python-api.md#snapshot-output) for the format, baselines or custom sinks.

Pass a Python file or `-` for stdin; subsequent arguments reach `sys.argv`. For large text or JavaScript, read files in Python rather than nesting shell quoting:

```bash
python3 "$SURF_SKILL/scripts/run.py" /tmp/surf-step.py /tmp/body.txt /tmp/inspect.js
```

For action signatures and administrative operations, read [Python API](docs/python-api.md).

## Login and human unblock

When a site asks for login that the user's normal Chrome already has, offer to import that site's cookies; on consent, follow [import for one site](docs/cookie-import.md#import-for-one-site). Never import without asking. For other cookie configuration, read [cookie import](docs/cookie-import.md) first. For extension-based login, read [1Password setup](docs/1password-setup.md) once and [autofill](docs/1password-autofill.md) when logging in.

When blocked, ask the user to complete the blocker in the Surf Agent window and confirm when done. Preserve the page and wait for that confirmation, then inspect it using the same thread. Reopening the URL may destroy completed human work.

For manual login, close Surf automation windows and call `Browser().open_profile(url)`. Close manual Chrome before resuming automation. Bring a window forward with `Thread(name).focus()` only when requested.

## Recovery

After timeout or connection loss, inspect the same named thread before deciding whether to repeat an action: submissions, purchases, or messages may already have taken effect.

For a session cell timeout, recover the session first as [Cell timeout](#cell-timeout) describes.

`is_open()` checks without creating a page, but false can also mean the bridge is unavailable. Reopen only when absence is established and navigation is safe. For a persistently unavailable bridge, use `Browser().stop_bridge()`; the next browser action restarts it. Restarting does not make replay safe.

## Cleanup

Close every thread you opened when its work is complete, including after errors; retain a thread only for an explicit pending human handoff. Thread cleanup stays the caller's job in either mode: a session ending does not close its threads.

```python
from surf_agent import Browser, Thread

Thread("research-42").close()
Browser().close_matching("run-42-*")
```

Use only task-owned cleanup patterns. `close_matching("*")` requires ownership of all remembered threads. For inventory or intentional detachment, consult the [API reference](docs/python-api.md).
