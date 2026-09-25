from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """A temp dir short enough to hold Unix sockets.

    macOS caps socket paths at 103 bytes and its per-user temp dir alone takes
    about 50, so pytest's tmp_path leaves no room for a session socket.
    """
    with tempfile.TemporaryDirectory(prefix="surf-", dir="/tmp") as directory:
        yield Path(directory)
