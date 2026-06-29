from __future__ import annotations

import os
import tempfile


def write_temp_js(code: str, *, prefix: str = "surf-chatgpt-") -> str:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", prefix=prefix, dir="/tmp", delete=False) as handle:
        handle.write(code)
        handle.write("\n")
        return handle.name


def unlink_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
