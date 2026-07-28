# PROTOTYPE — resumable command and JSON interface

This is throwaway planning code for **Specify the command and JSON interface**.
It is not production behavior.

## Question

Does one submit command, plus a compact group of session commands, make the
recoverable ChatGPT lifecycle obvious? The prototype makes the proposed
commands, JSON result, exit code, and hidden browser/session state visible after
every action so awkward duplication or missing information can be found before
implementation.

Run from the repository root:

```sh
uv run python packages/surf-chatgpt/prototypes/command_json_interface/tui.py
```

## Proposed interface

```text
surf-chatgpt ask [--session ID_OR_URL | --thread SURF_THREAD]
                 [--model QUERY] [--thinking QUERY]
                 [--wait[=SECONDS]] [--retain]
                 [--pace natural|none] [--allow-logged-out]
                 [PROMPT]

surf-chatgpt session status  SESSION [--retain]
surf-chatgpt session result  SESSION [--wait[=SECONDS]] [--retain]
surf-chatgpt session handoff SESSION
surf-chatgpt session abandon SESSION
```

- Plain `ask` returns after ChatGPT assigns a durable session and Surf rebinds
  its page to the deterministic session thread.
- Public session objects contain only the durable session ID. The canonical
  `https://chatgpt.com/c/<id>` recovery URL is derived internally rather than
  duplicated in output.
- `ask --session` submits a follow-up after resolving or recovering the durable
  ChatGPT session. `ask --thread` submits through a preserved pre-session page
  when login or a challenge blocked the original submission.
- Bare `--wait` uses 2700 seconds. `--wait=SECONDS` selects another observation
  deadline. Waiting never changes whether submission succeeded.
- `session status` reports only the affirmatively observed latest-attempt state.
- `session result` inspects once; `session result --wait` observes until terminal
  or its own deadline. Both are repeatable and non-consuming.
- `session handoff` preserves or recovers the page and returns its Surf thread.
  It never focuses. An agent may show
  `surf-agent --thread '<thread>' focus`, but only the user may run it.
- `session abandon` is the explicit confirmation. It has no `--yes` or `--force`
  variant.
- Results are one compact JSON object on stdout. There is no `--format` option.

The internal state deliberately shows page tokens and cleanup. Those are not
part of normal public JSON; they are visible here only to validate the exact-page
and restart behavior behind the interface.

## Suggested passes

1. Press `a`, then `o`, `r`, `c`, and `r` to compare submission, status,
   not-ready, and completed result shapes.
2. Reset with `z`, press `l`, then `a` to trigger a pre-session login handoff.
   Press `u` to represent the user completing login, then `p` to retry through
   the preserved Surf thread.
3. Press `b` after a session exists, then `f`. The durable session wrapper
   recovers a new page before submitting through the shared resolved-page path.
4. Toggle inspection failure with `i`, then try status, handoff, and abandonment.
5. Mark a terminal state and compare `r` with `v` to see default cleanup versus
   explicit retention without adding page-management fields to public JSON.
