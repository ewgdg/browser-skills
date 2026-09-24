from __future__ import annotations

import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from surf_agent.backends.patchright import bridge
from surf_agent.backends.patchright.bridge import PatchrightRuntime
from surf_agent.errors import ErrorCode

pytestmark = pytest.mark.skipif(
    os.environ.get("SURF_TEST_LIVE_PATCHRIGHT") != "1",
    reason="requires installed Chrome and Patchright; set SURF_TEST_LIVE_PATCHRIGHT=1",
)

PAGE = """<title>Primitives</title>
<body>
  <nav>Navigation noise</nav>
  <main><h1>Article</h1><p>Main body text</p></main>
  <button id="hidden" style="display:none">Hidden</button>
  <button id="disabled" disabled>Disabled</button>
  <input id="readonly" readonly value="fixed">
  <div id="covered-wrap" style="position:relative">
    <button id="covered">Covered</button>
    <div id="cookie-banner" style="position:absolute;inset:0;background:white">Accept cookies</div>
  </div>
  <a id="next" href="next.html">Next</a>
  <div id="spinner">Loading…</div>
  <script>
    setTimeout(() => {
      document.getElementById('spinner').remove();
      document.body.insertAdjacentHTML('beforeend', '<p>Saved</p>');
    }, 400);
  </script>
</body>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(PAGE)
    (root / "next.html").write_text("<title>Next</title><body>Next page</body>")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # Keep failure cases fast; production uses the longer default.
    monkeypatch.setattr(bridge, "ACTION_TIMEOUT_MS", 500)
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile", headless=True)
    try:
        yield runtime
    finally:
        runtime.stop()


def call_code(runtime: PatchrightRuntime, name: str, args: dict) -> tuple[ErrorCode, str]:
    with pytest.raises(bridge.BridgeCodedError) as raised:
        runtime.call(name, args)
    return raised.value.code, str(raised.value)


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        ("click", {"uid": "#hidden"}, ErrorCode.NOT_VISIBLE),
        ("click", {"uid": "#disabled"}, ErrorCode.NOT_ENABLED),
        ("fill", {"uid": "#readonly", "text": "x"}, ErrorCode.NOT_EDITABLE),
        ("click", {"uid": "#missing"}, ErrorCode.NOT_FOUND),
        ("click", {"uid": "@e999"}, ErrorCode.STALE_REF),
    ],
)
def test_action_failures_carry_actionability_codes(runtime, site, name, args, expected):
    runtime.call("open", {"url": f"{site}/index.html"})

    code, _message = call_code(runtime, name, args)

    assert code == expected


def test_intercepted_click_names_the_blocking_element(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})

    code, message = call_code(runtime, "click", {"uid": "#covered"})

    assert code == ErrorCode.INTERCEPTED
    assert "cookie-banner" in message


def test_wait_for_text_and_gone_conditions(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})

    assert runtime.call("wait-for", {"text": "Saved", "gone": "Loading…", "timeoutMs": 3_000}) == "waited\n"


def test_wait_for_url_after_navigation(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})
    runtime.call("click", {"uid": "#next"})

    assert runtime.call("wait-for", {"url": "*/next.html", "timeoutMs": 3_000}) == "waited\n"


def test_wait_timeout_reports_unmet_conditions_and_page_state(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})

    code, message = call_code(runtime, "wait-for", {"text": "Never shown", "url": "*/other*", "timeoutMs": 300})

    assert code == ErrorCode.WAIT_TIMEOUT
    assert "Never shown" in message
    assert "*/other*" in message
    assert f"{site}/index.html" in message
    assert "Primitives" in message


def test_text_scoped_to_target_excludes_other_regions(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})

    scoped = runtime.call("text", {"target": "main"})

    assert "Main body text" in scoped
    assert "Navigation noise" not in scoped


def test_text_scoped_to_snapshot_ref(runtime, site):
    runtime.call("open", {"url": f"{site}/index.html"})
    snapshot = runtime.call("snapshot", {})
    ref = re.search(r'main \[ref=(e\d+)\]', snapshot)
    assert ref, snapshot

    assert "Main body text" in runtime.call("text", {"target": f"@{ref.group(1)}"})
