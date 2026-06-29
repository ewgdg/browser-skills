from __future__ import annotations

import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

ACTIONABLE_SELECTOR = "a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]"
STALE_REF_MESSAGE = "Ref {ref!r} not found in the current page snapshot. Capture a new snapshot."
CLOSED_TARGET_MESSAGE = "Target page, context or browser has been closed"
STARTUP_PAGE_URLS = {"", "about:blank", "about:home", "about:newtab", "chrome://newtab/"}
SNAPSHOT_DEPTH: int | None = None
SNAPSHOT_BOXES = False
CSS_PATH_SCRIPT = """
el => {
  if (!el || !el.tagName) return null;
  const esc = globalThis.CSS && CSS.escape ? CSS.escape : value => String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  const parts = [];
  for (let node = el; node && node.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
    let part = node.tagName.toLowerCase();
    if (node.id) {
      parts.unshift(`${part}#${esc(node.id)}`);
      break;
    }
    const parent = node.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(child => child.tagName === node.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
    }
    parts.unshift(part);
  }
  return parts.join(' > ');
}
"""


@dataclass(frozen=True)
class TargetFingerprint:
    tag: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    bbox: dict[str, float] | None = None


@dataclass(frozen=True)
class RefTarget:
    ref: str
    selector: str
    index: int
    css_path: str | None
    fingerprint: TargetFingerprint


@dataclass
class PageSlot:
    page: Any
    page_token: int
    ref_map: dict[str, RefTarget] = field(default_factory=dict)


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

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def format_snapshot_node(fingerprint: TargetFingerprint) -> str:
    label = fingerprint.role or fingerprint.tag or "element"
    name = fingerprint.name or fingerprint.text
    name = " ".join(name.split())[:160]
    return f'{label} "{name}"' if name else label


def fingerprint_matches(expected: TargetFingerprint, actual: TargetFingerprint) -> bool:
    if expected.tag and actual.tag and expected.tag != actual.tag:
        return False
    expected_labels = {value for value in (expected.name, expected.text) if value}
    actual_labels = {value for value in (actual.name, actual.text) if value}
    if expected_labels:
        # Role alone is too broad: many changed buttons share role/tag.
        return bool(expected_labels & actual_labels)
    if expected.role:
        return expected.role == actual.role
    return True


def bbox_from_raw(box: Any) -> dict[str, float] | None:
    if not isinstance(box, dict):
        return None
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        value = box.get(key)
        if isinstance(value, (int, float)):
            result[key] = float(value)
    return result or None
