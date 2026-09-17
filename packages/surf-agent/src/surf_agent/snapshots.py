from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

if TYPE_CHECKING:
    from .backends.base import AgentPage
from .constants import (
    SNAPSHOT_DIFF_MAX_HUNKS,
    SNAPSHOT_DIFF_MAX_RATIO,
    SNAPSHOT_DIFF_MIN_SAVED_CHARS,
)


@dataclass(frozen=True)
class SnapshotCapture:
    text: str
    page_id: int | None
    url: str | None
    title: str | None
    origin: str | None
    url_without_fragment: str | None


@dataclass(frozen=True)
class SnapshotDiffDecision:
    output: str
    used_diff: bool
    reason: str


def snapshot_capture_from_page(*, text: str, page: AgentPage) -> SnapshotCapture:
    return SnapshotCapture(
        text=text,
        page_id=page.page_id,
        url=page.url,
        title=page.title,
        origin=url_origin(page.url),
        url_without_fragment=url_without_fragment(page.url),
    )


def url_origin(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def url_without_fragment(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def unified_snapshot_diff(before: SnapshotCapture, after: SnapshotCapture) -> str:
    return "".join(
        difflib.unified_diff(
            before.text.splitlines(keepends=True),
            after.text.splitlines(keepends=True),
            fromfile="baseline",
            tofile="current",
        )
    )


def count_diff_hunks(diff_text: str) -> int:
    return sum(1 for line in diff_text.splitlines() if line.startswith("@@"))


def choose_snapshot_diff(
    before: SnapshotCapture | None, after: SnapshotCapture
) -> SnapshotDiffDecision:
    if before is None:
        return SnapshotDiffDecision(
            snapshot_fallback_output(after.text, "no baseline"),
            used_diff=False,
            reason="no baseline",
        )
    if before.page_id != after.page_id:
        return SnapshotDiffDecision(
            snapshot_fallback_output(after.text, "page changed"),
            used_diff=False,
            reason="page changed",
        )
    if before.origin and after.origin and before.origin != after.origin:
        return SnapshotDiffDecision(
            snapshot_fallback_output(after.text, "origin changed"),
            used_diff=False,
            reason="origin changed",
        )

    diff_text = unified_snapshot_diff(before, after)
    if not diff_text:
        return SnapshotDiffDecision(
            format_snapshot_header("diff", "no changes"),
            used_diff=True,
            reason="no changes",
        )

    diff_chars = len(diff_text)
    full_chars = len(after.text)
    saved_chars = full_chars - diff_chars
    hunk_count = count_diff_hunks(diff_text)

    if diff_chars > full_chars * SNAPSHOT_DIFF_MAX_RATIO:
        return SnapshotDiffDecision(
            snapshot_fallback_output(after.text, "diff too large"),
            used_diff=False,
            reason="diff too large",
        )
    if saved_chars < SNAPSHOT_DIFF_MIN_SAVED_CHARS:
        return SnapshotDiffDecision(
            snapshot_fallback_output(
                after.text, f"saved chars < {SNAPSHOT_DIFF_MIN_SAVED_CHARS}"
            ),
            used_diff=False,
            reason="saved chars too small",
        )
    if hunk_count > SNAPSHOT_DIFF_MAX_HUNKS:
        return SnapshotDiffDecision(
            snapshot_fallback_output(after.text, f"hunks > {SNAPSHOT_DIFF_MAX_HUNKS}"),
            used_diff=False,
            reason="too many hunks",
        )
    return SnapshotDiffDecision(
        format_snapshot_header("diff", "") + diff_text, used_diff=True, reason=""
    )


def snapshot_fallback_output(snapshot_text: str, reason: str) -> str:
    return format_snapshot_header("fallback", reason) + snapshot_text


def format_snapshot_header(kind: str, reason: str) -> str:
    if not reason:
        return ""
    label = "snapshot-diff" if kind == "diff" else "snapshot fallback"
    return f"# {label}: {reason}\n"
