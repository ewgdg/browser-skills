---
name: surf
description: Real browser control for web research, documentation lookup, testing, screenshots, forms, page inspection, and debugging. Use when lightweight API tools are unavailable or rendered pages, authenticated sessions, or browser interaction are required.
---

# Surf

Follow the workflow below; open linked `docs/` only when the stated task or problem applies.

Run Python through `scripts/run.py`. Named browser threads persist between calls; Python variables, handles and their emission baselines live with the interpreter that ran the code.

## Prepare

Set `SURF_SKILL` to the absolute directory containing this installed `SKILL.md`, not the working directory. Requires Python 3, `uv`, and Google Chrome; the launcher manages Python dependencies.

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
- **Session** — `run.py --new-session -` creates an interpreter and reports its id, and later cells pass `--session ID`. Multi-step work that repeatedly inspects the same page benefits from one; a one-shot script does not need it.

A session keeps the initialized handle, its emission baseline and any helper or data from earlier cells, so later cells use them directly instead of importing again or rebuilding the handle. Create the session once and keep its id: `--new-session` prints it as the last stdout output of that call.

```bash
python3 "$SURF_SKILL/scripts/run.py" --new-session --name research - <<'PY'
from surf_agent import Thread

thread = Thread("research")
thread.open("https://example.com")
thread.emit(thread.snapshot())
PY
# … --- BEGIN session metadata --- / session_id: research-1f3a9c02 / --- END session metadata ---
```

```bash
python3 "$SURF_SKILL/scripts/run.py" --session research-1f3a9c02 - <<'PY'
thread.fill("@query", "browser skills")  # the handle and its baseline survived
thread.press("Enter")
thread.emit(thread.snapshot())
PY
```

The id is the session's only handle: an unknown id is refused rather than started, so reuse only ids this task created. Another task's interpreter holds its own bindings and emission baseline, so attaching to it yields diffs against observations this task never received. If the id is lost, `--list-sessions` lists each live session with its working directory; a task that waits on a human should create its session with a longer `--ttl`.

A session cell comes from stdin: send a file as `- < cell.py`, or run it without session options as an ordinary script. Cells run sequentially: a second cell while one is running fails immediately instead of queueing. A session ends when idle for its timeout, set at creation with `--ttl SECONDS` (default 1800 s), or with `--kill-session ID`, which stops it early, including an interpreter that stopped answering.

Cell output is capped at 4 MB per stream, with a marker saying what was dropped and which stream was capped; the number of lines is not limited. A cell that needs more writes a file and reads back what it needs.

A cell that exceeds `--timeout SECONDS` (default 300) destroys the session interpreter: the call reports the replacement, the id stops existing, and the failed cell's side effects are unknown. The browser thread outlives the interpreter, so `--new-session` and `Thread(name)` reach it again; a new handle has no baseline, so emit a full observation first. `--session ID --reset` discards bindings without replacing the interpreter or the id.

An interpreter that did not answer is busy, suspended or wedged rather than absent: it still holds the session's bindings, so it is never replaced silently, and a cell that holds the interpreter lock reads the same way as a wedged one. Let any cell you started finish first. `--list-sessions` keeps it and marks it unresponsive, and `--kill-session ID` stops it. If a call reports that the interpreter could not be identified, stop that pid yourself; a host that cannot prove which process owns the socket does not signal one.

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

Inspect the snapshot before choosing targets. Batch deterministic actions until a new observation or human decision is needed:

```python
from surf_agent import Thread

thread = Thread("research-42")
thread.fill("@query", "browser skills")  # use a target from the observed snapshot
thread.press("Enter")
thread.emit(thread.snapshot())
```

Actions and observations are silent; print only useful results. `snapshot().text` is complete. `emit(snapshot)` outputs a numbered observation with explicit BEGIN/END boundaries: full text first, then useful diffs; `full=True` forces full output. Multiple emissions appear in order in the same script output, not separate agent turns. End the script when the next action requires a decision. Read [snapshot semantics](docs/python-api.md#snapshot-output) for the format, baselines or custom sinks.

Pass a Python file or `-` for stdin; subsequent arguments reach `sys.argv`. For large text or JavaScript, read files in Python rather than nesting shell quoting:

```bash
python3 "$SURF_SKILL/scripts/run.py" /tmp/surf-step.py /tmp/body.txt /tmp/inspect.js
```

For action signatures and administrative operations, read [Python API](docs/python-api.md).

## Login and human unblock

For existing Chrome login state, read [cookie import](docs/cookie-import.md) before configuring access. For extension-based login, read [1Password setup](docs/1password-setup.md) once and [autofill](docs/1password-autofill.md) when logging in.

When blocked, ask the user to complete the blocker in the Surf Agent window and confirm when done. Preserve the page and wait for that confirmation, then inspect it using the same thread. Reopening the URL may destroy completed human work.

For manual login, close Surf automation windows and call `Browser().open_profile(url)`. Close manual Chrome before resuming automation. Bring a window forward with `Thread(name).focus()` only when requested.

## Recovery

After timeout or connection loss, inspect the same named thread before deciding whether to repeat an action: submissions, purchases, or messages may already have taken effect.

A cell timeout leaves a fresh interpreter behind, so reattach as [Sessions](#sessions) describes before repeating its work.

`is_open()` checks without creating a page, but false can also mean the bridge is unavailable. Reopen only when absence is established and navigation is safe. For a persistently unavailable bridge, use `Browser().stop_bridge()`; the next browser action restarts it. Restarting does not make replay safe.

## Cleanup

Close every thread you opened when its work is complete, including after errors; retain a thread only for an explicit pending human handoff. Thread cleanup stays the caller's job in either mode: a session ending does not close its threads.

```python
from surf_agent import Browser, Thread

Thread("research-42").close()
Browser().close_matching("run-42-*")
```

Use only task-owned cleanup patterns. `close_matching("*")` requires ownership of all remembered threads. For inventory or intentional detachment, consult the [API reference](docs/python-api.md).
