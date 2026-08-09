from __future__ import annotations

import io
import json
import os

import pytest

from surf_google_search import cli


pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("SURF_GOOGLE_SEARCH_LIVE") != "1",
    reason="set SURF_GOOGLE_SEARCH_LIVE=1 to run Google compatibility smoke tests",
)
def test_live_google_search_returns_only_the_compact_public_schema() -> None:
    output = io.StringIO()

    exit_code = cli.main(["latest Patchright documentation"], stdout=output)
    payload = json.loads(output.getvalue())

    if payload.get("error", {}).get("type") == "human_intervention_required":
        pytest.skip("Google requires human intervention in the Surf profile")
    assert exit_code == 0
    assert set(payload) == {"ok", "query", "pages", "results", "exhausted"}
    assert payload["ok"] is True
    assert payload["results"], "the stable compatibility query should return an organic result"
    assert len(output.getvalue().encode()) < 12_000
    for result in payload["results"]:
        assert set(result) == {"rank", "title", "url", "snippet", "displayed_date"}
        assert result["url"].startswith(("http://", "https://"))
        assert ":~:text=" not in result["url"]
