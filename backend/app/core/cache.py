"""Small in-process TTL cache.

Repeated clicks on the same moment of a video are common and each extraction
costs a multimodal model call, so caching materially reduces cost and latency.
It is per-process and non-persistent; the interface is deliberately minimal
(`get`/`set`) so it can be swapped for Redis later without touching services.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(
        self,
        ttl_sec: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_sec
        self._max = max_entries
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, V]] = OrderedDict()

    def get(self, key: str) -> V | None:
        if self._ttl <= 0:
            return None
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: V) -> None:
        if self._ttl <= 0:
            return
        self._data[key] = (self._clock() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)
