"""Filter construction for mem0 OSS.

A useful recall almost always spans scope atoms: this repo's knowledge, its
domains, and the firm's standards. All of them are unioned into one filter tree
and served by a single ``search()`` call rather than N round trips.

Three OSS constraints shape the output here, all verified against
``Memory._process_metadata_filters`` and ``pgvector._build_filter_conditions``
and pinned by the contract tests:

1. **``OR`` branches must be flat dicts.** Top-level ``AND`` is flattened by the
   preprocessor, but an ``AND`` *nested inside* ``OR`` is passed through
   verbatim, and pgvector then compiles the literal key into
   ``payload->>'AND' = ANY(...)``, which matches nothing. Keys within a branch
   are implicitly ANDed, so the flat form is both correct and simpler.
2. **Metadata is filtered by flat top-level keys.** ``Memory._create_memory``
   flattens the caller's metadata straight into the payload. The Platform's
   nested ``{"metadata": {"type": ...}}`` form raises
   ``Unsupported metadata filter operator: type`` on OSS.
3. **A list value means "one of".** ``pgvector`` compiles it to
   ``payload->>key = ANY(...)``, which is what makes multi-tier and multi-status
   filtering expressible inside a flat branch.

**Why scope is matched on metadata and not on ``user_id``.** A memory covering
``oms`` and ``gateway`` is stored once, under one of them. Filtering a search by
``user_id`` would therefore miss it from the other. Each scope atom is instead
written as its own flat key (``scope_repo_gateway``), so any atom in the query
finds the memory, and equality — all pgvector offers — is enough.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...errors import ScopeError
from ...models import Memory, MemoryStatus, MemoryTier
from ...scope import MemoryScope
from ...taxonomy import MemoryType

SCOPE_KEY_PREFIX = "scope_"
#: Presence marker for a scope atom. A constant value keeps matching to plain
#: equality, which every candidate provider supports.
SCOPE_PRESENT = "1"

TYPE_KEY = "type"
TIER_KEY = "tier"
STATUS_KEY = "status"

#: Keys a caller may never set through ``metadata_filters``: overriding any of
#: them would silently widen or break scoping.
RESERVED_FILTER_KEYS = frozenset({"user_id", "agent_id", "run_id", TYPE_KEY, TIER_KEY, STATUS_KEY})


def atom_key(atom: str) -> str:
    """Return the flat payload key marking membership of scope *atom*.

    ``"repo:oms"`` -> ``"scope_repo_oms"``, ``"firm"`` -> ``"scope_firm"``.
    """
    return f"{SCOPE_KEY_PREFIX}{atom.replace(':', '_')}"


def scope_metadata(scope: MemoryScope) -> dict[str, str]:
    """Render *scope* as the flat payload keys a search matches on."""
    return {atom_key(atom): SCOPE_PRESENT for atom in scope.atoms}


def search_filters(
    scope: MemoryScope,
    *,
    types: Sequence[MemoryType] | None = None,
    tiers: Sequence[MemoryTier] = (),
    statuses: Sequence[MemoryStatus] = (),
    task: str | None = None,
    metadata_filters: Mapping[str, str] | None = None,
) -> dict:
    """Build one filter tree covering every atom in *scope*.

    A single atom returns its bare flat filter — wrapping one branch in ``OR``
    only costs the vector store work.
    """
    atoms = scope.atoms
    if not atoms:  # pragma: no cover - MemoryScope forbids an empty scope
        raise ScopeError("At least one scope atom is required for retrieval")

    common = _common_conditions(
        types=types, tiers=tiers, statuses=statuses, task=task, metadata_filters=metadata_filters
    )
    branches = [{atom_key(atom): SCOPE_PRESENT, **common} for atom in atoms]

    if len(branches) == 1:
        return branches[0]
    return {"OR": branches}


def _common_conditions(
    *,
    types: Sequence[MemoryType] | None,
    tiers: Sequence[MemoryTier],
    statuses: Sequence[MemoryStatus],
    task: str | None,
    metadata_filters: Mapping[str, str] | None,
) -> dict:
    """Conditions repeated into every branch, since AND-inside-OR is unusable."""
    conditions: dict = {}

    if types:
        conditions[TYPE_KEY] = _values(types)
    if tiers:
        conditions[TIER_KEY] = _values(tiers)
    if statuses:
        conditions[STATUS_KEY] = _values(statuses)
    if task:
        conditions["run_id"] = task

    for key, value in (metadata_filters or {}).items():
        if key in RESERVED_FILTER_KEYS or key.startswith(SCOPE_KEY_PREFIX):
            raise ScopeError(
                f"Metadata filter key {key!r} would override scoping; use the scope/tier/type arguments instead"
            )
        conditions[key] = value

    return conditions


def _values(items: Sequence) -> str | list[str]:
    """Render an enum sequence as a single value or a list ("one of")."""
    rendered = list(dict.fromkeys(item.value for item in items))
    return rendered[0] if len(rendered) == 1 else rendered


def payload_metadata(memory: Memory) -> dict[str, str]:
    """Flat payload written alongside a memory, carrying everything a filter needs.

    Scope keys are stripped from caller metadata and re-derived from the
    memory's own scope. Without that, a caller could set ``scope_firm`` on a
    repo-scoped memory and have it answer firm-wide queries — scope would stop
    being a contract and become a suggestion.
    """
    payload: dict[str, str] = {
        **{
            key: str(value)
            for key, value in memory.metadata.items()
            if not key.startswith(SCOPE_KEY_PREFIX)
        },
        "source": memory.provenance.source,
        "confidence": f"{memory.confidence:.4f}",
        TYPE_KEY: memory.type.value,
        TIER_KEY: memory.tier.value,
        STATUS_KEY: memory.status.value,
        **scope_metadata(memory.scope),
    }

    for key, value in (
        ("reference", memory.provenance.reference),
        ("author", memory.provenance.author),
        ("evidence", memory.provenance.evidence),
        ("doc_sha", memory.provenance.doc_sha),
        ("recorded_at", memory.provenance.recorded_at.isoformat()),
        ("superseded_by", memory.superseded_by),
        ("task", memory.task),
    ):
        if value:
            payload[key] = value

    return payload
