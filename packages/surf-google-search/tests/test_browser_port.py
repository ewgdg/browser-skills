from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from surf_google_search.browser_port import (
    PageObservationError,
    SearchPageKind,
    SurfBrowserPagePort,
)


@dataclass
class FakeSurfAgent:
    evaluation: dict[str, object]
    calls: list[list[str]] = field(default_factory=list)
    close_calls: int = 0

    def is_open(self) -> bool:
        self.calls.append(["is_open"])
        return True

    def open(self, url: str) -> str:
        self.calls.append(["open", url])
        return ""

    def evaluate(self, code: str) -> object:
        self.calls.append(["eval", code])
        return self.evaluation

    def close(self) -> None:
        self.close_calls += 1


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "results", "results": [], "next_url": None},
        {
            "kind": "exhausted",
            "results": [
                {
                    "title": "Unexpected",
                    "url": "https://example.com/unexpected",
                    "snippet": None,
                    "displayed_date": None,
                }
            ],
            "next_url": None,
        },
        {
            "kind": "unknown",
            "results": [],
            "next_url": "https://www.google.com/search?q=query&start=10",
        },
    ),
)
def test_surf_adapter_rejects_inconsistent_observation_states(
    payload: dict[str, object],
) -> None:
    browser = SurfBrowserPagePort(agent_factory=lambda thread: FakeSurfAgent(payload))

    with pytest.raises(PageObservationError):
        browser.observe("thread-1")


def test_surf_adapter_preserves_more_than_ten_observed_results() -> None:
    payload = {
        "kind": "results",
        "results": [
            {
                "title": f"Result {position}",
                "url": f"https://example.com/{position}",
                "snippet": None,
                "displayed_date": None,
            }
            for position in range(1, 12)
        ],
        "next_url": None,
    }
    browser = SurfBrowserPagePort(agent_factory=lambda thread: FakeSurfAgent(payload))

    observation = browser.observe("thread-1")

    assert len(observation.results) == 11


def test_surf_adapter_consumes_typed_evaluation_value() -> None:
    payload = {
        "kind": "exhausted",
        "results": [],
        "next_url": None,
    }

    class AxiAgent(FakeSurfAgent):
        def evaluate(self, code: str) -> object:
            self.calls.append(["eval", code])
            return payload

    browser = SurfBrowserPagePort(agent_factory=lambda thread: AxiAgent(payload))

    observation = browser.observe("thread-1")

    assert observation.kind is SearchPageKind.EXHAUSTED


def test_surf_adapter_exposes_one_backend_agnostic_page_observation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = FakeSurfAgent(
        {
            "kind": "results",
            "results": [
                {
                    "title": "Patchright",
                    "url": "https://example.com/patchright",
                    "snippet": "Browser automation.",
                    "displayed_date": "Jun 23, 2026",
                }
            ],
            "next_url": "https://www.google.com/search?q=patchright&start=10&num=10",
        }
    )
    created_threads: list[str] = []

    def create_agent(thread: str) -> FakeSurfAgent:
        created_threads.append(thread)
        return agent

    browser = SurfBrowserPagePort(agent_factory=create_agent)

    assert browser.is_open("thread-1") is True
    browser.open("thread-1", "https://www.google.com/search?q=patchright&start=0&num=10")
    observation = browser.observe("thread-1")
    browser.close("thread-1")

    assert created_threads == ["thread-1"]
    assert agent.calls[0] == ["is_open"]
    assert agent.calls[1] == [
        "open",
        "https://www.google.com/search?q=patchright&start=0&num=10",
    ]
    assert agent.calls[2][0] == "eval"
    assert "data-snf" in agent.calls[2][1]
    assert observation.kind is SearchPageKind.RESULTS
    assert observation.results[0].title == "Patchright"
    assert observation.results[0].displayed_date == "Jun 23, 2026"
    assert observation.next_url == "https://www.google.com/search?q=patchright&start=10&num=10"
    assert agent.close_calls == 1
    assert capsys.readouterr().out == ""
