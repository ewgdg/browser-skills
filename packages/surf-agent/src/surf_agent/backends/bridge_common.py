from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

STALE_REF_MESSAGE = "Ref {ref!r} not found in the current page snapshot. Capture a new snapshot."
CLOSED_TARGET_MESSAGE = "Target page, context or browser has been closed"
STARTUP_PAGE_URLS = {"", "about:blank", "about:home", "about:newtab", "chrome://newtab/"}
SNAPSHOT_DEPTH: int | None = None
SNAPSHOT_BOXES = False
# Playwright's aria-ref selector accepts AI snapshot refs from main frames and iframes.
NATIVE_ARIA_REF_PATTERN = re.compile(r"^(?:f\d+)?e\d+$")


@dataclass
class PageSlot:
    page: Any
    page_token: int


class BridgeRequestHandler(BaseHTTPRequestHandler):
    runtime: Any

    def do_GET(self) -> None:
        if urlparse(self.path).path != "/health":
            self._write(404, {"error": "not found"})
            return
        self._write(200, {"status": "ok"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/call":
            self._write(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length") or "0")
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            name = str(payload.get("name") or "")
            if name == "stop":
                result = self.runtime.stop()
                self._write(200, {"result": result})
                self.server._BaseServer__shutdown_request = True
                return
            result = self.runtime.call(name, payload.get("args") or {})
        except Exception as exc:
            self._write(500, {"error": str(exc)})
            return
        self._write(200, {"result": result})
        after_response = getattr(self.runtime, "after_response", None)
        if after_response is not None:
            after_response(name)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
