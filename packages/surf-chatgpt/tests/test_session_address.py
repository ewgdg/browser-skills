from __future__ import annotations

import pytest

from surf_chatgpt.session_address import InvalidSessionAddress, SessionAddress


@pytest.mark.parametrize("value", ["abc123", "ABC_123-xyz"])
def test_session_id_normalizes_to_id_only_public_identity(value: str) -> None:
    address = SessionAddress.parse(value)

    assert address.id == value
    assert address.to_public_json() == {"id": value}
    assert address.canonical_url == f"https://chatgpt.com/c/{value}"
    assert address.thread == f"surf-chatgpt-session-{value}"


def test_exact_canonical_url_normalizes_to_the_same_session_identity() -> None:
    address = SessionAddress.parse("https://chatgpt.com/c/abc_123-X")

    assert address == SessionAddress.parse("abc_123-X")
    assert address.to_public_json() == {"id": "abc_123-X"}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        " abc123",
        "abc123 ",
        ".",
        "abc.123",
        "abc/123",
        "é",
        "http://chatgpt.com/c/abc123",
        "https://www.chatgpt.com/c/abc123",
        "https://chatgpt.com:443/c/abc123",
        "https://user@chatgpt.com/c/abc123",
        "https://chatgpt.com/c/",
        "https://chatgpt.com/c/abc123/",
        "https://chatgpt.com/c/abc123/extra",
        "https://chatgpt.com/c/abc123?model=pro",
        "https://chatgpt.com/c/abc123#answer",
        "https://chatgpt.com/C/abc123",
        "https://example.com/c/abc123",
    ],
)
def test_malformed_session_reference_is_rejected(value: str) -> None:
    with pytest.raises(InvalidSessionAddress):
        SessionAddress.parse(value)
