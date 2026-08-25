"""Failure and latency counters.

Memory is best effort, which is exactly why it needs to be measurable: a
provider that has been timing out for a week looks identical, from the agent's
side, to a pool with nothing useful in it. These counters are what tell those
two apart.

Deliberately in-process and dependency-free — a consumer scrapes or logs
:meth:`Metrics.snapshot` through whatever it already uses. Introducing a metrics
backend here would make memory a hard dependency of the agent, which is the one
thing the reliability design forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class OperationStats:
    """Counters for one operation (``search``, ``get``, ``insert``, ...)."""

    calls: int = 0
    failures: int = 0
    timeouts: int = 0
    total_seconds: float = 0.0

    @property
    def average_seconds(self) -> float:
        """Mean latency across all calls, successful or not."""
        return self.total_seconds / self.calls if self.calls else 0.0

    def as_dict(self) -> dict:
        """Render for logging or scraping."""
        return {
            "calls": self.calls,
            "failures": self.failures,
            "timeouts": self.timeouts,
            "average_seconds": round(self.average_seconds, 4),
        }


class Metrics:
    """Thread-safe counters, one bucket per operation."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._stats: dict[str, OperationStats] = {}

    def record(self, operation: str, *, seconds: float, failed: bool = False, timed_out: bool = False) -> None:
        """Record one completed (or failed) call."""
        with self._lock:
            stats = self._stats.setdefault(operation, OperationStats())
            stats.calls += 1
            stats.total_seconds += seconds
            if failed:
                stats.failures += 1
            if timed_out:
                stats.timeouts += 1

    def snapshot(self) -> dict[str, dict]:
        """Return a copy of the current counters."""
        with self._lock:
            return {operation: stats.as_dict() for operation, stats in self._stats.items()}

    def for_operation(self, operation: str) -> OperationStats:
        """Return the (live) stats bucket for *operation*."""
        with self._lock:
            return self._stats.setdefault(operation, OperationStats())


#: Recorded operation names, so dashboards and tests agree on spelling.
SEARCH = "search"
GET = "get"
INSERT = "insert"
UPDATE = "update"
