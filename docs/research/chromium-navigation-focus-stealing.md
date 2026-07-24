# Chromium navigation focus stealing under Niri

Date: 2026-07-24

## Conclusion

The strongest explanation is not `page.goto()` itself and not `promptElement.focus()`. The local Niri configuration enables `honor-xdg-activation-with-invalid-serial`, a global opt-out from Niri's focus-stealing protection. Chromium can request XDG activation when a page calls top-level `window.focus()`. With that Niri flag enabled, even a stale or otherwise invalid Chromium input serial can activate the Surf window.

This fits the controlled result:

- `Target.createTarget(..., focus: false)` leaves compositor focus unchanged.
- Navigating that unfocused target to Google moves Niri focus to Surf.
- Passive pages may not move focus.

The first test should therefore be to disable only `honor-xdg-activation-with-invalid-serial`, reload Niri, and repeat the same Google/ChatGPT/passive-page matrix. Do not change Surf code for this test.

## Primary-source evidence

### Niri is currently configured to permit this class of activation

The observed local configuration enables `honor-xdg-activation-with-invalid-serial`.

Niri's own documentation is explicit: it normally ignores XDG activation tokens with invalid serials “to prevent windows from randomly stealing focus”; the debug flag makes Niri honor them. The documented benefit is fixing tray-icon and notification activation for clients such as Discord and Telegram, so disabling it has that tradeoff ([Niri debug options](https://github.com/YaLTeR/niri/blob/7f26c3ee804fb6ed458ef7fb0e3c794f14e0b3bc/docs/wiki/Configuration:-Debug-Options.md#honor-xdg-activation-with-invalid-serial)).

The implementation matches the documentation:

