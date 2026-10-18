"""Coalesce identical concurrent operations into one, and stop it when nobody waits for it.

A double click, a retry while the first request is still running, or two tabs on the same
moment would otherwise each pay for the same model calls. Callers with the same key share one
task. Its progress events reach every caller that is still waiting, and a caller that joins
late first receives the latest event. The shared task is cancelled only when the last caller
has gone (for example, every client disconnected). A provider may still bill a request it had
already accepted; cancelling stops the calls that have not been made yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")
Listener = Callable[[E], Awaitable[None]]


@dataclass
class _Flight(Generic[T, E]):
    task: asyncio.Future[T] | None = None
    waiters: int = 0
    listeners: list[Listener[E]] = field(default_factory=list)
    last_event: E | None = None


class SingleFlight(Generic[T, E]):
    def __init__(self) -> None:
        self._flights: dict[str, _Flight[T, E]] = {}

    def in_flight(self) -> int:
        return len(self._flights)

    async def run(
        self,
        key: str,
        start: Callable[[Listener[E]], Awaitable[T]],
        listener: Listener[E] | None = None,
    ) -> tuple[T, bool]:
        """Run `start(emit)` once per key; returns (result, joined_an_existing_flight)."""
        flight = self._flights.get(key)
        joined = flight is not None
        if flight is None:
            flight = _Flight()
            self._flights[key] = flight

            async def emit(event: E) -> None:
                flight.last_event = event
                for fn in list(flight.listeners):
                    try:
                        await fn(event)
                    except Exception:  # one caller's broken stream must not stop the others
                        flight.listeners.remove(fn)

            def forget(_: asyncio.Future[T]) -> None:
                if self._flights.get(key) is flight:
                    del self._flights[key]

            flight.task = asyncio.ensure_future(start(emit))
            flight.task.add_done_callback(forget)
        elif listener is not None and flight.last_event is not None:
            await listener(flight.last_event)

        if listener is not None:
            flight.listeners.append(listener)
        flight.waiters += 1
        assert flight.task is not None
        try:
            return await asyncio.shield(flight.task), joined
        finally:
            flight.waiters -= 1
            if listener is not None and listener in flight.listeners:
                flight.listeners.remove(listener)
            if flight.waiters == 0 and not flight.task.done():
                flight.task.cancel()  # every caller has gone
