"""V1 is human approved; nothing here deletes."""

import pytest

from firm_memory import lifecycle
from firm_memory.errors import LifecycleError
from firm_memory.lifecycle import ApprovalPolicy
from firm_memory.models import MemoryStatus
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import MemoryType
from tests.conftest import make_memory

AUTOMATED = ApprovalPolicy(auto_approve_enabled=True)


def test_v1_posture_sends_everything_to_a_human():
    decision = lifecycle.evaluate(make_memory(confidence=1.0))
    assert decision.needs_human
    assert "disabled" in decision.reason


@pytest.mark.parametrize("critical", sorted(lifecycle.CRITICAL_TYPES, key=lambda t: t.value))
def test_critical_types_always_need_a_human_however_confident(critical):
    memory = make_memory(type=critical, confidence=1.0, scope=MemoryScope.for_repo("oms"))
    assert lifecycle.requires_human_approval(memory, AUTOMATED)


def test_firm_wide_scope_always_needs_a_human():
    memory = make_memory(type=MemoryType.TOOLING_SETUP, confidence=1.0, scope=MemoryScope.firm_wide())
    assert lifecycle.requires_human_approval(memory, AUTOMATED)


def test_a_confident_non_critical_memory_can_be_automated():
    memory = make_memory(type=MemoryType.TOOLING_SETUP, confidence=0.95, scope=MemoryScope.for_repo("oms"))
    assert lifecycle.evaluate(memory, AUTOMATED).auto_approved


def test_low_confidence_falls_back_to_a_human_with_the_numbers_stated():
    memory = make_memory(type=MemoryType.TOOLING_SETUP, confidence=0.4, scope=MemoryScope.for_repo("oms"))
    decision = lifecycle.evaluate(memory, AUTOMATED)
    assert decision.needs_human and "0.40" in decision.reason


def test_approval_records_who_endorsed_it():
    approved = lifecycle.approve(make_memory(), approver="ashish")
    assert approved.status is MemoryStatus.ACTIVE
    assert approved.provenance.author == "ashish"


def test_dispute_demotes_rather_than_deletes():
    active = lifecycle.approve(make_memory(confidence=0.9))
    disputed = lifecycle.dispute(active, reporter="eng", reason="code says otherwise")

    assert disputed.status is MemoryStatus.DISPUTED
    assert disputed.confidence < active.confidence
    assert disputed.metadata["dispute_reason"] == "code says otherwise"


def test_a_disputed_memory_can_be_reconfirmed():
    active = lifecycle.approve(make_memory(confidence=0.9))
    restored = lifecycle.approve(lifecycle.dispute(active), approver="lead")
    assert restored.status is MemoryStatus.ACTIVE
    assert restored.confidence == pytest.approx(active.confidence)


def test_supersession_keeps_the_original_queryable_and_named():
    active = lifecycle.approve(make_memory())
    superseded = lifecycle.supersede(active, by="m2")
    assert superseded.status is MemoryStatus.SUPERSEDED
    assert superseded.superseded_by == "m2"


def test_supersession_requires_the_replacement_id():
    with pytest.raises(LifecycleError):
        lifecycle.supersede(lifecycle.approve(make_memory()), by="  ")


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (MemoryStatus.PROPOSED, MemoryStatus.DISPUTED),
        (MemoryStatus.ACTIVE, MemoryStatus.REJECTED),
        (MemoryStatus.REJECTED, MemoryStatus.ACTIVE),
        (MemoryStatus.SUPERSEDED, MemoryStatus.ACTIVE),
    ],
)
def test_illegal_transitions_are_refused(start, target):
    with pytest.raises(LifecycleError):
        lifecycle.transition(make_memory(status=start), target)


def test_rejection_keeps_the_memory_and_its_reason():
    """Kept so the same candidate is not proposed again forever."""
    rejected = lifecycle.reject(make_memory(), reviewer="lead", reason="already covered by #2")
    assert rejected.status is MemoryStatus.REJECTED
    assert rejected.metadata["rejection_reason"] == "already covered by #2"
