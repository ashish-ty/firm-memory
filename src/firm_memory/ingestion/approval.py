"""The human approval gate.

    Source -> Candidate -> taxonomy / scope / provenance checks -> Human
    approval -> Firm Memory -> Provider.insert()

The checks happen when the candidate is constructed: the canonical model
refuses a memory with no scope, no content, or a type outside the taxonomy, so
nothing invalid can reach the queue. What is left for this module is the part a
machine should not decide — whether the firm endorses the fact.

An approved candidate is written to the provider; a rejected one is dropped from
the queue with its reason recorded. Neither deletes anything from the pool:
rejection prevents a memory from ever becoming firm knowledge, which is a
different thing from removing knowledge the firm already had.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from hashlib import blake2b

from ..errors import LifecycleError
from ..lifecycle import ApprovalPolicy, evaluate
from ..lifecycle import approve as approve_memory
from ..lifecycle import reject as reject_memory
from ..models import Memory
from .store import CandidateStore, InMemoryCandidateStore


@dataclass(frozen=True, slots=True)
class Candidate:
    """One memory awaiting review, with the reason it is waiting."""

    id: str
    memory: Memory
    reason: str

    def to_dict(self) -> dict:
        """Render for a review UI or an MCP response."""
        return {"candidate_id": self.id, "reason": self.reason, "memory": self.memory.to_dict()}


def candidate_id(memory: Memory) -> str:
    """Derive a stable id from the memory's content and scope.

    Content-addressed so that re-proposing the same fact — which a stateless bot
    will do on every re-run of the same MR — updates one queue entry instead of
    burying the reviewer in duplicates.
    """
    digest = blake2b(digest_size=8)
    digest.update(memory.content.strip().lower().encode("utf-8"))
    digest.update(b"\x00")
    digest.update("|".join(memory.scope.atoms).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(memory.type.value.encode("utf-8"))
    return f"cand-{digest.hexdigest()}"


class ApprovalQueue:
    """Holds candidates and commits the ones a human endorses.

    Takes a ``commit`` callable rather than a :class:`~firm_memory.memory.FirmMemory`
    so that the ingestion layer does not depend on the API layer that uses it.
    """

    def __init__(
        self,
        commit: Callable[[Memory], Memory],
        *,
        store: CandidateStore | None = None,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._commit = commit
        self._store = store or InMemoryCandidateStore()
        self._policy = policy or ApprovalPolicy()

    def submit(self, memory: Memory) -> Candidate:
        """Queue *memory* for review, returning the candidate handle."""
        decision = evaluate(memory, self._policy)
        candidate = Candidate(candidate_id(memory), memory, decision.reason)
        self._store.add(candidate.id, memory)
        return candidate

    def pending(self) -> Iterator[Candidate]:
        """Yield everything currently awaiting a human."""
        for identifier, memory in self._store.items():
            yield Candidate(identifier, memory, evaluate(memory, self._policy).reason)

    def approve(self, identifier: str, *, approver: str, confidence: float | None = None) -> Memory:
        """Endorse a candidate and write it to the provider.

        An approver is required. Approval is the moment a fact becomes firm
        knowledge, and an unattributable one cannot be revisited later.
        """
        if not (approver or "").strip():
            raise LifecycleError("Approval requires an approver; firm knowledge must be attributable")

        memory = self._store.remove(identifier)
        if memory is None:
            raise LifecycleError(f"No candidate with id {identifier!r} is awaiting approval")

        return self._commit(approve_memory(memory, approver=approver, confidence=confidence))

    def reject(self, identifier: str, *, reviewer: str, reason: str | None = None) -> Memory:
        """Turn a candidate down, removing it from the queue."""
        memory = self._store.remove(identifier)
        if memory is None:
            raise LifecycleError(f"No candidate with id {identifier!r} is awaiting approval")
        return reject_memory(memory, reviewer=reviewer, reason=reason)
