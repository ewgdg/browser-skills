from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "live: opt-in tests against live browser websites")