- `token_created()` immediately accepts the token when the debug option is enabled; otherwise it validates the serial against recent keyboard and pointer enter serials ([Niri handler](https://github.com/YaLTeR/niri/blob/7f26c3ee804fb6ed458ef7fb0e3c794f14e0b3bc/src/handlers/mod.rs#L778-L816)).
- `request_activation()` calls `layout.activate_window()` for an accepted, non-expired token ([Niri handler](https://github.com/YaLTeR/niri/blob/7f26c3ee804fb6ed458ef7fb0e3c794f14e0b3bc/src/handlers/mod.rs#L818-L842)).

The Wayland protocol deliberately leaves the final decision to the compositor. A compositor may refuse an unwanted activation, and may reject tokens lacking a valid recent input serial ([xdg-activation-v1 protocol](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/xdg-activation/xdg-activation-v1.xml)).

### Chromium turns an allowed `window.focus()` into XDG activation

The relevant Chromium path is:

1. Blink's `DOMWindow::focus()` calls `Frame::FocusPage()` for an allowed top-level focus request ([`dom_window.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/core/frame/dom_window.cc#688), [`frame.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/core/frame/frame.cc#674)). Chromium attempts to prevent unexpected focus stealing here by requiring transient user activation or its unrestricted-window-focus setting.
2. The browser-side receiver reaches `RenderViewHostImpl::OnFocus()`, which invokes `delegate_->Activate()` ([`render_view_host_impl.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/renderer_host/render_view_host_impl.cc#858)).
3. `WebContentsImpl::Activate()` calls its delegate's `ActivateContents()` ([`web_contents_impl.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/web_contents/web_contents_impl.cc#4527)).
4. `Browser::ActivateContents()` activates the tab and calls `window_->Activate()` ([`browser.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/ui/browser.cc#1502)).
5. On Wayland, `WaylandToplevelWindow::Activate()` uses an inherited XDG activation token or asks `XdgActivation` for a fresh token ([`wayland_toplevel_window.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/ozone/platform/wayland/host/wayland_toplevel_window.cc#332), [`xdg_activation.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/ozone/platform/wayland/host/xdg_activation.cc#102)). The token request uses Chromium's latest tracked touch, mouse, or key serial when one exists ([`xdg_activation.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/ozone/platform/wayland/host/xdg_activation.cc#148)).

This chain explains why Niri's invalid-serial policy is decisive: Chromium can ask, but Niri decides whether an old/unrelated serial is sufficient to move compositor focus.

### `element.focus()` is not the same operation

The HTML Standard separates the two APIs:

- `HTMLElement.focus()` runs document focusing steps and changes the focused area in the page ([HTML focus management APIs](https://html.spec.whatwg.org/multipage/interaction.html#focus-management-apis)).
- `Window.focus()` targets the window's navigable. The standard notes that these APIs historically affected system-level widget focus but were widely abused ([HTML `Window.focus()`](https://html.spec.whatwg.org/multipage/interaction.html#dom-window-focus)).
- The autofocus section states directly: focusing an autofocus element does **not** imply that the browser window must be focused ([HTML autofocus](https://html.spec.whatwg.org/multipage/interaction.html#the-autofocus-attribute)).

Chromium follows this distinction. `Element::Focus()` updates Blink's `FocusController` ([`element.cc`](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/third_party/blink/renderer/core/dom/element.cc#8280)); it does not call the `Frame::FocusPage()` activation path above. Therefore `promptElement.focus()` should change `document.activeElement`, not independently activate the Niri window. This remains worth verifying after the compositor-policy test, but it is not the leading cause.

### Patchright `goto()` does not explicitly activate the page

Playwright's Chromium backend implements frame navigation with `Page.navigate`. Its explicit fronting operation is separate and uses `Page.bringToFront` ([Playwright `crPage.ts`](https://github.com/microsoft/playwright/blob/f86bd78cc191f154d9db49dedf96e7ead7b7b694/packages/playwright-core/src/server/chromium/crPage.ts#L154-L183), [navigation implementation](https://github.com/microsoft/playwright/blob/f86bd78cc191f154d9db49dedf96e7ead7b7b694/packages/playwright-core/src/server/chromium/crPage.ts#L565-L571)). The installed Patchright driver has the same separation.

CDP likewise defines independent commands:

- [`Page.navigate`](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-navigate) only navigates the current page.
- [`Page.bringToFront`](https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-bringToFront) activates the tab.
- [`Target.activateTarget`](https://chromedevtools.github.io/devtools-protocol/tot/Target/#method-activateTarget) explicitly “activates (focuses) the target.”
- [`Target.createTarget`](https://chromedevtools.github.io/devtools-protocol/tot/Target/#method-createTarget) documents that `focus: false` preserves browser-window focus during creation. It says nothing about later page behavior.

So `page.goto()` is the trigger timing, but current evidence does not show that Patchright itself issues an activation command.

## Ranked, falsifiable hypotheses

1. **Niri's invalid-serial debug option turns a site-triggered Chromium activation request into focus stealing.** High confidence. Disable only the option and repeat the controlled matrix. Prediction: Google and ChatGPT no longer steal Niri focus, while passive pages remain unchanged.
2. **Google/ChatGPT call `window.focus()` during or shortly after load.** Medium confidence. Install an init script before navigation that records and suppresses `window.focus()` while separately recording `HTMLElement.prototype.focus()`. Prediction with the Niri debug option still enabled: a `window.focus()` count appears, and suppressing it prevents focus transfer. Element-focus calls alone do not transfer compositor focus.
3. **Chromium activates for a navigation-adjacent reason other than page `window.focus()`.** Lower confidence. Suppress `window.focus()` and compare Patchright `page.goto()` with a direct CDP `Page.navigate`. Prediction: if both still steal focus, the source is below the Patchright wrapper and needs Wayland/CDP tracing.
4. **Focus emulation changes the site's decision to request activation.** Low confidence and not a fix by itself. [`Emulation.setFocusEmulationEnabled`](https://chromedevtools.github.io/devtools-protocol/tot/Emulation/#method-setFocusEmulationEnabled) only simulates an active/focused page. Test it as a differential; do not assume it suppresses OS activation.

## Recommended next controlled test

Use the already-known red case and change one variable:

1. Keep the Surf target unfocused and record the focused Niri window ID.
2. Disable `honor-xdg-activation-with-invalid-serial` and reload Niri.
3. Navigate the same target to Google; record Niri focus after `domcontentloaded` and again after a short settling interval.
4. Repeat for ChatGPT and a passive page, for both first and reused targets.
5. Trigger and complete a HITL handoff.

Success criteria:

- Normal navigation never changes the focused Niri window.
- The HITL workflow remains usable for manual completion.

The fifth step verifies that the manual unblock workflow still works under the corrected compositor policy.

## Fix direction after the test

1. **Preferred system policy:** remove the Niri debug opt-out. This is the generic boundary that decides whether an unfocused client may take desktop focus.
2. **Preserve HITL intentionally:** keep login/CAPTCHA handoff explicit and user-driven.
3. **Browser-local defense in depth:** if the init-script probe proves `window.focus()` is the site trigger, suppress top-level `window.focus()` in background Surf pages. This modifies page semantics and is less robust than compositor enforcement.
4. **Do not treat `focus: false`, `Page.navigate`, or focus emulation as a complete fix.** They control target creation, navigation, or page-visible focus state—not the compositor's activation policy.
