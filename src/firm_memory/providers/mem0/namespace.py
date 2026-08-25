"""Projecting canonical scope onto mem0's entity axes.

mem0 OSS exposes three entity axes — ``user_id``, ``agent_id``, ``run_id`` — plus
free-form metadata. There is no ``app_id`` (Platform-only), so this module fixes
how the three are spent:

======================  ==============================================
mem0 axis               firm-memory meaning
======================  ==============================================
``user_id``             the pool owner — one constant for the whole firm
``agent_id``            the repo slug (repo-primary memories only)
``run_id``              the task, e.g. ``mr-4821`` (episodic tier only)
======================  ==============================================

**Why ``user_id`` is not the partition.** It is the obvious place to put
``repo:oms`` — and that is what an earlier design did — but ``Memory.search``
requires at least one of ``user_id``/``agent_id``/``run_id`` at the *top level*
of the filter dict (``mem0/memory/main.py``, "filters must contain at least one
of"). A top-level key is ANDed with everything below it, so spending ``user_id``
on the partition makes a cross-partition ``OR`` inexpressible: either the search
is pinned to one partition, or it has no top-level entity key and mem0 rejects
it outright. ``user_id`` therefore names the pool, and scope is matched on flat
metadata keys, which ``OR`` can range over freely.

**A layer is a provider detail, not a firm concept.** The firm's contract is
:class:`~firm_memory.scope.MemoryScope`, whose atoms are independent. A layer is
simply which kind of atom a partition was cut from, and it survives here because
it still decides where a memory is filed and how a pool is administered.

**The one-write rule.** A memory scoped to three repos is stored *once*, in its
broadest partition, with every atom recorded as a flat metadata key. Writing one
copy per atom would split a single fact into copies that drift apart and that no
single query can reach — the same failure that rules out per-engineer and
per-team scoping.

Owner labels are prefixed (``repo:``, ``domain:``) so an administrative scan
cannot silently mix layers, repos and domains.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...errors import ScopeError
from ...models import Memory, MemoryTier
from ...scope import DOMAIN_PREFIX, FIRM_ATOM, REPO_PREFIX, MemoryScope

#: One pool for the whole firm. Not an engineer, not a team — see
#: :mod:`firm_memory.scope`.
DEFAULT_POOL_OWNER = "firm"
DEFAULT_FIRM_OWNER = DEFAULT_POOL_OWNER  # retained name for existing deployments

OWNER_KEY = "owner"
LAYER_KEY = "layer"


class Layer(StrEnum):
    """Which kind of scope atom a partition was cut from.

    There is deliberately no per-engineer and no per-team layer.
    """

    FIRM = "firm"
    DOMAIN = "domain"
    REPO = "repo"


#: Broadest first. A memory spanning several atoms is filed under the first of
#: these it has, so firm-wide knowledge never ends up recorded under one repo.
LAYER_BREADTH: tuple[Layer, ...] = (Layer.FIRM, Layer.DOMAIN, Layer.REPO)


@dataclass(frozen=True, slots=True)
class Partition:
    """One storage partition, derived from a single scope atom."""

    layer: Layer
    #: The repo or domain slug. ``None`` for the firm layer, which has one partition.
    value: str | None = None

    @classmethod
    def from_atom(cls, atom: str) -> Partition:
        """Parse a canonical scope atom (``"repo:oms"``) into a partition."""
        if atom == FIRM_ATOM:
            return cls(Layer.FIRM)
        if atom.startswith(DOMAIN_PREFIX):
            return cls(Layer.DOMAIN, atom[len(DOMAIN_PREFIX) :])
        if atom.startswith(REPO_PREFIX):
            return cls(Layer.REPO, atom[len(REPO_PREFIX) :])
        raise ScopeError(f"Unrecognised scope atom {atom!r}")

    @property
    def atom(self) -> str:
        """The canonical scope atom this partition came from."""
        return FIRM_ATOM if self.layer is Layer.FIRM else f"{self.layer.value}:{self.value}"

    @property
    def owner(self) -> str:
        """The prefixed owner label recorded on the payload for administration."""
        if self.layer is Layer.FIRM:
            return FIRM_ATOM
        if not self.value:
            raise ScopeError(f"Layer {self.layer.value!r} requires a slug")
        return f"{self.layer.value}:{self.value}"


@dataclass(frozen=True, slots=True)
class Namespace:
    """Turns canonical scope into mem0 entity kwargs and ownership metadata.

    Frozen: scoping is derived, never mutated in place.
    """

    pool_owner: str = DEFAULT_POOL_OWNER

    def partitions(self, scope: MemoryScope) -> tuple[Partition, ...]:
        """Every partition *scope* touches, broadest first."""
        return tuple(
            sorted(
                (Partition.from_atom(atom) for atom in scope.atoms),
                key=lambda partition: LAYER_BREADTH.index(partition.layer),
            )
        )

    def primary(self, scope: MemoryScope) -> Partition:
        """The single partition a memory with *scope* is filed under."""
        partitions = self.partitions(scope)
        if not partitions:  # pragma: no cover - MemoryScope forbids an empty scope
            raise ScopeError("Cannot write an unscoped memory")
        return partitions[0]

    def entity_kwargs(self, partition: Partition, *, task: str | None = None) -> dict[str, str]:
        """Return the mem0 entity kwargs for *partition*, omitting unset axes.

        ``agent_id`` is set only for the repo layer, where it is the repo
        scoping axis the pool already speaks and the handle for per-repo
        administration. ``run_id`` is set only when a task is supplied, which
        the caller does only for episodic memory: durable and index memory must
        stay retrievable on the next task, and binding it to a ``run_id`` would
        strand it.
        """
        kwargs: dict[str, str] = {"user_id": self.pool_owner}
        if partition.layer is Layer.REPO and partition.value:
            kwargs["agent_id"] = partition.value
        if task:
            kwargs["run_id"] = task
        return kwargs

    def write_kwargs(self, memory: Memory) -> dict[str, str]:
        """Return the entity kwargs *memory* is written under."""
        task = memory.task if memory.tier is MemoryTier.EPISODIC else None
        return self.entity_kwargs(self.primary(memory.scope), task=task)

    def ownership_metadata(self, memory: Memory) -> dict[str, str]:
        """Payload keys recording which partition a memory was filed under."""
        partition = self.primary(memory.scope)
        return {OWNER_KEY: partition.owner, LAYER_KEY: partition.layer.value}
