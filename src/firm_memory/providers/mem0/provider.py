"""The mem0 provider.

Everything above this file speaks canonical memories; everything below speaks
mem0. The provider owns retrieval mechanics — embeddings, vector search,
ranking — and the platform only guarantees that what comes back conforms to the
firm's contract.

Writes are ``infer=False`` by default. A canonical memory is *already* a
distilled fact that passed the taxonomy and the approval gate; handing it back
to mem0's extractor would re-summarise a summary and lose the provenance the
gate just attached. Inference is opt-in for the one case that needs it:
reconciling a distilled fact against a near-duplicate already in the pool.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ...errors import ProviderError
from ...models import (
    DEFAULT_SEARCH_STATUSES,
    DEFAULT_SEARCH_TIERS,
    Memory,
    MemoryStatus,
    MemoryTier,
)
from ...scope import MemoryScope
from ...taxonomy import MemoryType, fact_extraction_instructions
from ..base import DEFAULT_SEARCH_LIMIT
from .filters import payload_metadata, search_filters
from .mapping import to_memory
from .namespace import Namespace
from .settings import Mem0Settings, build_memory_config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...config import Settings

logger = logging.getLogger(__name__)

#: Ask mem0 for everything and let the platform cut, for a specific reason.
#: mem0 2.x runs a hybrid search — dense vectors, then Postgres full-text over
#: ``text_lemmatized``, then entity boosts — but its ``threshold`` gates the
#: *semantic* score **before** the keyword score is fused in
#: (``mem0/utils/scoring.py``). Its default of 0.1 therefore discards exactly
#: the memories hybrid search exists to find: an exact match on an identifier
#: that embeds poorly — an error code, a ticker, "CBE" — is dropped before its
#: keyword score can rescue it. Score filtering is the platform's job anyway,
#: since ``min_score`` must mean the same thing across providers.
_PROVIDER_THRESHOLD = 0.0


class Mem0Provider:
    """Canonical memory stored in and retrieved from self-hosted mem0 OSS."""

    name = "mem0"

    def __init__(
        self,
        memory: Any,
        *,
        settings: Mem0Settings,
        namespace: Namespace | None = None,
    ) -> None:
        self._memory = memory
        self._settings = settings
        self._namespace = namespace or Namespace(pool_owner=settings.pool_owner)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        memory_factory: Any = None,
    ) -> Mem0Provider:
        """Build the provider from platform settings and the environment.

        ``memory_factory`` is injectable for tests; by default the mem0 SDK is
        imported lazily so importing ``firm_memory`` stays cheap for a consumer
        that has selected a different provider.
        """
        mem0_settings = Mem0Settings.from_env(settings.env)

        if memory_factory is None:

            def memory_factory(config: dict) -> Any:
                from mem0 import Memory as Mem0Memory

                return Mem0Memory.from_config(config)

        try:
            backend = memory_factory(build_memory_config(mem0_settings))
        except Exception as exc:
            raise ProviderError(f"Failed to initialise the mem0 backend: {exc}") from exc

        return cls(backend, settings=mem0_settings)

    # --- extraction support (read-only) --------------------------------------

    @property
    def backend(self) -> Any:
        """The underlying mem0 ``Memory``.

        Exposed for the extractor, which reuses mem0's own prompt and LLM but
        deliberately stops before mem0 writes anything.
        """
        return self._memory

    @property
    def extraction_instructions(self) -> str:
        """The firm's taxonomy instructions, as handed to mem0's extractor."""
        return fact_extraction_instructions()

    def extraction_context(
        self,
        scope: MemoryScope,
        query: str,
        *,
        limit: int = 10,
    ) -> list[tuple[str, str]]:
        """Return ``(id, text)`` for memories already in the pool near *query*.

        Deduplication context for extraction: shown to the model so it returns
        only what is genuinely new. Scoped like any other read, so a fact is
        deduplicated against the pool it would actually join. Failure is not
        fatal — extraction without context yields duplicates, which the approval
        queue catches, whereas raising would lose the whole ingestion run.
        """
        try:
            found = self.search(query, scope, limit)
        except ProviderError:
            logger.warning("Could not load deduplication context; extracting without it", exc_info=True)
            return []
        return [(memory.id or "", memory.content) for memory in found]

    # --- MemoryProvider ------------------------------------------------------

    def insert(self, memory: Memory, *, infer: bool = False) -> Memory:
        """Persist *memory* and return it with the provider-assigned id."""
        metadata = {**payload_metadata(memory), **self._namespace.ownership_metadata(memory)}

        try:
            response = self._memory.add(
                memory.content,
                **self._namespace.write_kwargs(memory),
                metadata=metadata,
                infer=infer,
            )
        except Exception as exc:
            raise ProviderError(f"Failed to write memory: {exc}") from exc

        assigned = _first_id(response)
        return memory.with_id(assigned) if assigned else memory

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
        """Return memories relevant to *query* within *scope*, most relevant first."""
        scope = scope or MemoryScope.firm_wide()

        # The pool owner is the top-level entity key mem0 requires; it is ANDed
        # with the scope union below, which is exactly the intent.
        filters = {
            "user_id": self._namespace.pool_owner,
            **search_filters(scope, types=types, tiers=tiers, statuses=statuses, task=task),
        }

        try:
            response = self._memory.search(
                query,
                filters=filters,
                top_k=limit,
                threshold=_PROVIDER_THRESHOLD,
                rerank=self._settings.reranker_enabled,
            )
        except Exception as exc:
            raise ProviderError(f"Failed to search memory: {exc}") from exc

        return _to_memories(_records(response))

    def get(self, memory_id: str) -> Memory | None:
        """Return the memory with *memory_id*, or ``None`` if there is no such memory."""
        try:
            record = self._memory.get(memory_id)
        except Exception as exc:
            raise ProviderError(f"Failed to read memory {memory_id!r}: {exc}") from exc

        if not isinstance(record, Mapping):
            return None
        return to_memory(record)

    def update(self, memory: Memory) -> Memory:
        """Persist changes to an existing memory, identified by ``memory.id``."""
        if not memory.id:
            raise ProviderError("Cannot update a memory that has never been inserted")

        metadata = {**payload_metadata(memory), **self._namespace.ownership_metadata(memory)}

        try:
            self._memory.update(memory.id, text=memory.content, metadata=metadata)
        except Exception as exc:
            raise ProviderError(f"Failed to update memory {memory.id!r}: {exc}") from exc

        return memory.touched()


def _records(response: Any) -> list[Mapping]:
    """Flatten mem0's response shapes (a ``results`` envelope or a bare list)."""
    results = response.get("results", []) if isinstance(response, Mapping) else response

    if isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        return [item for item in results if isinstance(item, Mapping)]
    return []


def _to_memories(records: Sequence[Mapping]) -> list[Memory]:
    """Convert records to canonical memories, dropping any that are unusable."""
    converted = (to_memory(record) for record in records)
    return [memory for memory in converted if memory is not None]


def _first_id(response: Any) -> str | None:
    """Pull the assigned id out of an ``add`` response, tolerating shape drift."""
    for record in _records(response):
        memory_id = record.get("id")
        if memory_id:
            return str(memory_id)
    if isinstance(response, Mapping) and response.get("id"):
        return str(response["id"])
    return None
