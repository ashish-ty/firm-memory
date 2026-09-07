"""Nothing becomes firm knowledge without passing the gate."""

import pytest

from firm_memory import FirmMemory, MemoryScope, MemoryType, Settings
from firm_memory.errors import LifecycleError, ProviderError
from firm_memory.ingestion import JsonFileCandidateStore
from firm_memory.providers.inmemory import InMemoryProvider

RULE = "Cash strategies stop sending at 15:20 because the exchange rejects after that."


def test_proposing_does_not_create_an_active_memory(memory, provider):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE, reference="mr-4821")

    assert proposal.accepted is False
    assert proposal.decision.needs_human
    assert list(provider.export_all()) == [], "a candidate must not be in the retrievable pool"


def test_a_proposed_memory_is_not_retrievable(memory):
    memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    assert memory.search("when do cash strategies stop sending") == []


def test_approval_makes_it_firm_knowledge_and_retrievable(memory):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE, reference="mr-4821")
    approved = memory.approvals.approve(proposal.candidate_id, approver="ashish")

    assert approved.status.value == "active"
    assert approved.provenance.author == "ashish"
    [hit] = memory.search("when do cash strategies stop sending")
    assert hit.id == approved.id


def test_provenance_survives_approval_so_a_memory_can_be_traced_back(memory):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE, reference="mr-4821", evidence="docs/cutoffs.md")
    approved = memory.approvals.approve(proposal.candidate_id, approver="ashish")

    assert approved.provenance.reference == "mr-4821"
    assert approved.provenance.evidence == "docs/cutoffs.md"


def test_approval_must_be_attributable(memory):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    with pytest.raises(LifecycleError, match="approver"):
        memory.approvals.approve(proposal.candidate_id, approver="  ")


def test_rejection_removes_the_candidate_without_storing_it(memory, provider):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    rejected = memory.approvals.reject(proposal.candidate_id, reviewer="lead", reason="already in the runbook")

    assert rejected.status.value == "rejected"
    assert list(memory.approvals.pending()) == []
    assert list(provider.export_all()) == []


def test_acting_twice_on_one_candidate_is_refused(memory):
    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    memory.approvals.approve(proposal.candidate_id, approver="ashish")

    with pytest.raises(LifecycleError, match="No candidate"):
        memory.approvals.approve(proposal.candidate_id, approver="ashish")


def test_re_proposing_the_same_fact_does_not_bury_the_reviewer(memory):
    """A stateless bot re-runs the same MR; the queue must not grow each time."""
    first = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    second = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)

    assert first.candidate_id == second.candidate_id
    assert len(list(memory.approvals.pending())) == 1


def test_the_pending_queue_says_why_each_candidate_is_waiting(memory):
    memory.propose(RULE, type=MemoryType.BUSINESS_RULE)
    [candidate] = list(memory.approvals.pending())
    assert candidate.reason


def test_the_queue_outlives_the_process_that_proposed(tmp_path):
    """The agent that proposes and the engineer who approves are different processes."""
    path = tmp_path / "candidates.json"
    provider = InMemoryProvider()
    settings = Settings(min_score=0.0)
    scope = MemoryScope.for_repo("oms")

    with FirmMemory(provider, settings=settings, scope=scope,
                    candidates=JsonFileCandidateStore(path)) as proposing:
        candidate_id = proposing.propose(RULE, type=MemoryType.BUSINESS_RULE).candidate_id

    with FirmMemory(provider, settings=settings, scope=scope,
                    candidates=JsonFileCandidateStore(path)) as approving:
        approved = approving.approvals.approve(candidate_id, approver="ashish")
        assert approved.status.value == "active"
        assert approving.search("when do cash strategies stop sending")


def test_automation_still_defers_to_a_human_on_critical_knowledge(provider):
    """Business rules are the memories most expensive to get wrong."""
    settings = Settings(auto_approve_enabled=True, auto_approve_threshold=0.5, min_score=0.0)
    with FirmMemory(provider, settings=settings, scope=MemoryScope.for_repo("oms")) as memory:
        proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE, confidence=0.99)
        assert proposal.accepted is False
        assert "critical type" in proposal.decision.reason


def test_automation_can_activate_a_confident_non_critical_memory(provider):
    settings = Settings(auto_approve_enabled=True, auto_approve_threshold=0.5, min_score=0.0)
    with FirmMemory(provider, settings=settings, scope=MemoryScope.for_repo("oms")) as memory:
        proposal = memory.propose(
            "The build uses uv; pip is not installed in CI.",
            type=MemoryType.TOOLING_SETUP,
            confidence=0.9,
        )
        assert proposal.accepted is True
        assert memory.search("what does the build use")


# --- the reviewer's endorsement must not evaporate ----------------------------


def test_a_failed_write_puts_the_candidate_back_rather_than_losing_it():
    """Claiming removes it from the queue; a failed commit must undo that."""

    class FailingProvider(InMemoryProvider):
        def insert(self, memory):
            raise ProviderError("pgvector is down")

    api = FirmMemory(FailingProvider(), settings=Settings(min_score=0.0), scope=MemoryScope.for_repo("oms"))
    proposal = api.propose(RULE, type=MemoryType.BUSINESS_RULE)

    with pytest.raises(ProviderError):
        api.approvals.approve(proposal.candidate_id, approver="ashish")

    assert [candidate.id for candidate in api.approvals.pending()] == [proposal.candidate_id]
    api.close()


def test_a_restored_candidate_is_the_one_proposed_not_the_one_amended():
    """The reviewer's edit was never committed either; it must not linger."""
    from firm_memory.ingestion import Amendment

    class FailingProvider(InMemoryProvider):
        def insert(self, memory):
            raise ProviderError("pgvector is down")

    api = FirmMemory(FailingProvider(), settings=Settings(min_score=0.0), scope=MemoryScope.for_repo("oms"))
    proposal = api.propose(RULE, type=MemoryType.BUSINESS_RULE)

    with pytest.raises(ProviderError):
        api.approvals.approve(
            proposal.candidate_id, approver="ashish", amendment=Amendment(content="A rewritten rule.")
        )

    [restored] = api.approvals.pending()
    assert restored.memory.content == RULE
    api.close()


def test_an_amendment_the_taxonomy_refuses_does_not_destroy_the_candidate(memory):
    """A reviewer's mistyped edit must not lose the fact it was meant to improve."""
    from firm_memory.errors import TaxonomyError
    from firm_memory.ingestion import Amendment

    proposal = memory.propose(RULE, type=MemoryType.BUSINESS_RULE)

    with pytest.raises(TaxonomyError):
        memory.approvals.approve(
            proposal.candidate_id, approver="ashish", amendment=Amendment(type="user_preferences")
        )

    [restored] = memory.approvals.pending()
    assert restored.memory.content == RULE
