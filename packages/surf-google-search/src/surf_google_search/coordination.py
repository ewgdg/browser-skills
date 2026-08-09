from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import IO, Protocol


class ExecutionLease(Protocol):
    def release(self) -> None: ...


class SearchCoordinator(Protocol):
    def acquire(self) -> ExecutionLease: ...

    def blocked_thread(self) -> str | None: ...

    def block(self, thread: str) -> None: ...

    def clear_block(self) -> None: ...


def coordinator_for_profile(
    profile_path: Path,
    *,
    state_root: Path,
) -> FileSearchCoordinator:
    profile_identity = str(profile_path.expanduser().resolve(strict=False))
    profile_key = hashlib.sha256(os.fsencode(profile_identity)).hexdigest()
    profile_state = state_root / "google-search" / profile_key
    return FileSearchCoordinator(
        lock_path=profile_state / "search.lock",
        marker_path=profile_state / "challenge.json",
    )


class _FileLease:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor < 0:
            return
        descriptor = self._descriptor
        self._descriptor = -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class FileSearchCoordinator:
    def __init__(self, *, lock_path: Path, marker_path: Path) -> None:
        self._lock_path = lock_path
        self._marker_path = marker_path

    def acquire(self) -> ExecutionLease:
        self._prepare_parent(self._lock_path)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            os.close(descriptor)
            raise
        return _FileLease(descriptor)

    def blocked_thread(self) -> str | None:
        try:
            value = json.loads(self._marker_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("thread"), str) or not value["thread"]:
            raise ValueError("challenge marker is invalid")
        return value["thread"]

    def block(self, thread: str) -> None:
        if not thread:
            raise ValueError("challenge thread must not be empty")
        self._prepare_parent(self._marker_path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._marker_path.name}.",
            dir=self._marker_path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                self._write_marker(stream, thread)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._marker_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def clear_block(self) -> None:
        self._marker_path.unlink(missing_ok=True)

    @staticmethod
    def _prepare_parent(path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def _write_marker(stream: IO[str], thread: str) -> None:
        json.dump({"thread": thread}, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")


class _NoopLease:
    def release(self) -> None:
        pass


class NoopSearchCoordinator:
    def acquire(self) -> ExecutionLease:
        return _NoopLease()

    def blocked_thread(self) -> str | None:
        return None

    def block(self, thread: str) -> None:
        pass

    def clear_block(self) -> None:
        pass
