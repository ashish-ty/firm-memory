"""The review operations, with no HTTP in them.

Everything a reviewer can do is here, so the gate can be tested without a
server and reached from a script, a different transport, or a future UI without
being reimplemented. The HTTP layer above translates; it decides nothing.

One rule shapes the whole module: **the reviewer is told what actually
happened**. A queue that cannot be reached must not render as an empty queue, a
candidate someone else already approved must not read as a mysterious failure,
and an approval whose write failed must say so rather than appearing to succeed.
Each of those is a case where a comfortable lie would leave firm knowledge in a
state nobody intended.
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import InvalidInputError
from ..ingestion.amendment import Amendment
from ..mcp.tools import taxonomy_reference
from ..memory import FirmMemory
from ..models import Memory

logger = logging.getLogger(__name__)


class ReviewService:
    """Everything a human reviewer can do to the queue."""

    def __init__(self, memory: FirmMemory) -> None:
        self._memory = memory

    # --- reading -------------------------------------------------------------

    def queue(self) -> dict:
        """Every candidate awaiting a person, oldest first where the store knows.

        Failures propagate. An unreachable queue and an empty one look identical
        on screen, and showing "nothing to review" for a full queue is this
        tool's worst possible failure.
        """
        candidates = [candidate.to_dict() for candidate in self._memory.approvals.pending()]
        return {"count": len(candidates), "candidates": candidates}

    def taxonomy(self) -> dict:
        """The types a reviewer may retype a candidate to, and what each means.

        Served rather than hardcoded in the page so the vocabulary has exactly
        one definition — a dropdown that has drifted from the taxonomy produces
        amendments the model then refuses.
        """
        return {"types": taxonomy_reference()}

    def scope(self) -> dict:
        """The scope this service's memory instance reads and writes under."""
        return self._memory.scope.to_dict()

    # --- deciding ------------------------------------------------------------

    def approve(
        self,
        candidate_id: str,
        *,
        approver: str,
        confidence: float | None = None,
        amendment: Amendment | None = None,
    ) -> dict:
        """Endorse a candidate, optionally correcting it first.

        This is the moment a proposal becomes firm knowledge and the only path
        by which anything reaches the retrievable pool.
        """
        approver = _required(approver, "approver")
        approved = self._memory.approvals.approve(
            candidate_id, approver=approver, confidence=confidence, amendment=amendment
        )
        logger.info("Candidate %s approved by %s as memory %s", candidate_id, approver, approved.id)
        return _decided(approved, amended=amendment is not None and not amendment.is_empty)

    def reject(self, candidate_id: str, *, reviewer: str, reason: str | None = None) -> dict:
        """Turn a candidate down, so it stops occupying the queue.

        Rejection is not deletion of anything the firm knew: the candidate was
        never firm knowledge, and refusing it is a decision worth recording.
        """
        reviewer = _required(reviewer, "reviewer")
        rejected = self._memory.approvals.reject(candidate_id, reviewer=reviewer, reason=reason)
        logger.info("Candidate %s rejected by %s (%s)", candidate_id, reviewer, reason or "no reason given")
        return _decided(rejected, amended=False)


def _decided(memory: Memory, *, amended: bool) -> dict[str, Any]:
    """Render the outcome of a decision for the UI."""
    return {"memory": memory.to_dict(), "status": memory.status.value, "amended": amended}


def _required(name: str, field: str) -> str:
    """Insist on an attributable name.

    Approval and rejection are both decisions the firm may need to revisit, and
    a decision nobody is named for cannot be.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidInputError(f"A {field} name is required; a review decision must be attributable")
    return cleaned
