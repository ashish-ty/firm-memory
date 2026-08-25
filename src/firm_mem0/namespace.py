"""The firm-wide namespace contract.

mem0 OSS exposes three entity axes — ``user_id``, ``agent_id``, ``run_id`` — plus
free-form metadata. There is no ``app_id`` (that is Platform-only), so this
module fixes how the three axes are spent:

======================  ==============================================
mem0 axis               firm-mem0 meaning
======================  ==============================================
``user_id``             the memory *owner*, prefixed per layer
``agent_id``            the repo slug (see :mod:`firm_mem0.repo`)
``run_id``              the task (e.g. ``gitlab-issue-4821``)
======================  ==============================================

**An owner is never a person or a team.** The bot is stateless and serves every
engineer identically, and no team owns a set of use cases, so knowledge belongs
to the codebase and to the firm — not to whoever happened to trigger the call.
Per-person or per-team pools would fragment the same fact into copies that no
single query can reach.

Owners are prefixed (``repo:``) so that a bare ``user_id`` scan cannot silently
mix layers or repos. ``Layer.REPO`` derives both ``user_id`` and ``agent_id``
from the same slug: the first partitions the layer, the second is the repo
scoping axis every filter already speaks.

Instances are frozen: scoping is derived, never mutated in place.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType

from .errors import NamespaceError

#: Identity values must round-trip through vector-store filters unchanged.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

REPO_PREFIX = "repo:"
DEFAULT_FIRM_OWNER = "firm"

_EMPTY_METADATA: Mapping[str, str] = MappingProxyType({})


class Layer(str, Enum):
    """Which pool a memory belongs to.

    ``REPO`` is the default write target and the bulk of code memory. ``FIRM``
    holds curated, cross-repo conventions and is normally written by leads, not
    agents. There is deliberately no per-engineer or per-team layer.
    """

    REPO = "repo"
    FIRM = "firm"


@dataclass(frozen=True, slots=True)
class Namespace:
    """Immutable identity used to scope every read and write."""

    repo: str | None = None
    task: str | None = None
    metadata: Mapping[str, str] = field(default=_EMPTY_METADATA)
    firm_owner: str = DEFAULT_FIRM_OWNER

    def __post_init__(self) -> None:
        # Validate at construction so bad identities fail at the boundary rather
        # than silently creating an orphan partition nobody can query.
        object.__setattr__(self, "firm_owner", _validated("firm_owner", self.firm_owner))
        if self.repo is not None:
            object.__setattr__(self, "repo", _validated("repo", self.repo))
        if self.task is not None:
            object.__setattr__(self, "task", _validated("task", self.task))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def owner_for(self, layer: Layer) -> str:
        """Return the ``user_id`` that owns memories in *layer*."""
        if layer is Layer.REPO:
            return f"{REPO_PREFIX}{self.require_repo()}"
        if layer is Layer.FIRM:
            return self.firm_owner
        raise NamespaceError(f"Unknown layer: {layer!r}")

    def entity_kwargs(self, layer: Layer) -> dict[str, str]:
        """Return the mem0 entity kwargs for *layer*, omitting unset axes.

        Only ``REPO`` is task-scoped. ``FIRM`` holds durable facts that must
        stay retrievable on the next task, so binding it to a ``run_id`` would
        strand it; it drops the repo too, since a convention scoped to one repo
        is not a firm convention.
        """
        kwargs: dict[str, str] = {"user_id": self.owner_for(layer)}

        if layer is not Layer.REPO:
            return kwargs

        kwargs["agent_id"] = self.require_repo()

        if self.task:
            kwargs["run_id"] = self.task

        return kwargs

    def require_repo(self) -> str:
        """Return the repo slug, failing loudly when scoping would be silently lost."""
        if not self.repo:
            raise NamespaceError("Layer.REPO requires a repo slug; resolve one with firm_mem0.repo.resolve_repo_slug()")
        return self.repo

    def with_repo(self, repo: str | None) -> Namespace:
        """Return a copy scoped to *repo*."""
        return replace(self, repo=repo)

    def with_task(self, task: str | None) -> Namespace:
        """Return a copy scoped to *task*."""
        return replace(self, task=task)

    def with_metadata(self, **values: str) -> Namespace:
        """Return a copy with *values* merged over the existing metadata."""
        return replace(self, metadata={**self.metadata, **values})


def _validated(field_name: str, value: str) -> str:
    normalised = (value or "").strip().lower()
    if not _SLUG_PATTERN.match(normalised):
        raise NamespaceError(
            f"Invalid {field_name} {value!r}: must match {_SLUG_PATTERN.pattern} "
            "(lowercase alphanumerics, dot, dash, underscore)"
        )
    return normalised
