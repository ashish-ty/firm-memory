"""Reading mem0 records must tolerate shape drift, not fail the recall."""

import pytest

from firm_memory.models import MemoryStatus, MemoryTier
from firm_memory.providers.mem0.mapping import flatten, to_memory
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import MemoryType

BASE = {"id": "m1", "memory": "Order IDs come from the sequencer.", "metadata": {"scope_repo_oms": "1"}}


def record(**metadata):
    return {**BASE, "metadata": {**BASE["metadata"], **metadata}}


def test_nested_metadata_wins_over_top_level_bookkeeping():
    merged = flatten({"id": "m1", "source": "provider", "metadata": {"source": "interview"}})
    assert merged["source"] == "interview"


@pytest.mark.parametrize("content_key", ["memory", "content", "text", "data"])
def test_content_is_found_under_any_of_mem0s_field_names(content_key):
    memory = to_memory({"id": "m1", content_key: "a durable fact", "scope_firm": "1"})
    assert memory.content == "a durable fact"


def test_a_record_with_no_content_is_dropped_rather_than_raised_on():
    assert to_memory({"id": "m1", "metadata": {"scope_firm": "1"}}) is None


def test_an_untyped_record_is_kept_as_unclassified_not_discarded():
    """Memory written before the taxonomy was enforced is still worth returning."""
    assert to_memory(record()).type is MemoryType.TASK_LEARNING


def test_a_type_outside_the_taxonomy_falls_back_rather_than_losing_the_fact():
    assert to_memory(record(type="food_preferences")).type is MemoryType.TASK_LEARNING


@pytest.mark.parametrize("bad", ["nonsense", "", None])
def test_an_unreadable_tier_or_status_falls_back_to_the_safe_default(bad):
    memory = to_memory(record(tier=bad, status=bad))
    assert memory.tier is MemoryTier.DURABLE
    assert memory.status is MemoryStatus.ACTIVE


@pytest.mark.parametrize(("raw", "expected"), [("0.9", 0.9), ("2.0", 1.0), ("-1", 0.0), ("abc", 0.5), (None, 0.5)])
def test_confidence_is_clamped_into_a_probability(raw, expected):
    assert to_memory(record(confidence=raw)).confidence == expected


def test_an_unparseable_timestamp_is_treated_as_absent():
    assert to_memory({**record(), "created_at": "not-a-date"}).created_at is None


def test_a_parseable_timestamp_is_preserved():
    memory = to_memory({**record(), "created_at": "2026-08-01T10:00:00+00:00"})
    assert memory.created_at.year == 2026


def test_unmodelled_payload_keys_survive_as_metadata():
    memory = to_memory(record(branch="main", service="billing"))
    assert memory.metadata == {"branch": "main", "service": "billing"}


def test_structural_and_scope_keys_do_not_leak_into_metadata():
    memory = to_memory(record(type="business_rules", scope_firm="1", owner="repo:oms", layer="repo"))
    assert not {"type", "scope_firm", "scope_repo_oms", "owner", "layer"} & set(memory.metadata)


def test_ownership_metadata_recovers_the_scope_of_a_plugin_written_memory():
    memory = to_memory({"id": "m1", "memory": "x", "owner": "domain:execution"})
    assert memory.scope == MemoryScope(domains=("execution",))


def test_a_bare_agent_id_is_read_as_a_repo_scope():
    memory = to_memory({"id": "m1", "memory": "x", "agent_id": "oms"})
    assert memory.scope == MemoryScope(repos=("oms",))


def test_a_record_with_no_scope_information_at_all_is_treated_as_firm_wide():
    """Better surfaced everywhere than silently unreachable forever."""
    assert to_memory({"id": "m1", "memory": "x"}).scope == MemoryScope(firm=True)
