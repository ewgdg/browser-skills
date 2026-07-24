from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PacingProfile:
    minimum_seconds: float
    maximum_seconds: float


NATURAL_PACING_PROFILE = PacingProfile(minimum_seconds=0.4, maximum_seconds=1.0)
PACING_PROFILE_NAMES = ("natural", "none")


class Pacer:
    def __init__(
        self,
        profile: PacingProfile | None,
        *,
        random_source: Callable[[float, float], float] = random.uniform,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._profile = profile
        self._random_source = random_source
        self._sleeper = sleeper

    @classmethod
    def for_name(
        cls,
        name: str,
        *,
        random_source: Callable[[float, float], float] = random.uniform,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> Pacer:
        if name == "natural":
            return cls(NATURAL_PACING_PROFILE, random_source=random_source, sleeper=sleeper)
        if name == "none":
            return cls(None, random_source=random_source, sleeper=sleeper)
        raise ValueError(f"unsupported pacing profile: {name}")

    def pause(self) -> None:
        if self._profile is None:
            return
        duration = self._random_source(self._profile.minimum_seconds, self._profile.maximum_seconds)
        self._sleeper(duration)
