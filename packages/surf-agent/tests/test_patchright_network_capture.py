from __future__ import annotations

import json
import os
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from surf_agent.backends.patchright.bridge import PatchrightRuntime

ITEMS = {"items": [{"id": 1, "title": "First"}, {"id": 2, "title": "Second"}], "next": None}
FETCHING_PAGE = """<title>Feed</title>
<link rel="stylesheet" href="/style.css">
<body><div id="status">loading</div>
<button onclick="fetch('/api/more.json')">More</button>
<script>
fetch('/api/items.json').then(response => response.json()).then(data => {
  document.getElementById('status').textContent = 'loaded ' + data.items.length;
});
</script></body>"""


@pytest.fixture
def site_url(tmp_path):
    site = tmp_path / "site"
    (site / "api").mkdir(parents=True)
    (site / "index.html").write_text(FETCHING_PAGE)
    (site / "other.html").write_text("<title>Other</title><body>Other page</body>")
    (site / "style.css").write_text("body { color: black; }")
    (site / "api" / "items.json").write_text(json.dumps(ITEMS))
    (site / "api" / "more.json").write_text(json.dumps({"items": []}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.skipif(
    os.environ.get("SURF_TEST_LIVE_PATCHRIGHT") != "1",
    reason="requires installed Chrome and Patchright; set SURF_TEST_LIVE_PATCHRIGHT=1",
)
def test_first_open_captures_api_response_and_open_clears_it(tmp_path, site_url):
    runtime = PatchrightRuntime(profile_dir=tmp_path / "profile", headless=True)
    try:
        # First call on the thread: the page is created already loading this URL.
        runtime.call("open", {"url": f"{site_url}/index.html"})
        runtime.call("wait-for", {"text": "loaded 2", "timeoutMs": 3_000})

        entries = json.loads(runtime.call("responses", {}))["responses"]
        assert [entry["url"] for entry in entries] == [f"{site_url}/api/items.json"]
        [entry] = entries
        assert entry["method"] == "GET"
        assert entry["status"] == 200
        assert entry["content_type"].startswith("application/json")
        assert entry["shape"] == {"items": [{"id": "int", "title": "str"}], "next": "null"}

        detail = json.loads(runtime.call("response-body", {"key": entry["key"]}))
        assert detail["body"] == ITEMS

        # The response lands while no bridge call runs; listing must still see it.
        runtime.call("click", {"uid": "button"})
        time.sleep(0.5)
        urls = [entry["url"] for entry in json.loads(runtime.call("responses", {}))["responses"]]
        assert urls == [f"{site_url}/api/items.json", f"{site_url}/api/more.json"]

        runtime.call("open", {"url": f"{site_url}/other.html"})
        assert json.loads(runtime.call("responses", {}))["responses"] == []
    finally:
        runtime.stop()
