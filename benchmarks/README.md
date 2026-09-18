# Three-mode local browser benchmark

Historical comparison: the CLI arm requires checkout `24aa940` (the recorded pilot implementation). The current Python replacement intentionally removes that CLI; do not restore it to rerun this comparison. The fixture and analyzer remain usable independently, and the persistent arm drives the shipped session runtime rather than a benchmark-only worker.

Benchmark infrastructure only; no changes to Surf's production lifecycle. Run from the repository root. Each participant gets a fresh fixture process, dedicated Surf home and thread name, and identical task turns from `benchmarks/tasks.md`. Use the same model/settings. The runner owns scoring, timing and transcript/token collection.

## Runner setup

```sh
RUN=$(mktemp -d /tmp/surf-benchmark.XXXXXX)
export SURF_AGENT_HOME="$RUN/surf-home"
export SURF_AGENT_PATCHRIGHT_PORT=19331  # Runner: reserve a different unused port per arm.
uv run python benchmarks/fixture.py --info "$RUN/runner-info.json"
```

Keep the fixture process running in a terminal. Its stdout gives the public URL; the info file also holds the private oracle bearer token. Start a separate fixture per participant: state resets by restarting. No profile/cookie import is needed or allowed. Configure the benchmark browser backend in this isolated Surf home, never copy a user's profile. All modes must use the same backend and pacing settings; this harness does not initialize them. A separate home alone does not isolate Patchright's listening port: reserve distinct unused `SURF_AGENT_PATCHRIGHT_PORT` values per arm and pass each arm's home and port to every browser command and worker startup.

### Keep browser lifecycle independent of the execution mode

Some agent shell runners reap child processes when a command finishes. An automatically started browser bridge then disappears between calls, unfairly penalizing CLI/fresh scripts. Start the bridge in a controller-owned persistent terminal (use `tty=true` in this harness), with the same isolated environment in every mode:

```sh
export SURF_AGENT_BACKEND=patchright
uv run python -m surf_agent.backends.patchright.bridge \
  --port "$SURF_AGENT_PATCHRIGHT_PORT" \
  --profile-dir "$SURF_AGENT_HOME/profiles/chrome"
```

Keep that command running; in another terminal pre-open the assigned thread on its fixture URL and verify `state` in a separate shell call. Do this equally for all modes. The scored pilot measures warm browser workflows, not setup/startup. No cookie sources are configured; this explicit bridge startup is benchmark-only, not a replacement for production lifecycle preflight.

Give participants only their URL, mode, thread name, isolated environment, API/CLI documentation and the task turns. They must not read fixture source, runner info or oracle responses. Bearer authentication isolates the HTTP oracle, **not hostile agents sharing a Unix account**; this benchmark assumes instruction-following participants. Shell access remains allowed for scripts, quoting and local result files under `/tmp`, not out-of-browser fixture inspection.

### Modes

- **Optimized CLI:** `uv run --package surf-agent surf-agent …`; allow existing `do`, suppressed output, selected observations, diff and evaluate capabilities. Shell parsing and `/tmp` state files are allowed. Do not force full snapshots.
- **Fresh Python:** `uv run --package surf-agent python /tmp/task.py` or `uv run --package surf-agent python -`; use `from surf_agent import Thread`. Each invocation gets a fresh interpreter but reacquires the same named browser thread. Persist values explicitly in `/tmp` if desired. Close the browser only after all task turns.
- **Persistent Python:** one shipped session interpreter, created once and driven cell by cell below; globals and `Thread` handles survive between cells. Same browser API and observation freedom as fresh Python.

`uv run --package surf-agent` makes imports work for scripts outside the repository. No new dependencies are needed.

## Persistent session

Export `SURF_SKILL` to the installed skill directory and keep the participant's isolated
`SURF_AGENT_HOME` and unique `SURF_AGENT_PATCHRIGHT_PORT` in the environment, then create the
session once:

```sh
python3 "$SURF_SKILL/scripts/run.py" --new-session --name bench - <<'PY'
from surf_agent import Thread
thread = Thread('benchmark-persistent')
values = []
print('ready')
PY
# The call ends with --- BEGIN session metadata --- / session_id: bench-xxxxxxxx
```

Later cells pass that id back and read their source from stdin, exactly like `run.py -`:

```sh
python3 "$SURF_SKILL/scripts/run.py" --session bench-xxxxxxxx - <<'PY'
values.append(42)
print(values)
PY
```

Cells run sequentially: a second call while one is running is refused immediately. `--ttl SECONDS`
at creation sets the idle timeout for arms that wait on a human, `--session ID --reset` clears
participant bindings without replacing the interpreter, and `--kill-session ID` ends the session.
Cell output is capped at 4 MB per stream (bytes only, with a marker that says what was dropped), and
only `print()`/`emit()` reaches the caller: descriptor writes and
subprocess output are dropped by design, so an arm that needs more writes a file under `/tmp`.

For turn 4's interpreter-loss injection, run a cell that exceeds its own `--timeout`: the shipped
runtime destroys that interpreter, the call reports the replacement, the id stops existing and the
browser thread survives, so the participant has to create a new session and reattach with
`Thread(name)` before continuing. That is the recovery the skill documents, so the injection
measures the real path. Model conversation and scratch files remain available equally in all modes.

## Independent oracle

Runner only, after each relevant task turn:

```sh
uv run python - "$RUN/runner-info.json" <<'PY'
import json, sys, urllib.request
info = json.load(open(sys.argv[1]))
request = urllib.request.Request(info['url'] + '/oracle',
    headers={'Authorization': 'Bearer ' + info['token']})
print(json.dumps(json.load(urllib.request.urlopen(request)), ensure_ascii=False, indent=2))
PY
```

Compare lookup/aggregation answers against `expected`. For form correctness compare all stored fields, including exact multiline `notes`; expected name/type/code/request ID are stated on the pages. Require one submission and one attempt after turn 2 and after recovery. The fixture supports idempotency for unit tests, but scored participants must never submit twice. Verify field visibility with a controller-only read of the page after turn 1, audit turn 3's transcript for browser rereads, and check the thread is closed after turn 4. The HTTP oracle alone cannot prove those conditions.

## Usage and decision matrix

Keep private original participant transcripts outside the repository. `analyze.py` exports only usage, timing and numeric diagnostics, without thought text or encrypted reasoning:

```sh
uv run python benchmarks/analyze.py --pretty summarize /path/to/participant.jsonl
uv run python benchmarks/analyze.py --pretty evaluate /path/to/manifest.json
```

The manifest has a `modes` array. Each record supplies `name`, `transcript`, `expectedStageCount: 4`, `correctness` (five booleans), `recovery` (three booleans), and `simplicity` (CLI 5, fresh Python 4, persistent Python 2). Transcript stage count is measured automatically; missing expected count is not assumed complete. Alternatively supply sanitized `usage`, `activeElapsedSeconds`, and `stageCount` instead of `transcript`.

Weights, gates and limitations are frozen in `plans/done/code-mode-benchmark.md`. Results and interpretation are in `docs/benchmarks/code-mode-pilot.md`. Scores are descriptive pilot results, not proof of architectural superiority. An efficiency score does not override failed correctness or recovery.

## Verification

```sh
uv run pytest benchmarks -q
uv run python benchmarks/fixture.py --help
```

Tests cover oracle authentication, exact storage and idempotency. They do not launch a browser, and
the session runtime's own behaviour is covered by `packages/surf-agent/tests/test_session.py` and
`tests/test_skill_launcher.py`. Browser interaction and comparative scoring are runner-owned.
