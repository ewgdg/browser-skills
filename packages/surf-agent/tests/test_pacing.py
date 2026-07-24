from __future__ import annotations

import pytest

from surf_agent.pacing import NATURAL_PACING_PROFILE, Pacer


def test_natural_pacer_samples_profile_bounds_and_sleeps_once() -> None:
    random_calls: list[tuple[float, float]] = []
    sleeps: list[float] = []

    def sample(minimum: float, maximum: float) -> float:
        random_calls.append((minimum, maximum))
        return 0.7

    Pacer.for_name("natural", random_source=sample, sleeper=sleeps.append).pause()

    assert random_calls == [(NATURAL_PACING_PROFILE.minimum_seconds, NATURAL_PACING_PROFILE.maximum_seconds)]
    assert sleeps == [0.7]
    assert (NATURAL_PACING_PROFILE.minimum_seconds, NATURAL_PACING_PROFILE.maximum_seconds) == (0.4, 1.0)


def test_none_pacer_never_samples_or_sleeps() -> None:
    random_calls: list[tuple[float, float]] = []
    sleeps: list[float] = []

    Pacer.for_name(
        "none",
        random_source=lambda minimum, maximum: random_calls.append((minimum, maximum)) or minimum,
        sleeper=sleeps.append,
    ).pause()

    assert random_calls == []
    assert sleeps == []


def test_unknown_pacing_profile_fails_clearly() -> None:
    with pytest.raises(ValueError, match="unsupported pacing profile: fast"):
        Pacer.for_name("fast")
