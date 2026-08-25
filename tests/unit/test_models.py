"""The canonical model is what makes provider migration possible."""

import pytest

from firm_memory.errors import InvalidInputError, TaxonomyError
from firm_memory.models import DEFAULT_SEARCH_TIERS, Memory, MemoryStatus, MemoryTier
from firm_memory.scope import MemoryScope
from tests.conftest import make_memory


def test_a_new_memory_is_proposed_not_active():
    """Nothing becomes firm knowledge without passing the gate."""
    assert make_memory().status is MemoryStatus.PROPOSED


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_content_is_refused(bad):
    with pytest.raises(InvalidInputError):
        make_memory(content=bad)


def test_a_type_outside_the_taxonomy_is_refused():
    with pytest.raises(TaxonomyError):
        make_memory(type="food_preferences")


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_confidence_must_be_a_probability(bad):
    with pytest.raises(InvalidInputError):
        make_memory(confidence=bad)


def test_durable_memory_cannot_be_bound_to_a_task():
    """Task-bound durable memory is stranded: it can never be found again."""
    with pytest.raises(InvalidInputError, match="must not be task-scoped"):
        make_memory(tier=MemoryTier.DURABLE, task="mr-4821")


def test_index_memory_cannot_be_bound_to_a_task():
    """The point of an index card is surfacing issue 4821 while working on 5177."""
    with pytest.raises(InvalidInputError, match="must not be task-scoped"):
        make_memory(tier=MemoryTier.INDEX, task="mr-5177")


def test_episodic_memory_requires_a_task():
    with pytest.raises(InvalidInputError, match="requires a task"):
        make_memory(tier=MemoryTier.EPISODIC)


def test_episodic_memory_is_excluded_from_the_default_search_tiers():
    """An absent task filter means 'don't care' to a vector store, not 'unset'."""
    assert MemoryTier.EPISODIC not in DEFAULT_SEARCH_TIERS
    assert set(DEFAULT_SEARCH_TIERS) == {MemoryTier.DURABLE, MemoryTier.INDEX}


def test_round_trips_through_its_canonical_json_form():
    original = make_memory(
        scope=MemoryScope(firm=True, repos=("oms",)),
        confidence=0.8,
        metadata={"branch": "main"},
    )
    assert Memory.from_dict(original.to_dict()).to_dict() == original.to_dict()


def test_metadata_cannot_be_mutated_through_the_memory():
    memory = make_memory(metadata={"branch": "main"})
    with pytest.raises(TypeError):
        memory.metadata["branch"] = "hacked"  # type: ignore[index]


def test_derivation_leaves_the_original_untouched():
    memory = make_memory()
    assert memory.with_id("m1").id == "m1"
    assert memory.id is None
