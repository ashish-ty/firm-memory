"""The canonical memory representation.

This model belongs to the firm and must not depend on any provider (HLD §5). It
is what makes provider migration possible: every provider maps *to and from*
this shape, so exporting from one and importing into another is a
representation-preserving move rather than a rewrite.

Two axes are carried on every memory and are easy to confuse:

* :class:`MemoryStatus` — *is this fact endorsed?* It moves through the approval
  and correction lifecycle (:mod:`firm_memory.lifecycle`).
* :class:`MemoryTier` — *how long does this fact live, and how was it written?*
  It is the write-path axis: per-task scratch, distilled durable knowledge, or a
  verbatim index card.

The two are orthogonal. A memory can be ``ACTIVE`` and ``EPISODIC`` (a finding
accepted on this MR) or ``PROPOSED`` and ``DURABLE`` (a candidate convention
awaiting a human).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from .errors import InvalidInputError
from .provenance import Provenance
from .scope import MemoryScope
from .taxonomy import MemoryType, coerce_type

_EMPTY_METADATA: Mapping[str, str] = MappingProxyType({})


class MemoryStatus(StrEnum):
    """Where a memory sits in the approval and correction lifecycle."""

    #: Suggested by an agent or an ingestion job; never returned by a default search.
    PROPOSED = "proposed"
    #: Endorsed and retrievable.
    ACTIVE = "active"
    #: Someone reported it as stale or wrong; retrievable but demoted and flagged.
    DISPUTED = "disputed"
    #: Replaced by a newer memory, named in ``superseded_by``. Kept, not deleted.
    SUPERSEDED = "superseded"
    #: Reviewed and turned down. Kept so the same candidate is not re-proposed forever.
    REJECTED = "rejected"


class MemoryTier(StrEnum):
    """Lifetime and write path — orthogonal to :class:`MemoryStatus`."""

    #: Per-task working memory (findings and their dispositions on one MR).
    #: Task-scoped and never part of a normal recall.
    EPISODIC = "episodic"
    #: Durable knowledge, written through the distillation/approval gate.
    DURABLE = "durable"
    #: One verbatim card per closed issue or MR. Deliberately document-shaped:
    #: fact extraction would destroy the symptom -> root cause -> fix structure
    #: that makes it retrievable months later.
    INDEX = "index"


#: What a plain search returns. Episodic memory is excluded by default: absence
#: of a task filter means "don't care" to a vector store, not "unset", so an
#: unguarded search would surface every MR's scratch state. This default is the
#: guard, and it is covered by a contract test.
DEFAULT_SEARCH_TIERS: tuple[MemoryTier, ...] = (MemoryTier.DURABLE, MemoryTier.INDEX)

#: What a plain search returns. Proposals await a human; rejected memory stays
#: out; superseded memory is reachable by id but should not be recalled.
DEFAULT_SEARCH_STATUSES: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE, MemoryStatus.DISPUTED)

DEFAULT_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class Memory:
    """One durable engineering fact, in the firm's canonical form."""

    content: str
    type: MemoryType
    scope: MemoryScope
    provenance: Provenance = field(default_factory=Provenance)
    confidence: float = DEFAULT_CONFIDENCE
    status: MemoryStatus = MemoryStatus.PROPOSED
    tier: MemoryTier = MemoryTier.DURABLE
    #: The task (MR/issue) this memory belongs to. Only meaningful for the
    #: episodic tier; durable and index memory must outlive any single task.
    task: str | None = None
    #: Id of the memory that replaced this one. Supersession replaces deletion:
    #: the firm keeps its history rather than losing the record of a decision.
    superseded_by: str | None = None
    metadata: Mapping[str, str] = field(default=_EMPTY_METADATA)
    id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: Retrieval relevance for this result. Transient — never persisted.
    score: float | None = None

    def __post_init__(self) -> None:
        content = (self.content or "").strip()
        if not content:
            raise InvalidInputError("A memory requires non-empty content")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "type", coerce_type(self.type))

        if not 0.0 <= self.confidence <= 1.0:
            raise InvalidInputError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")

        if self.task is not None and self.tier is not MemoryTier.EPISODIC:
            raise InvalidInputError(
                f"tier {self.tier.value!r} must not be task-scoped: durable and index memory "
                "exists to be found on the next task, and binding it to one strands it"
            )
        if self.tier is MemoryTier.EPISODIC and not self.task:
            raise InvalidInputError("episodic memory requires a task; it is per-task working memory")

        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    # --- immutable derivation ------------------------------------------------

    def with_id(self, memory_id: str) -> Memory:
        """Return a copy carrying the provider-assigned id."""
        return replace(self, id=memory_id)

    def with_score(self, score: float | None) -> Memory:
        """Return a copy carrying a retrieval relevance score."""
        return replace(self, score=score)

    def touched(self) -> Memory:
        """Return a copy stamped as updated now."""
        return replace(self, updated_at=datetime.now(UTC))

    # --- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        """Render the canonical JSON form used by MCP responses and exports."""
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type.value,
            "scope": self.scope.to_dict(),
            "provenance": self.provenance.to_dict(),
            "confidence": self.confidence,
            "status": self.status.value,
            "tier": self.tier.value,
            "task": self.task,
            "superseded_by": self.superseded_by,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, raw: Mapping) -> Memory:
        """Rebuild a memory from its canonical JSON form."""
        return cls(
            content=raw.get("content", ""),
            type=coerce_type(raw.get("type", "")),
            scope=MemoryScope.from_dict(raw.get("scope")),
            provenance=Provenance.from_dict(raw.get("provenance")),
            confidence=float(raw.get("confidence", DEFAULT_CONFIDENCE)),
            status=MemoryStatus(raw.get("status", MemoryStatus.PROPOSED.value)),
            tier=MemoryTier(raw.get("tier", MemoryTier.DURABLE.value)),
            task=raw.get("task"),
            superseded_by=raw.get("superseded_by"),
            metadata=raw.get("metadata") or {},
            id=raw.get("id"),
            created_at=_parse(raw.get("created_at")),
            updated_at=_parse(raw.get("updated_at")),
            score=raw.get("score"),
        )


def _parse(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
