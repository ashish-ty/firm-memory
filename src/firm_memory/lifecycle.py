"""Approval and correction lifecycle.

V1 ingestion is **fully human approved** (HLD §7): an agent proposes, a person
endorses. The canonical model records confidence from the beginning anyway, so
confidence-based automation can be switched on later without a migration — the
policy object below is the switch.

Some knowledge is never auto-approved regardless of confidence: business rules,
architecture decisions, firm-wide conventions and production-critical knowledge
are the memories most expensive to get wrong, and a firm-wide scope multiplies
the blast radius of any single mistake.

**Nothing here deletes.** Retention policy is supersession plus reduced
confidence, so the record that a decision was made and later unmade survives —
which is frequently the most valuable thing in the pool.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .errors import LifecycleError
from .models import Memory, MemoryStatus
from .taxonomy import MemoryType

#: Types whose cost of being wrong is high enough that a human always signs off.
CRITICAL_TYPES: frozenset[MemoryType] = frozenset(
    {
        MemoryType.BUSINESS_RULE,
        MemoryType.ARCHITECTURE_DECISION,
        MemoryType.CONVENTION,
        MemoryType.PRODUCTION_ISSUE,
    }
)

DEFAULT_AUTO_APPROVE_THRESHOLD = 0.85

#: How much confidence a memory loses when someone reports it wrong. It is
#: demoted rather than removed: the reporter may themselves be mistaken, and the
#: memory stays visible (and flagged) until a human resolves the disagreement.
DISPUTE_CONFIDENCE_PENALTY = 0.3

_ALLOWED_TRANSITIONS: dict[MemoryStatus, frozenset[MemoryStatus]] = {
    MemoryStatus.PROPOSED: frozenset({MemoryStatus.ACTIVE, MemoryStatus.REJECTED}),
    MemoryStatus.ACTIVE: frozenset({MemoryStatus.DISPUTED, MemoryStatus.SUPERSEDED}),
    MemoryStatus.DISPUTED: frozenset(
        {MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED, MemoryStatus.REJECTED}
    ),
    MemoryStatus.SUPERSEDED: frozenset(),
    MemoryStatus.REJECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """Configuration of the approval gate.

    The default is the V1 posture: everything goes to a human.
    """

    auto_approve_enabled: bool = False
    auto_approve_threshold: float = DEFAULT_AUTO_APPROVE_THRESHOLD
    critical_types: frozenset[MemoryType] = CRITICAL_TYPES
    #: Whether a firm-wide memory always needs a person, even once automation is on.
    firm_scope_always_human: bool = True


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The gate's verdict on one candidate, with the reason it reached it."""

    auto_approved: bool
    reason: str

    @property
    def needs_human(self) -> bool:
        """Whether a person must review this candidate before it becomes active."""
        return not self.auto_approved


def evaluate(memory: Memory, policy: ApprovalPolicy | None = None) -> ApprovalDecision:
    """Decide whether *memory* may be activated without a human.

    The reason string is part of the contract: an engineer reviewing a queue
    should be able to see why each candidate is waiting for them.
    """
    policy = policy or ApprovalPolicy()

    if not policy.auto_approve_enabled:
        return ApprovalDecision(False, "automated approval is disabled; all memory is human approved")
    if memory.type in policy.critical_types:
        return ApprovalDecision(False, f"{memory.type.value} is a critical type and always requires human approval")
    if policy.firm_scope_always_human and memory.scope.is_firm_wide:
        return ApprovalDecision(False, "firm-wide scope always requires human approval")
    if memory.confidence < policy.auto_approve_threshold:
        return ApprovalDecision(
            False,
            f"confidence {memory.confidence:.2f} is below the auto-approval threshold "
            f"{policy.auto_approve_threshold:.2f}",
        )
    return ApprovalDecision(True, f"confidence {memory.confidence:.2f} meets policy for a non-critical memory")


def requires_human_approval(memory: Memory, policy: ApprovalPolicy | None = None) -> bool:
    """Whether *memory* must be reviewed by a person before activation."""
    return evaluate(memory, policy).needs_human


# --- transitions -------------------------------------------------------------


def transition(memory: Memory, to: MemoryStatus, **changes) -> Memory:
    """Return a copy of *memory* moved to status *to*, rejecting illegal moves."""
    allowed = _ALLOWED_TRANSITIONS[memory.status]
    if to not in allowed:
        permitted = ", ".join(sorted(status.value for status in allowed)) or "nothing (terminal state)"
        raise LifecycleError(
            f"Cannot move memory {memory.id or '<new>'} from {memory.status.value} to {to.value}; "
            f"allowed: {permitted}"
        )
    return replace(memory, status=to, updated_at=datetime.now(UTC), **changes)


def approve(memory: Memory, *, approver: str | None = None, confidence: float | None = None) -> Memory:
    """Endorse a proposal (or re-confirm a disputed memory) as active."""
    changes: dict = {"provenance": memory.provenance.with_author(approver or memory.provenance.author)}
    if confidence is not None:
        changes["confidence"] = confidence
    elif memory.status is MemoryStatus.DISPUTED:
        # Re-confirmation restores what the dispute took away.
        changes["confidence"] = min(1.0, memory.confidence + DISPUTE_CONFIDENCE_PENALTY)
    return transition(memory, MemoryStatus.ACTIVE, **changes)


def reject(memory: Memory, *, reviewer: str | None = None, reason: str | None = None) -> Memory:
    """Turn a candidate down, keeping it so it is not re-proposed forever."""
    metadata = dict(memory.metadata)
    if reason:
        metadata["rejection_reason"] = reason
    if reviewer:
        metadata["rejected_by"] = reviewer
    return transition(memory, MemoryStatus.REJECTED, metadata=metadata)


def dispute(memory: Memory, *, reporter: str | None = None, reason: str | None = None) -> Memory:
    """Flag a memory as stale or wrong, demoting rather than deleting it."""
    metadata = dict(memory.metadata)
    if reason:
        metadata["dispute_reason"] = reason
    if reporter:
        metadata["disputed_by"] = reporter
    return transition(
        memory,
        MemoryStatus.DISPUTED,
        confidence=max(0.0, memory.confidence - DISPUTE_CONFIDENCE_PENALTY),
        metadata=metadata,
    )


def supersede(memory: Memory, *, by: str) -> Memory:
    """Mark *memory* as replaced by the memory with id *by*."""
    if not (by or "").strip():
        raise LifecycleError("Superseding requires the id of the memory that replaces this one")
    return transition(
        memory,
        MemoryStatus.SUPERSEDED,
        superseded_by=by,
        confidence=max(0.0, memory.confidence - DISPUTE_CONFIDENCE_PENALTY),
    )
