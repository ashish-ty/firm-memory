"""The Firm Memory API: scope, taxonomy and reliability, end to end."""

import pytest

from firm_memory import FirmMemory, MemoryScope, MemoryTier, MemoryType, Settings
from firm_memory.errors import InvalidInputError, ProviderError, TaxonomyError
from firm_memory.providers.inmemory import InMemoryProvider
from tests.conftest import REPO, make_memory


def active(memory: FirmMemory, **overrides):
    """Commit an already-approved memory, as the approval queue would."""
    from firm_memory.lifecycle import approve

    return memory.commit(approve(make_memory(**overrides), approver="lead"))


# --- scope -------------------------------------------------------------------


def test_the_default_scope_is_this_repo_its_domains_and_the_firm(memory):
    assert memory.scope == MemoryScope(firm=True, domains=("execution",), repos=(REPO,))


def test_recall_reaches_firm_knowledge_from_inside_a_repo(memory):
    active(memory, content="All Python services use uv for dependency management.",
           scope=MemoryScope.firm_wide(), type=MemoryType.CONVENTION)

    assert [hit.content for hit in memory.search("how do we manage python dependencies")]


def test_a_memory_scoped_to_several_repos_is_found_from_each_of_them(memory):
    """Stored once, reachable from every repo it is true for."""
    active(memory, content="Order routing always passes through the risk engine.",
           scope=MemoryScope(repos=("oms", "gateway")), type=MemoryType.BUSINESS_RULE)

    for repo in ("oms", "gateway"):
        found = memory.for_scope(MemoryScope.for_repo(repo)).search("order routing risk")
        assert [hit.content for hit in found], f"not reachable from {repo}"


def test_another_repos_knowledge_does_not_leak_in(memory):
    active(memory, content="Analytics jobs are scheduled by Airflow.",
           scope=MemoryScope.for_repo("analytics"), type=MemoryType.TOOLING_SETUP)

    assert memory.search("how are analytics jobs scheduled") == []


# --- tiers -------------------------------------------------------------------


def test_episodic_scratch_state_never_surfaces_in_an_ordinary_recall(memory):
    """The silent failure this default exists to prevent."""
    active(memory, content="Reviewer rejected the coalescing cache on this MR.",
           tier=MemoryTier.EPISODIC, task="mr-4821", type=MemoryType.REVIEW_PATTERN)

    assert memory.search("coalescing cache") == []


def test_episodic_memory_is_still_reachable_when_the_task_is_named(memory):
    active(memory, content="Reviewer rejected the coalescing cache on this MR.",
           tier=MemoryTier.EPISODIC, task="mr-4821", type=MemoryType.REVIEW_PATTERN)

    found = memory.search("coalescing cache", tiers=(MemoryTier.EPISODIC,), task="mr-4821")
    assert len(found) == 1


def test_index_cards_are_retrievable_on_a_different_task(memory):
    """The point is surfacing issue 4821 while working on 5177."""
    active(memory, content="Issue 4821: 504s under bulk import, root cause cache stampede, fixed by coalescing.",
           tier=MemoryTier.INDEX, type=MemoryType.PRODUCTION_ISSUE)

    assert len(memory.search("504s under bulk import")) == 1


# --- taxonomy ----------------------------------------------------------------


def test_a_type_outside_the_taxonomy_is_refused_before_any_write(provider, memory):
    with pytest.raises(TaxonomyError):
        memory.propose("Ashish prefers tabs.", type="user_preferences")
    assert list(provider.export_all()) == []


def test_search_can_be_narrowed_to_a_type(memory):
    active(memory, content="MCX orders always route through Risk Engine A.", type=MemoryType.BUSINESS_RULE)
    active(memory, content="MCX gateway uses uv for dependency management.", type=MemoryType.TOOLING_SETUP)

    found = memory.search("mcx", types=["BUSINESS_RULE"])
    assert [hit.type for hit in found] == [MemoryType.BUSINESS_RULE]


# --- reliability -------------------------------------------------------------


class BrokenProvider(InMemoryProvider):
    def search(self, *_args, **_kwargs):
        raise RuntimeError("connection refused")

    def get(self, _memory_id):
        raise RuntimeError("connection refused")

    def insert(self, _memory):
        raise RuntimeError("connection refused")


class SlowProvider(InMemoryProvider):
    def search(self, *_args, **_kwargs):
        from time import sleep

        sleep(0.5)
        return []


def test_a_provider_failure_yields_an_empty_recall_not_an_exception():
    """A failed recall must never fail a code review."""
    with FirmMemory(BrokenProvider(), scope=MemoryScope.firm_wide()) as memory:
        assert memory.search("anything") == []
        assert memory.get("m1") is None
        assert memory.metrics.for_operation("search").failures == 1


def test_a_slow_provider_is_abandoned_at_the_timeout():
    settings = Settings(timeout_seconds=0.05)
    with FirmMemory(SlowProvider(), settings=settings, scope=MemoryScope.firm_wide()) as memory:
        assert memory.search("anything") == []
        assert memory.metrics.for_operation("search").timeouts == 1


def test_a_write_failure_is_surfaced_rather_than_swallowed():
    """Silently dropping a memory an engineer approved is worse than an error."""
    with FirmMemory(BrokenProvider(), scope=MemoryScope.firm_wide()) as memory, pytest.raises(ProviderError):
        memory.commit(make_memory())


def test_results_below_the_relevance_floor_are_dropped(provider):
    """A weak match is worse than no memory: it spends context and misleads."""
    settings = Settings(min_score=0.9)
    with FirmMemory(provider, settings=settings, scope=MemoryScope.firm_wide()) as memory:
        from firm_memory.lifecycle import approve

        memory.commit(
            approve(
                make_memory(content="Order IDs come from the sequencer.", scope=MemoryScope.firm_wide()),
                approver="lead",
            )
        )

        assert memory.search("sequencer deployment rollback checklist") == []
        assert memory.search("order ids come from the sequencer")


def test_an_empty_query_costs_nothing(memory):
    assert memory.search("   ") == []
    assert memory.metrics.for_operation("search").calls == 0


# --- corrections -------------------------------------------------------------


def test_correcting_a_memory_demotes_it_and_records_why(memory):
    stored = active(memory, content="Cash strategies stop sending at 15:20.", type=MemoryType.BUSINESS_RULE)

    corrected = memory.correct(stored.id, reason="the cut-off moved to 15:25", reporter="eng")
    assert corrected.status.value == "disputed"
    assert corrected.metadata["dispute_reason"] == "the cut-off moved to 15:25"
    assert corrected.confidence < stored.confidence


def test_a_corrected_memory_is_still_retrievable_and_visibly_flagged(memory):
    """Demoted, not deleted: the reporter may themselves be wrong."""
    stored = active(memory, content="Cash strategies stop sending at 15:20.", type=MemoryType.BUSINESS_RULE)
    memory.correct(stored.id, reason="moved to 15:25")

    [hit] = memory.search("when do cash strategies stop sending")
    assert hit.status.value == "disputed"


def test_a_correction_needs_a_reason(memory):
    stored = active(memory)
    with pytest.raises(InvalidInputError, match="reason"):
        memory.correct(stored.id, reason="  ")


def test_correcting_an_unknown_memory_reports_absence_rather_than_failing(memory):
    assert memory.correct("no-such-id", reason="stale") is None
