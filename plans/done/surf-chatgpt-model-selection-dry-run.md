# Goal

Add a public `surf-chatgpt model select` command that exercises ChatGPT model/thinking selection without injecting or sending a prompt.

# Scope and constraints

- Select through the existing browser picker path.
- Verify the resulting checked picker state before reporting success.
- Return structured JSON by default and compact text on request.
- Keep the inspection browser thread open in the background after success or failure so the user can examine it without focus stealing.
- Preserve the existing human-gate behavior when login or verification requires user action.
- Do not change `ask` behavior or add compatibility aliases.
- Treat model and thinking as independent fuzzy queries: models come only from the nested model list; thinking comes only from the top-level mode list.
- Remove legacy parsing that treats thinking names or suffixes passed through `--model` as thinking selection.

# Work plan

1. Add a failing public-CLI regression test proving selection occurs without composer injection or prompt submission.
2. Add browser-side selected-state inspection and verification.
3. Wire the `model select` command through the client and CLI.
4. Update the surf-chatgpt skill instructions and validation checklist.
5. Run targeted tests, the package suite, lint/static validation, and a live no-prompt smoke test.

# Validation

- `uv run --project packages/surf-chatgpt python -m pytest packages/surf-chatgpt/tests`
- Repository lint/test commands discovered from project configuration.
- `git diff --check`
- Live: `--thinking pro` and a real nested model query must verify without creating a conversation; `--model pro` must fail with `model_unavailable`.

# Progress

- Public seam and command syntax agreed with the user.
- Added red/green CLI coverage for command parsing and compact output.
- Added verified browser selection with checked-state inspection and human-gate recovery.
- Confirmed the current mixed ChatGPT picker fixture and separated model rows from thinking modes.
- Fixed thinking-only selection when the active picker label is `Pro`.
- Passed full repository validation and installed the updated CLI and skill globally.
- Follow-up: user rejected automatic cleanup because it removes the inspection evidence.
- Changed the public lifecycle contract to keep and focus the inspection thread; compact text output now includes its thread id.
- Revalidated, reinstalled, and confirmed the global command leaves its Patchright page open after CLI exit.
- Follow-up: do not raise or focus the inspection window automatically; persistence and focus are separate contracts.
- Moved optional dry-run instructions into a dedicated skill reference, leaving one pointer in the main skill.
- Verified with Niri that the focused terminal window remains unchanged while one unfocused Surf inspection window stays open.
- Follow-up: adapt the command taxonomy to the current UI where Pro is a thinking mode, not a model.
- Removed thinking aliases and model-suffix parsing; both options now preserve independent fuzzy queries.
- Added DOM regressions for nested model-only matching and fuzzy `Pro` / `Extra High` thinking selection.
- Revalidated, reinstalled, and passed the live Patchright checks without focus stealing.

# Surprises and discoveries

- The current picker mixes thinking modes with a nested model-family menu.
- The picker presents thinking modes at the top level and actual models only after opening a nested selector; pooling both menus caused `--model pro` to select the wrong dimension.
- The new no-prompt command provided the red-capable loop that exposed the actual stale selector.

# Outcomes

- `surf-chatgpt model select --thinking pro` selects and independently verifies the `Pro` thinking mode without sending a prompt.
- `--model` searches only newly revealed nested model rows; `--model pro` fails with `model_unavailable`.
- `--thinking` accepts independent fuzzy queries such as `pro` and `extra-high`; no fixed low/medium/high mapping remains.
- Full validation: 298 tests passed, Ruff passed, skill validation passed, and live global smoke tests passed on Patchright.
- Inspection sessions remain open in the background until the user explicitly closes the returned thread.
- Optional inspection documentation lives outside the always-loaded skill body.
- One unfocused inspection thread remains open: `surf-chatgpt-3359133-96cb990232c8`.
