"""Scope is the firm's contract for who a fact is true for."""

import pytest

from firm_memory.errors import ScopeError
from firm_memory.scope import MemoryScope


def test_scope_attributes_are_independent_not_hierarchical():
    """One fact can be true for a domain and several repos at once."""
    scope = MemoryScope(firm=True, domains=("trading",), repos=("oms", "execution"))
    assert scope.atoms == ("firm", "domain:trading", "repo:oms", "repo:execution")


def test_values_are_normalised_and_deduplicated_in_order():
    scope = MemoryScope(repos=("OMS", "oms", "Gateway"))
    assert scope.repos == ("oms", "gateway")


@pytest.mark.parametrize("bad", ["has space", "bad/slash", "Ünicode", "", "   "])
def test_unusable_slugs_are_rejected_at_construction(bad):
    with pytest.raises(ScopeError):
        MemoryScope(repos=(bad,))


def test_a_memory_scoped_to_nothing_is_refused():
    """An unscoped memory is unreachable, so it must not be constructible."""
    with pytest.raises(ScopeError, match="at least one"):
        MemoryScope()


def test_there_is_no_engineer_or_team_axis():
    """Memory is owned by the codebase and the firm, never by a person or team."""
    fields = set(MemoryScope.__dataclass_fields__)
    assert fields == {"firm", "domains", "repos"}
    assert not fields & {"engineer", "team", "user", "author", "squad"}


def test_query_scope_always_includes_the_firm():
    """Omitting firm knowledge is the commonest way a recall loses the answer."""
    assert MemoryScope.for_query("oms").firm is True


def test_overlap_is_atom_based_so_multi_repo_memory_is_reachable_from_each_repo():
    stored = MemoryScope(repos=("oms", "gateway"))
    assert stored.overlaps(MemoryScope.for_query("gateway"))
    assert stored.overlaps(MemoryScope.for_query("oms"))
    assert not stored.overlaps(MemoryScope(repos=("analytics",)))


def test_round_trips_through_its_json_form():
    scope = MemoryScope(firm=True, domains=("execution",), repos=("oms",))
    assert MemoryScope.from_dict(scope.to_dict()) == scope


def test_derivation_does_not_mutate_the_original():
    scope = MemoryScope(repos=("oms",))
    derived = scope.with_repos("gateway").as_firm_wide()
    assert derived.repos == ("oms", "gateway") and derived.firm is True
    assert scope.repos == ("oms",) and scope.firm is False
