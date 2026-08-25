"""An in-process provider: substring matching over a dict.

Two honest uses. It lets the test suite exercise the full platform — API,
lifecycle, MCP — without a database or an embedding model, so those tests
verify firm semantics rather than mem0's behaviour. And it gives a consumer a
working default before the real store is provisioned, which matters because
memory is meant to be optional infrastructure.

It is **not** a retrieval engine: ranking is term overlap, not semantics. Never
select it in production.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import count
from re import findall

from ..models import (
    DEFAULT_SEARCH_STATUSES,
    DEFAULT_SEARCH_TIERS,
    Memory,
    MemoryStatus,
    MemoryTier,
)
from ..scope import MemoryScope
from ..taxonomy import MemoryType
from .base import DEFAULT_SEARCH_LIMIT


class InMemoryProvider:
    """Canonical memories held in a dict, searched by term overlap."""

    name = "memory"

    def __init__(self) -> None:
        self._store: dict[str, Memory] = {}
        self._ids = count(1)

    # --- MemoryProvider ------------------------------------------------------

    def insert(self, memory: Memory) -> Memory:
        """Store *memory* under a freshly assigned id."""
        memory_id = memory.id or f"mem-{next(self._ids)}"
        now = datetime.now(UTC)
        stored = Memory.from_dict(
            {
                **memory.to_dict(),
                "id": memory_id,
                "created_at": (memory.created_at or now).isoformat(),
                "updated_at": now.isoformat(),
            }
        )
        self._store[memory_id] = stored
        return stored

    def search(
        self,
        query: str,
        scope: MemoryScope | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        types: Sequence[MemoryType] | None = None,
        tiers: Sequence[MemoryTier] = DEFAULT_SEARCH_TIERS,
        statuses: Sequence[MemoryStatus] = DEFAULT_SEARCH_STATUSES,
        task: str | None = None,
    ) -> list[Memory]:
        """Return stored memories matching *query*, most overlapping first."""
        terms = _terms(query)
        wanted_types = set(types or ())

        scored: list[tuple[float, Memory]] = []
        for memory in self._store.values():
            if scope is not None and not scope.overlaps(memory.scope):
                continue
            if tiers and memory.tier not in tiers:
                continue
            if statuses and memory.status not in statuses:
                continue
            if wanted_types and memory.type not in wanted_types:
                continue
            if task is not None and memory.task != task:
                continue

            score = _overlap(terms, _terms(memory.content))
            if score > 0:
                scored.append((score, memory))

        scored.sort(key=lambda pair: (-pair[0], pair[1].id or ""))
        return [memory.with_score(score) for score, memory in scored[:limit]]

    def get(self, memory_id: str) -> Memory | None:
        """Return the memory with *memory_id*, regardless of scope or status."""
        return self._store.get(memory_id)

    def update(self, memory: Memory) -> Memory:
        """Replace the stored memory identified by ``memory.id``."""
        if not memory.id or memory.id not in self._store:
            raise KeyError(f"No memory with id {memory.id!r}")
        stored = memory.touched()
        self._store[memory.id] = stored
        return stored

    # --- MigratableProvider --------------------------------------------------

    def export_all(self):
        """Yield every stored memory in canonical form."""
        yield from self._store.values()

    def import_all(self, memories) -> int:
        """Store *memories* verbatim, preserving ids and timestamps.

        Deliberately not routed through :meth:`insert`: a migration that
        restamped ``updated_at`` would silently rewrite the pool's history, and
        history is much of what makes the pool worth migrating.
        """
        imported = 0
        for memory in memories:
            memory_id = memory.id or f"mem-{next(self._ids)}"
            self._store[memory_id] = memory if memory.id else memory.with_id(memory_id)
            imported += 1
        return imported


def _terms(text: str) -> set[str]:
    """Lowercase word set, which is as much lexical analysis as this warrants."""
    return set(findall(r"[a-z0-9]+", text.lower()))


def _overlap(query_terms: set[str], content_terms: set[str]) -> float:
    """Fraction of query terms present in the content."""
    if not query_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)
