"""Memory scope — the firm's semantic contract for *who a fact is true for*.

Scope is deliberately a set of **independent attributes** rather than a strict
hierarchy (HLD §6), because firm knowledge does not respect a tree: a rule about
order routing is true for ``gateway``, ``risk`` and ``oms`` at once, and for the
``execution`` domain as a whole.

    {"firm": true, "domains": ["trading"], "repos": ["oms", "execution"]}

There is intentionally **no engineer-level and no team-level scope**. The same
question must return the same firm knowledge regardless of which engineer or
agent asks it; an identity axis would split one fact into copies that drift
apart and that no single query can reach.

Scope is owned here, not by the provider. A provider is told how to *project*
scope onto its own storage axes — see
:mod:`firm_memory.providers.mem0.namespace`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .errors import ScopeError

#: Scope values must round-trip through provider filters unchanged.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

FIRM_ATOM = "firm"
DOMAIN_PREFIX = "domain:"
REPO_PREFIX = "repo:"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Immutable statement of the reach of a memory, or of a query.

    On a stored memory it means "this fact is true for these things". On a
    search it means "I am working on these things". The two are matched atom by
    atom, so a memory scoped to three repos is found from any one of them
    without being stored three times.
    """

    firm: bool = False
    domains: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Normalise at construction: an invalid or duplicated scope must fail at
        # the boundary rather than create a partition nobody can query.
        object.__setattr__(self, "domains", _normalised("domain", self.domains))
        object.__setattr__(self, "repos", _normalised("repo", self.repos))
        if not self.firm and not self.domains and not self.repos:
            raise ScopeError(
                "A memory must be scoped to at least one of firm, domains or repos; "
                "an unscoped memory is unreachable"
            )

    # --- construction helpers ------------------------------------------------

    @classmethod
    def for_repo(cls, repo: str, *, domains: Iterable[str] = (), firm: bool = False) -> MemoryScope:
        """Scope a memory (or a query) to one repo, optionally its domains."""
        return cls(firm=firm, domains=tuple(domains), repos=(repo,))

    @classmethod
    def firm_wide(cls) -> MemoryScope:
        """Scope covering the whole firm and nothing narrower."""
        return cls(firm=True)

    @classmethod
    def for_query(cls, repo: str | None = None, *, domains: Iterable[str] = ()) -> MemoryScope:
        """Build the scope an agent should search with.

        Firm knowledge is always included: a convention that applies everywhere
        applies to the repo in hand too, and omitting it is the most common way
        a retrieval silently loses the answer.
        """
        return cls(firm=True, domains=tuple(domains), repos=(repo,) if repo else ())

    # --- derived views -------------------------------------------------------

    @property
    def atoms(self) -> tuple[str, ...]:
        """The canonical atom strings this scope covers.

        ``("firm", "domain:execution", "repo:oms")``. Atoms are the unit of
        matching: a memory is a candidate for a query when the two scopes share
        at least one atom.
        """
        parts: list[str] = [FIRM_ATOM] if self.firm else []
        parts.extend(f"{DOMAIN_PREFIX}{domain}" for domain in self.domains)
        parts.extend(f"{REPO_PREFIX}{repo}" for repo in self.repos)
        return tuple(parts)

    @property
    def is_firm_wide(self) -> bool:
        """Whether this scope claims firm-wide reach (which gates approval)."""
        return self.firm

    def overlaps(self, other: MemoryScope) -> bool:
        """Whether *other* shares at least one atom with this scope."""
        return bool(set(self.atoms) & set(other.atoms))

    # --- immutable derivation ------------------------------------------------

    def with_repos(self, *repos: str) -> MemoryScope:
        """Return a copy also covering *repos*."""
        return replace(self, repos=self.repos + tuple(repos))

    def with_domains(self, *domains: str) -> MemoryScope:
        """Return a copy also covering *domains*."""
        return replace(self, domains=self.domains + tuple(domains))

    def as_firm_wide(self) -> MemoryScope:
        """Return a copy promoted to firm-wide reach."""
        return replace(self, firm=True)

    def to_dict(self) -> dict:
        """Render the JSON form used on the MCP boundary and in exports."""
        return {"firm": self.firm, "domains": list(self.domains), "repos": list(self.repos)}

    @classmethod
    def from_dict(cls, raw: dict | None) -> MemoryScope:
        """Rebuild a scope from its JSON form, validating as if freshly constructed."""
        if not raw:
            raise ScopeError("Scope is required; an unscoped memory is unreachable")
        return cls(
            firm=bool(raw.get("firm", False)),
            domains=tuple(raw.get("domains") or ()),
            repos=tuple(raw.get("repos") or ()),
        )


def _normalised(field_name: str, values: Iterable[str]) -> tuple[str, ...]:
    """Lowercase, validate, and de-duplicate while preserving first-seen order."""
    seen: dict[str, None] = {}
    for value in values:
        candidate = (value or "").strip().lower()
        if not _SLUG_PATTERN.match(candidate):
            raise ScopeError(
                f"Invalid {field_name} {value!r}: must match {_SLUG_PATTERN.pattern} "
                "(lowercase alphanumerics, dot, dash, underscore)"
            )
        seen.setdefault(candidate)
    return tuple(seen)
