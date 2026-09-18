# Chromium navigation focus stealing under Niri

Accepted findings from the investigation into Surf navigations taking Niri focus. Full
evidence, primary sources and the Chromium call chain:
`~/.agents/artifacts/outputs/browser-skills/2026-07-24/chromium-navigation-focus-stealing/research.md`.

## Conclusion

The leading cause is local compositor policy, not Surf code: the Niri configuration enables
`honor-xdg-activation-with-invalid-serial`, which makes Niri accept XDG activation even with a
stale or otherwise invalid input serial. Chromium can request that activation when a page calls
top-level `window.focus()`, so `page.goto()` is the timing rather than the trigger.

Two near-misses are ruled out: `Target.createTarget(..., focus: false)` leaves compositor focus
unchanged, and `element.focus()` is a different operation that changes the page's focused area
without activating the window.

## Test this first

Disable only `honor-xdg-activation-with-invalid-serial`, reload Niri, and repeat the same
Google/ChatGPT/passive-page matrix with the Surf target unfocused, recording Niri's focused
window before and after. Success criteria: ordinary navigation never changes it, and the manual
unblock handoff still works. Do not change Surf code for this test.

## Fix direction

1. Preferred: remove the Niri debug opt-out. That is the generic boundary deciding whether an
   unfocused client may take desktop focus.
2. Keep login and CAPTCHA handoff explicit and user-driven.
3. Defence in depth only if an init-script probe proves `window.focus()` is the site trigger:
   suppress top-level `window.focus()` in background Surf pages, accepting that this changes page
   semantics and is weaker than compositor enforcement.
4. Do not treat `focus: false`, `Page.navigate` or focus emulation as complete fixes: they control
   target creation, navigation or page-visible focus state, never compositor activation policy.
