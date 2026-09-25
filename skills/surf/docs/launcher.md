# Launcher setup and validation

Read this for dependency or release-installation failures, or deliberate local development validation. Normal invocation belongs to [Sessions](../SKILL.md#sessions), and the browser workflow to [browse, observe, decide](../SKILL.md#browse-observe-decide).

## Dependencies and execution

`scripts/run.py` requires Python 3 and `uv` on PATH. It asks uv for Python 3.11 and `surf-agent[patchright]`, independently of the current project's environment and uv configuration. Install missing uv using its [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

The launcher executes Python directly: file-relative imports, working directory, script arguments, stdin, exceptions and exit codes follow ordinary Python behavior. It does not process inline dependency metadata. Google Chrome is a separate prerequisite; see [Patchright setup](patchright-backend.md#setup-and-selection) for detection and executable overrides.

## Published runtime

`runtime-revision`, beside `SKILL.md`, selects a full 40-character Git commit for the runtime dependency. Users update the skill; the launcher installs its matching runtime. The Google Search skill's launcher reads the same pin, so both skills share one runtime revision.

The pinned requirement is what gets installed into the environment above; there is no
separate runtime installation to manage, and a new revision builds a new environment.

A missing or invalid pin is an installation defect. Stop and report it, then update or repair the installed skill. If the pinned commit cannot be fetched, report that failure.

## Sessions

`run.py --new-session [--name SLUG] [--ttl SECONDS] -` creates an interpreter and reports the session id as the last stdout output of that call; [SKILL.md](../SKILL.md) covers when a session is worth using:

```text
--- BEGIN session metadata ---
session_id: research-1f3a9c02
idle_timeout_s: 1800
--- END session metadata ---
```

`run.py --session ID -` runs the next cell in that interpreter, `--session ID --reset` discards its bindings without replacing it, and `--timeout SECONDS` bounds a cell (default 300); a timed-out cell ends the session, and [Cell timeout](../SKILL.md#cell-timeout) gives the recovery. Ids are opaque: a call naming a session that does not exist is refused with the live list rather than starting a new interpreter.

A session ends when it has been idle for `--ttl` seconds (default 1800), measured between cells, so a running cell is never cut by it, or when `run.py --kill-session ID` stops it. `run.py --list-sessions` prints each live session with its interpreter pid, cell count, idle time, timeout and working directory; an interpreter that does not answer is listed as unresponsive rather than swept away, because it may still be resumed and must stay stoppable by the id that names it.

Each session is one socket file in `$XDG_RUNTIME_DIR/surf-agent/` (the state directory when `XDG_RUNTIME_DIR` is unset); that file is the whole session record. The interpreter keeps the environment, working directory and Python bindings of the call that created it, runs cells sequentially (a cell sent while another runs is refused immediately rather than queued), and points its own stdout and stderr at `/dev/null`: cell output travels over the socket, so only `print()` and `emit()` reach the caller. Raw file-descriptor writes, subprocess output, and anything written after the interpreter dies are dropped.

Each stream is capped at 4 MB of bytes, so a runaway cell cannot buffer without limit; the number of lines is not limited. A capped stream ends with a marker saying how many of how many bytes were shown and that the rest was discarded, and the cell's frame says which stream was capped. That ceiling is well above realistic cell output - pi keeps the text of a truncated tool result in a file, while this is the only lossy boundary - and a cell that needs more writes a file and reads back what it needs.

If a call reports that the interpreter did not answer, it is suspended or wedged. Stop it with `--kill-session ID`, which shuts down a responding interpreter and kills one that cannot answer the shutdown request, or resume it (`kill -CONT <pid>`) to keep its bindings; browser threads survive either way.

Confirmation is what makes that safe: the process that may be signalled is the one holding that session's socket open, so neither a recycled pid nor a process that merely mentions the path is killed. A host that cannot identify the process says so instead of signalling an unconfirmed pid, and names the `kill -9 <pid>` command to use instead.

Ownership comes from the kernel's own record of the socket: `/proc/net/unix` matched against `/proc/<pid>/fd` where `/proc` exists, and `lsof -a -U -F pcfn` where it does not (macOS ships `lsof`). A match on the worker's command line is a weaker claim - a process that merely mentions the path satisfies it - so that pid is reported for the caller to stop, never signalled by the tool. Liveness is read the same way: `/proc/<pid>/stat` where present, `ps -o state=` otherwise, because a finished worker that its creator has not reaped is a zombie and must not read as running. The research behind these choices, including what FreeBSD would need, is in [unix-socket-owner-without-proc.md](https://github.com/ewgdg/browser-skills/blob/main/docs/research/unix-socket-owner-without-proc.md).

Identification also has to be unique. If two processes are bound to one socket path (an older listener whose socket file was deleted, plus a newer one), or the socket's owner cannot be established at all, nothing is signalled and the command says so: a pid chosen from an ambiguous answer could be the wrong process, and stopping the wrong interpreter is worse than leaving a wedged one for `kill -9 <pid>`.

An interpreter that is busy inside a cell holds the lock that would answer the probe, so it reads as unresponsive exactly like a wedged one; the message says which two states it could be in, and `kill -CONT` keeps the bindings of either.

## Local development validation

Only when deliberately testing local runtime changes, supply an existing built wheel by absolute path:

```bash
export SURF_AGENT_DEPENDENCY=/absolute/path/surf_agent-0.1.0-py3-none-any.whl
python3 "$SURF_SKILL/scripts/run.py" - <<'PY'
from surf_agent import Browser
Browser().setup()
PY
```

This override bypasses the revision pin and installs the wheel with its Patchright extra. It accepts a wheel, not a source checkout or arbitrary dependency string. Successful local validation does not verify remote installation. Unset `SURF_AGENT_DEPENDENCY` when testing a published pin.
