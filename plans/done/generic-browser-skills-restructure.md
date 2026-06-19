# Generic Browser Skills Restructure

## Goal
Rename package/repo metadata from ChatGPT-only to generic browser skills, and move tests so each skill owns its tests.

## Scope & Constraints
- No backend swap yet.
- No new general `surf` skill implementation yet.
- Keep current `surf-chatgpt` behavior intact.
- Preserve package installability through `package.json` `pi.skills`.

## Work Plan
1. Rename package metadata/docs to generic browser skills name.
2. Move `tests/` under `skills/surf-chatgpt/tests/`.
3. Update test commands in docs and config so tests run from skill dir without root `PYTHONPATH` hacks.
4. Run test suite.

## Validation
- `cd skills/surf-chatgpt && uv run python -m unittest discover -s tests`
- root package metadata still exposes `skills`.

## Progress
- Renamed npm package metadata to `browser-skills`.
- Moved ChatGPT tests to `skills/surf-chatgpt/tests/`.
- Updated README and skill validation commands.
- Verified local tests and npm pack file list; package excludes skill-local tests and Python caches.

## Outcomes & Retrospective
- Repo metadata is generic enough for multiple browser skills.
- Test hierarchy is skill-local, so ChatGPT tests run from `skills/surf-chatgpt` without root `PYTHONPATH`.
- Physical checkout directory was not renamed inside the live agent session to avoid invalidating the harness cwd; do it outside or in a final one-shot command when ready.
