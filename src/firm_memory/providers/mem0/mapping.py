"""Translating between mem0 records and canonical memories.

mem0 flattens metadata into the payload on write, and returns it in a mix of
top-level keys and a nested ``metadata`` dict depending on call path and
version. Reading tolerates both rather than pinning one shape, because the
alternative — a silent empty result after a provider upgrade — is the failure
mode this layer exists to prevent.

A record that cannot be turned into a valid canonical memory is dropped rather
than raised on: one malformed row must not fail an agent's whole recall.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime

from ...errors import FirmMemoryError
from ...models import Memory, MemoryStatus, MemoryTier
from ...provenance import Provenance
from ...scope import MemoryScope
from ...taxonomy import MemoryType, coerce_type
from .filters import SCOPE_KEY_PREFIX, STATUS_KEY, TIER_KEY, TYPE_KEY
from .namespace import LAYER_KEY, OWNER_KEY, Layer

logger = logging.getLogger(__name__)

_CONTENT_KEYS = ("memory", "content", "text", "data")


def flatten(record: Mapping) -> dict:
    """Merge a record's nested ``metadata`` with its top-level keys.

    Nested metadata wins: it is what the caller actually wrote, whereas
    top-level keys may be provider bookkeeping.
    """
    nested = record.get("metadata")
    merged = {key: value for key, value in record.items() if key != "metadata"}
    if isinstance(nested, Mapping):
        merged.update(nested)
    return merged


def to_memory(record: Mapping) -> Memory | None:
    """Convert one mem0 record into a canonical memory, or ``None`` if unusable."""
    payload = flatten(record)

    content = next((str(payload[key]) for key in _CONTENT_KEYS if payload.get(key)), "")
    if not content.strip():
        return None

    try:
        scope = _scope_from(payload)
        tier = _enum(MemoryTier, payload.get(TIER_KEY), MemoryTier.DURABLE)
        task = payload.get("run_id") or payload.get("task")
        return Memory(
            content=content,
            type=_type_from(payload),
            scope=scope,
            provenance=Provenance.from_dict(
                {
                    "source": payload.get("source"),
                    "reference": payload.get("reference"),
                    "author": payload.get("author"),
                    "evidence": payload.get("evidence"),
                    "doc_sha": payload.get("doc_sha"),
                    "recorded_at": payload.get("recorded_at"),
                }
            ),
            confidence=_float(payload.get("confidence"), default=0.5),
            status=_enum(MemoryStatus, payload.get(STATUS_KEY), MemoryStatus.ACTIVE),
            tier=tier,
            # Only episodic memory may carry a task; a stray run_id on durable
            # memory is provider bookkeeping, not part of the canonical fact.
            task=task if tier is MemoryTier.EPISODIC else None,
            superseded_by=payload.get("superseded_by"),
            metadata=_extra_metadata(payload),
            id=payload.get("id"),
            created_at=_timestamp(payload.get("created_at")),
            updated_at=_timestamp(payload.get("updated_at")),
            score=_optional_float(payload.get("score")),
        )
    except FirmMemoryError:
        logger.warning("Dropping unusable memory record id=%s", payload.get("id"), exc_info=True)
        return None


def _type_from(payload: Mapping) -> MemoryType:
    """Resolve the taxonomy type, defaulting rather than dropping the row.

    Memory written before the taxonomy was enforced — or by the editor plugin —
    may carry no type. Such a fact is still worth returning; it is simply
    unclassified, which ``task_learnings`` is the honest bucket for.
    """
    raw = payload.get(TYPE_KEY)
    if not raw:
        return MemoryType.TASK_LEARNING
    try:
        return coerce_type(str(raw))
    except FirmMemoryError:
        return MemoryType.TASK_LEARNING


def _scope_from(payload: Mapping) -> MemoryScope:
    """Rebuild scope from the flat ``scope_*`` keys, falling back to entity axes."""
    firm = False
    domains: list[str] = []
    repos: list[str] = []

    for key in payload:
        if not key.startswith(SCOPE_KEY_PREFIX):
            continue
        remainder = key[len(SCOPE_KEY_PREFIX) :]
        kind, _, slug = remainder.partition("_")
        if kind == Layer.FIRM.value and not slug:
            firm = True
        elif kind == Layer.DOMAIN.value and slug:
            domains.append(slug)
        elif kind == Layer.REPO.value and slug:
            repos.append(slug)

    if firm or domains or repos:
        return MemoryScope(firm=firm, domains=tuple(domains), repos=tuple(repos))

    return _legacy_scope(payload)


def _legacy_scope(payload: Mapping) -> MemoryScope:
    """Derive scope from ownership metadata or the entity axes.

    Two cases land here. A memory written by this provider always carries
    ``scope_*`` keys, so it never does. A memory written by the editor plugin,
    or by the pre-platform client that spent ``user_id`` on the partition, does
    — and this keeps an existing pool readable through the new contract instead
    of requiring a migration before the first query returns anything.
    """
    owner = str(payload.get(OWNER_KEY) or payload.get("user_id") or "")
    kind, _, slug = owner.partition(":")

    if slug and kind == Layer.REPO.value:
        return MemoryScope(repos=(slug,))
    if slug and kind == Layer.DOMAIN.value:
        return MemoryScope(domains=(slug,))

    agent = str(payload.get("agent_id") or "")
    if agent:
        return MemoryScope(repos=(agent,))
    return MemoryScope(firm=True)


#: Payload keys the canonical model already carries as first-class fields.
_STRUCTURAL_KEYS = frozenset(
    {
        "id", "memory", "content", "text", "data", "hash", "score", "user_id", "agent_id", "run_id",
        "created_at", "updated_at", "task", "source", "reference", "author", "evidence", "doc_sha",
        "recorded_at", "confidence", "superseded_by", TYPE_KEY, TIER_KEY, STATUS_KEY,
        OWNER_KEY, LAYER_KEY,
    }
)


def _extra_metadata(payload: Mapping) -> dict[str, str]:
    """Everything the caller attached that the canonical model does not model."""
    return {
        key: str(value)
        for key, value in payload.items()
        if key not in _STRUCTURAL_KEYS and not key.startswith(SCOPE_KEY_PREFIX) and value is not None
    }


def _enum(enum_cls, raw, default):
    try:
        return enum_cls(str(raw)) if raw else default
    except ValueError:
        return default


def _float(raw, *, default: float) -> float:
    value = _optional_float(raw)
    return default if value is None else min(1.0, max(0.0, value))


def _timestamp(raw) -> datetime | None:
    """Parse an ISO timestamp, treating anything unparseable as absent."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _optional_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
