from __future__ import annotations

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from surf_agent.backends.patchright.bridge import PatchrightRuntime


@pytest.mark.skipif(
    os.environ.get("SURF_TEST_LIVE_PATCHRIGHT") != "1",
    reason="requires installed Chrome and Patchright; set SURF_TEST_LIVE_PATCHRIGHT=1",
)
@pytest.mark.parametrize("use_bfcache", [True, False], ids=["bfcache", "reload"])
def test_back_returns_dom_ready_for_cached_and_reloaded_history(tmp_path, monkeypatch, use_bfcache):
    from patchright.async_api import BrowserType

    if not use_bfcache:
        launch = BrowserType.launch_persistent_context

        async def launch_without_bfcache(browser_type, *args, **kwargs):
            kwargs["args"] = [*kwargs.get("args", []), "--disable-features=BackForwardCache"]
            return await launch(browser_type, *args, **kwargs)

        monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_without_bfcache)

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>First</title><body>First page</body>")
    (site / "next.html").write_text("<title>Next</title><body>Next page</body>")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site))
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile", headless=True)
    try:
        runtime.call("open", {"url": f"{base_url}/index.html"})
        page = runtime.pages["default"].page
        page.set_default_navigation_timeout(3_000)
        page.set_default_timeout(3_000)
        # Empty and same-document history must remain successful no-ops/traversals.
        assert runtime.call("back", {}) == "opened about:blank\n"
        assert runtime.call("back", {}) == "opened about:blank\n"
        runtime.call("open", {"url": f"{base_url}/index.html"})
        runtime._run(page.evaluate("history.pushState({}, '', '#section')"))
        assert runtime.call("back", {}) == f"opened {base_url}/index.html\n"
        runtime._run(page.evaluate("""() => {
            window.addEventListener('pageshow', event => {
                document.documentElement.dataset.restored = String(event.persisted);
            });
        }"""))
        runtime.call("open", {"url": f"{base_url}/next.html"})

        assert runtime.call("back", {}) == f"opened {base_url}/index.html\n"
        assert runtime._run(page.evaluate("document.readyState")) != "loading"
        restored = runtime._run(page.evaluate("document.documentElement.dataset.restored"))
        assert restored == ("true" if use_bfcache else None)
        assert "First page" in runtime.call("text", {})
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
