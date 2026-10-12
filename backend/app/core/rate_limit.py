"""Per-client sliding-window rate limiter (in-memory, single process).

Sufficient for a local single-user backend; protects the API key from runaway
loops or a leaked port. Replace with a shared store if deployed multi-instance.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from app.core.errors import RateLimitedError


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit: int,
        window_sec: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_sec
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, client_key: str) -> None:
        now = self._clock()
        hits = self._hits[client_key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            raise RateLimitedError(
                f"Too many requests: limit is {self._limit} per {int(self._window)}s."
            )
        hits.append(now)
