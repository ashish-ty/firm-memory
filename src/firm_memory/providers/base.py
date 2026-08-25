"""The smallest useful provider interface.

Deliberately **not** abstracted in V1 (HLD §4): reranking, query normalisation,
a retrieval engine, result validation, multi-provider federation. If the
provider's own retrieval is good enough, it is used directly. Extra layers get
introduced when an evaluation demonstrates a concrete need, not in anticipation
of one.

The design sketch listed ``insert`` and ``search``. ``get`` and ``update`` are
here because the V1 MCP surface includes ``memory_get`` (citation and
verification) and ``memory_correct`` (reporting a stale memory), and neither can
be served without them. That is the whole interface — four methods.

Export and import are a **migration** capability, not part of the runtime
interface; a provider that supports them implements
:class:`MigratableProvider` in addition.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Protocol, runtime_checkable

from ..models import (
    DEFAULT_SEARCH_STATUSES,
    DEFAULT_SEARCH_TIERS,
    Memory,
    MemoryStatus,
    MemoryTier,
)
from ..scope import MemoryScope
from ..taxonomy import MemoryType

DEFAULT_SEARCH_LIMIT = 10


@runtime_checkable
class MemoryProvider(Protocol):
    """Storage and retrieval for canonical :class:`~firm_memory.models.Memory`.

    Implementations receive and return canonical memories only. Anything
    provider-shaped — entity axes, payload keys, filter dialects — stays behind
    this boundary.
    """

    name: str

    def insert(self, memory: Memory) -> Memory:
        """Persist *memory* and return it with the provider-assigned id."""
        ...

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
        """Return memories relevant to *query* within *scope*, most relevant first.

        ``query``, ``scope`` and ``limit`` are the substance of the contract; the
        keyword filters are the firm's guarantees about what a search may return
        and every implementation must honour them. In particular ``tiers``
        defaults to excluding episodic memory: to a vector store an absent task
        filter means "don't care", not "unset", so ignoring this default leaks
        every task's scratch state into ordinary recall.
        """
        ...

    def get(self, memory_id: str) -> Memory | None:
        """Return the memory with *memory_id*, or ``None`` if there is no such memory.

        Not scope- or status-filtered: this serves citation and verification, so
        a superseded or disputed memory must still be reachable by id.
        """
        ...

    def update(self, memory: Memory) -> Memory:
        """Persist changes to an existing memory, identified by ``memory.id``."""
        ...


@runtime_checkable
class MigratableProvider(Protocol):
    """Optional bulk capability used to move a pool between providers.

    Kept off :class:`MemoryProvider` on purpose: a provider is useful without
    it, and requiring it would raise the bar for adding one.
    """

    def export_all(self) -> Iterator[Memory]:
        """Yield every stored memory in canonical form."""
        ...

    def import_all(self, memories: Iterable[Memory]) -> int:
        """Insert *memories* verbatim, preserving ids where possible. Returns the count."""
        ...
