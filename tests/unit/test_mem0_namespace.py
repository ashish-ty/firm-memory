"""Projecting canonical scope onto mem0's entity axes."""

import pytest

from firm_memory.errors import ScopeError
from firm_memory.models import MemoryTier
from firm_memory.providers.mem0.namespace import Layer, Namespace, Partition
from firm_memory.scope import MemoryScope
from tests.conftest import make_memory

NS = Namespace()


def test_there_is_no_engineer_or_team_layer():
    assert set(Layer) == {Layer.FIRM, Layer.DOMAIN, Layer.REPO}


@pytest.mark.parametrize(
    ("atom", "expected"),
    [
        ("firm", Partition(Layer.FIRM)),
        ("domain:execution", Partition(Layer.DOMAIN, "execution")),
        ("repo:oms", Partition(Layer.REPO, "oms")),
    ],
)
def test_atoms_parse_into_partitions(atom, expected):
    assert Partition.from_atom(atom) == expected


def test_an_unrecognised_atom_fails_loudly():
    with pytest.raises(ScopeError):
        Partition.from_atom("engineer:ashish")


def test_partitions_are_ordered_broadest_first():
    scope = MemoryScope(firm=True, domains=("execution",), repos=("oms",))
    assert [p.layer for p in NS.partitions(scope)] == [Layer.FIRM, Layer.DOMAIN, Layer.REPO]


def test_a_multi_scope_memory_is_filed_under_its_broadest_partition():
    """Firm knowledge must never end up recorded under one repo."""
    scope = MemoryScope(firm=True, repos=("oms", "gateway"))
    assert NS.primary(scope) == Partition(Layer.FIRM)


def test_user_id_names_the_pool_not_the_partition():
    """mem0 requires a top-level entity key, which is ANDed with everything
    below it — so spending user_id on the partition makes a cross-partition OR
    inexpressible."""
    repo_kwargs = NS.entity_kwargs(Partition(Layer.REPO, "oms"))
    firm_kwargs = NS.entity_kwargs(Partition(Layer.FIRM))
    assert repo_kwargs["user_id"] == firm_kwargs["user_id"] == "firm"


def test_the_pool_owner_is_configurable():
    assert Namespace(pool_owner="acme-eng").entity_kwargs(Partition(Layer.FIRM))["user_id"] == "acme-eng"


def test_repo_partitions_keep_the_repo_on_agent_id():
    assert NS.entity_kwargs(Partition(Layer.REPO, "oms"))["agent_id"] == "oms"


def test_non_repo_partitions_carry_no_agent_clause():
    assert "agent_id" not in NS.entity_kwargs(Partition(Layer.DOMAIN, "execution"))
    assert "agent_id" not in NS.entity_kwargs(Partition(Layer.FIRM))


def test_only_episodic_memory_is_bound_to_a_run():
    """Durable memory bound to a run_id is stranded on that task forever."""
    durable = make_memory(scope=MemoryScope.for_repo("oms"))
    episodic = make_memory(scope=MemoryScope.for_repo("oms"), tier=MemoryTier.EPISODIC, task="mr-4821")

    assert "run_id" not in NS.write_kwargs(durable)
    assert NS.write_kwargs(episodic)["run_id"] == "mr-4821"


def test_ownership_metadata_records_where_a_memory_was_filed():
    memory = make_memory(scope=MemoryScope(domains=("execution",), repos=("oms",)))
    assert NS.ownership_metadata(memory) == {"owner": "domain:execution", "layer": "domain"}


def test_owner_labels_are_prefixed_so_layers_cannot_collide():
    assert Partition(Layer.REPO, "execution").owner != Partition(Layer.DOMAIN, "execution").owner
