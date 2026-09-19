---
status: accepted
---

# Take session cells from stdin, and name the redirect in the refusal

`run.py --new-session/--session ID -` reads a cell from stdin, and a file argument is refused with the redirect that works: `--session ID - < cell.py`, plus the reminder that the same file without a session option runs as an ordinary script. The launcher keeps one meaning per token: `FILE` is an ordinary Python script — fresh interpreter, `__file__` set, the file's directory as `sys.path[0]` — while `-` is one cell `exec`'d into the session's namespace, where `__file__` never exists and `sys.path[0]` is the interpreter's working directory. Accepting a path in session position would give one command line two meanings and would resolve sibling imports differently with no local signal.

## Considered options

**Bare `FILE` as the cell source.** Rejected: a cell is `exec`'d, not run as a script, so the same token would silently change `__file__`, `sys.path[0]` and relative imports — the reason recorded when sessions were built (`plans/active/persistent-interpreter.md`). Reading a path as a cell is only honest once the caller states that it is one.

**A `--file PATH` option.** Deferred, not rejected: it names the intent, so no token changes meaning, and it removes the dead end entirely. Deferred because the friction it removes measured one refusal in 112 session cells over 45 days of pi transcripts, with no organic use of the redirect in that window, and it pays for that with a second source form and a divergence from script semantics that must be documented where it is used.

**Script parity inside a session (`__file__` and the file's directory on `sys.path` for that cell).** Rejected: `__file__` would change meaning every cell, and `sys.path` would accumulate directories across a session, so a later cell could import a module from a directory its own code never named.

**Leave the refusal as it was.** Rejected: it said cells come from stdin without saying how a file gets there, and the observed caller re-authored the file as a heredoc instead of redirecting it. The message now carries the caller's own flags and path.

## Consequences

`- < cell.py` is the supported file form, stated once in `skills/surf/SKILL.md`; script arguments still follow `-`. The runtime stays source-agnostic because it receives code bytes, so this decision lives in the launcher's grammar and error message and involves no runtime revision. Weighted for silent divergence (25), removed friction (20), simplicity (15), design consistency (15), document load (10), reversibility (10) and capability (5), the accepted option scored 96.5 of 100 against 92 for the unchanged refusal, 86 for the flag and 61.5 for parity, and that ordering held across every reweighting tested. Evidence came from classifying refusal output against the command that produced it across 1519 pi transcript files, not from a single session. Revisit the flag if refusals recur at roughly one per 20 session cells, or if file-based cells become routine work.
