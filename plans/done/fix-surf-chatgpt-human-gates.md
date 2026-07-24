# Fix surf-chatgpt human gates

## Goal

Make `surf-chatgpt ask` distinguish an active human-verification surface from ordinary ChatGPT page assets/content, and preserve the exact browser thread when login or verification requires a person.

## Intention

Detect blocking page state from visible, challenge-specific DOM instead of global keywords or loaded scripts. Represent human-required failures as resumable errors containing the focused thread and exact retry command.

## Scope & Constraints

- Cover both ask readiness and session-search page detection with one shared detector.
- Do not inspect or expose cookies.
- Do not weaken detection of a visible Cloudflare/CAPTCHA surface.
- Keep exit status nonzero until an answer exists.
- Do not send or retry the prompt automatically after human intervention.
- Preserve current cleanup for success and non-human-actionable failures.

## Work Plan

1. Add browser-executed DOM fixture tests for normal challenge assets/content and active visible challenge surfaces.
2. Implement one shared challenge detector and use it in ask and session search.
3. Add failing workflow tests for preserving/focusing human-gated ask threads and structured resume metadata.
4. Implement resumable human-gate errors and CLI rendering.
5. Tighten broad Surf stderr/stdout challenge classification.
6. Run package tests, lint, reinstall the tool, and perform logged-in Pro smoke verification.

## Validation

- A normal authenticated ChatGPT page with `/challenge-platform/` loaded is accepted.
- Ordinary content containing `captcha`, `cloudflare`, or `verify you are human` is accepted.
- A visible challenge iframe/form/root is rejected as `captcha_or_cloudflare`.
- Login/challenge errors preserve and focus their exact thread and provide `--thread` retry metadata.
- Other failures still close generated temporary threads.
- Full package test suite and lint pass.
- Installed CLI completes the Pro one-word smoke test.

## Progress

- Baseline: 75 package tests and 3 subtests passed.
- Live repro: authenticated ChatGPT page is rejected because it loads `/cdn-cgi/challenge-platform/scripts/jsd/api.js`; there is no challenge iframe/form/root or challenge text.
- Browser-executed regressions cover normal challenge assets/content, visible and hidden challenge surfaces, both ask and session-search paths, model-picker sidebar collisions, and delayed model UI.
- Human-gate errors preserve/focus the exact thread and return structured retry metadata.
- Final workspace validation: 288 tests and 29 subtests pass; Ruff and `git diff --check` pass.
- A freshly installed CLI completed the original ephemeral `--model pro --thinking high` smoke and returned `ok`.

## Surprises & Discoveries

- The current live trigger is the normally loaded Cloudflare script, not sidebar text.
- The ephemeral target is closed in `finally` even when a human must act on that exact page.
- A sidebar conversation-options button inherited its chat title through `aria-label`; a title containing “model” was mistaken for the model picker.
- ChatGPT's model control can appear after the composer, so empty/loading picker results need a short bounded retry while a real nonempty mismatch should still fail immediately.

## Decisions

- A loaded script is not a blocking challenge signal.
- Human gates remain errors, but become resumable through a preserved, focused Surf thread.
- Fuzzy model controls are accepted only inside the main composer; outside it, only the canonical model-switcher test ID is trusted.

## Outcomes & Retrospective

The original failure had two structural false positives rather than an authentication problem. Replacing global lexical checks with scoped DOM signals fixed challenge detection, and scoping the model picker fixed the exact ephemeral Pro path. The preserved-thread handoff turns real login/challenge gates into an explicit human-in-the-loop workflow without risking duplicate prompt submission.
