"""A small fixed-window rate limiter.

The token is the real access control; this is the second line. Its job is to
make an exposed endpoint unrewarding to hammer — token guessing, or a script
that loops on approve — not to shape traffic. A reviewer clicking through a
queue never comes close to the limit.

Deliberately in-process and approximate. A shared limiter would need a store of
its own, and this service is one process serving a handful of people; a
per-process bound is the honest scope of what it can promise, and the docstring
says so rather than implying a guarantee across replicas.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

WINDOW_SECONDS = 60.0
#: Above this many tracked clients the table is swept. Bounded so a spray of
#: forged source addresses cannot grow it without limit.
_SWEEP_AT = 1024


@dataclass
class RateLimiter:
    """Counts requests per client in a fixed window."""

    limit: int
    window_seconds: float = WINDOW_SECONDS
    _counts: dict[str, tuple[float, int]] = field(
        default_factory=lambda: defaultdict(lambda: (0.0, 0)), init=False, repr=False
    )
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def allow(self, client: str) -> bool:
        """Whether *client* may make one more request right now."""
        now = monotonic()
        with self._lock:
            if len(self._counts) > _SWEEP_AT:
                self._sweep(now)

            started, count = self._counts[client]
            if now - started >= self.window_seconds:
                self._counts[client] = (now, 1)
                return True
            if count >= self.limit:
                return False
            self._counts[client] = (started, count + 1)
            return True

    def _sweep(self, now: float) -> None:
        """Drop clients whose window has closed. Caller holds the lock."""
        expired = [
            client for client, (started, _) in self._counts.items() if now - started >= self.window_seconds
        ]
        for client in expired:
            del self._counts[client]
