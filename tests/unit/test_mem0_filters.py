"""Filter construction for mem0 OSS, under its verified constraints."""

import pytest

from firm_memory.errors import ScopeError
from firm_memory.models import MemoryStatus, MemoryTier
from firm_memory.providers.mem0.filters import (
    atom_key,
    payload_metadata,
    scope_metadata,
    search_filters,
)
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import MemoryType
from tests.conftest import make_memory


def test_each_scope_atom_becomes_its_own_flat_key():
    """Equality is all pgvector offers, so a multi-value scope cannot live in one key."""
    scope = MemoryScope(firm=True, domains=("execution",), repos=("oms", "gateway"))
    assert scope_metadata(scope) == {
        "scope_firm": "1",
        "scope_domain_execution": "1",
        "scope_repo_oms": "1",
        "scope_repo_gateway": "1",
    }


def test_atom_keys_survive_slugs_containing_underscores():
    assert atom_key("repo:billing_svc") == "scope_repo_billing_svc"


def test_a_single_atom_avoids_a_pointless_or_wrapper():
    assert search_filters(MemoryScope.for_repo("oms")) == {"scope_repo_oms": "1"}


def test_multiple_atoms_are_unioned_as_flat_branches():
    """An AND nested inside OR is passed through verbatim and matches nothing."""
    filters = search_filters(MemoryScope.for_query("oms", domains=("execution",)))
    assert filters == {
        "OR": [
            {"scope_firm": "1"},
            {"scope_domain_execution": "1"},
            {"scope_repo_oms": "1"},
        ]
    }


def test_shared_conditions_are_repeated_into_every_branch():
    """They cannot be hoisted: AND-inside-OR is unusable on OSS."""
    filters = search_filters(
        MemoryScope.for_query("oms"),
        tiers=(MemoryTier.DURABLE, MemoryTier.INDEX),
        statuses=(MemoryStatus.ACTIVE,),
    )
    for branch in filters["OR"]:
        assert branch["tier"] == ["durable", "index"]
        assert branch["status"] == "active"


def test_no_logical_operator_leaks_into_a_branch_as_a_literal_key():
    filters = search_filters(MemoryScope.for_query("oms", domains=("execution",)))
    for branch in filters["OR"]:
        assert not {"AND", "OR", "NOT"} & set(branch)


def test_a_single_valued_filter_is_not_wrapped_in_a_list():
    filters = search_filters(MemoryScope.for_repo("oms"), types=(MemoryType.BUSINESS_RULE,))
    assert filters["type"] == "business_rules"


@pytest.mark.parametrize("reserved", ["user_id", "agent_id", "run_id", "type", "tier", "status", "scope_firm"])
def test_metadata_filters_cannot_hijack_scoping(reserved):
    with pytest.raises(ScopeError, match="override scoping"):
        search_filters(MemoryScope.for_repo("oms"), metadata_filters={reserved: "x"})


def test_payload_carries_everything_a_filter_needs():
    memory = make_memory(scope=MemoryScope(firm=True, repos=("oms",)), confidence=0.9)
    payload = payload_metadata(memory)
    assert payload["type"] == "architecture_decisions"
    assert payload["tier"] == "durable"
    assert payload["status"] == "proposed"
    assert payload["scope_firm"] == "1" and payload["scope_repo_oms"] == "1"


def test_caller_metadata_cannot_forge_scope_or_taxonomy():
    memory = make_memory(
        scope=MemoryScope.for_repo("oms"),
        metadata={"type": "business_rules", "scope_firm": "1", "branch": "main"},
    )
    payload = payload_metadata(memory)
    assert payload["type"] == "architecture_decisions"
    assert "scope_firm" not in payload
    assert payload["branch"] == "main"
