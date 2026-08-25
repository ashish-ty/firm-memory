"""The agent-facing tool surface.

Four tools (HLD §8), each a thin translation between JSON and the Firm Memory
API:

``memory_search``   find knowledge relevant to what the agent is doing
``memory_get``      fetch one memory by id, for citation and verification
``memory_propose``  put forward a candidate; never creates an active memory
``memory_correct``  report a memory as stale or wrong

**Nothing here raises.** A tool that throws takes the agent's whole turn with
it, and memory is meant to be optional. Failures come back as an ``error`` key
the model can read and move past.

Tool descriptions are part of the contract: they are the only place an agent
learns that the codebase outranks memory, and that proposing is not writing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..errors import FirmMemoryError
from ..memory import FirmMemory
from ..models import Memory, MemoryTier
from ..provenance import Source
from ..scope import MemoryScope
from ..taxonomy import CODING_CATEGORIES

logger = logging.getLogger(__name__)

SEARCH_DESCRIPTION = (
    "Search the firm's engineering memory for why the code is the way it is: architecture "
    "decisions and their trade-offs, business rules the code enforces but does not explain, "
    "approaches already tried and rejected, past production issues, firm conventions and "
    "terminology. Use it before proposing a design, reviewing a change, or diagnosing an "
    "incident. Memory does NOT describe current code — read the repository for that. Where a "
    "memory and the code disagree, the code is right: use memory_correct to flag it."
)

GET_DESCRIPTION = (
    "Fetch one memory by its canonical id, for citing it in a review comment or verifying a "
    "claim before you act on it. Returns superseded and disputed memories too, so a citation "
    "can always be resolved."
)

PROPOSE_DESCRIPTION = (
    "Propose a durable engineering fact for the firm's memory. This does NOT create an active "
    "memory: the candidate is queued for a human to endorse. Propose only what stays true after "
    "this task ends, and never source code, diffs, stack traces, secrets, transient state, or "
    "facts about individual engineers — the repository already answers those, and storing them "
    "guarantees they go stale."
)

CORRECT_DESCRIPTION = (
    "Report that a memory is stale or wrong — for example the code now contradicts it. The "
    "memory is demoted and flagged for a human, never deleted, so the record of what the firm "
    "once believed survives. A reason is required."
)


class MemoryTools:
    """The four MCP tools, bound to one :class:`~firm_memory.memory.FirmMemory`."""

    def __init__(self, memory: FirmMemory) -> None:
        self._memory = memory

    # --- memory_search -------------------------------------------------------

    def memory_search(
        self,
        query: str,
        *,
        repos: Sequence[str] | None = None,
        domains: Sequence[str] | None = None,
        firm: bool = True,
        types: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        """Search active memories relevant to an agent query."""
        try:
            # ``firm`` here means "include firm knowledge", which the instance
            # scope already does — so with no repos or domains named there is
            # nothing to override.
            scope = (
                MemoryScope(firm=firm, domains=tuple(domains or ()), repos=tuple(repos or ()))
                if (repos or domains)
                else self._memory.scope
            )
            results = self._memory.search(query, scope=scope, types=types, limit=limit)
        except FirmMemoryError as exc:
            return _error(exc)
        except Exception as exc:  # pragma: no cover - defensive: a tool must not throw
            logger.exception("memory_search failed")
            return _error(exc)

        return {
            "count": len(results),
            "scope": scope.to_dict(),
            "memories": [_citable(memory) for memory in results],
        }

    # --- memory_get ----------------------------------------------------------

    def memory_get(self, memory_id: str) -> dict:
        """Retrieve a memory by canonical id."""
        try:
            memory = self._memory.get(memory_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("memory_get failed")
            return _error(exc)

        if memory is None:
            return {"found": False, "memory": None}
        return {"found": True, "memory": memory.to_dict()}

    # --- memory_propose ------------------------------------------------------

    def memory_propose(
        self,
        content: str,
        type: str,
        *,
        repos: Sequence[str] | None = None,
        domains: Sequence[str] | None = None,
        firm: bool = False,
        confidence: float | None = None,
        reference: str | None = None,
        author: str | None = None,
        evidence: str | None = None,
        task: str | None = None,
        tier: str = MemoryTier.DURABLE.value,
    ) -> dict:
        """Propose a candidate memory for human approval."""
        try:
            scope = self._write_scope(repos=repos, domains=domains, firm=firm)
            proposal = self._memory.propose(
                content,
                type=type,
                scope=scope,
                confidence=confidence,
                tier=MemoryTier(tier),
                task=task,
                source=Source.AGENT,
                reference=reference,
                author=author,
                evidence=evidence,
            )
        except ValueError as exc:
            return _error(exc)
        except FirmMemoryError as exc:
            return _error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("memory_propose failed")
            return _error(exc)

        return proposal.to_dict()

    # --- memory_correct ------------------------------------------------------

    def memory_correct(self, memory_id: str, reason: str, *, reporter: str | None = None) -> dict:
        """Report a memory as stale or incorrect."""
        try:
            corrected = self._memory.correct(memory_id, reason=reason, reporter=reporter)
        except FirmMemoryError as exc:
            return _error(exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("memory_correct failed")
            return _error(exc)

        if corrected is None:
            return {"found": False, "memory": None}
        return {"found": True, "memory": corrected.to_dict()}

    # --- helpers -------------------------------------------------------------

    def _write_scope(
        self,
        *,
        repos: Sequence[str] | None,
        domains: Sequence[str] | None,
        firm: bool,
    ) -> MemoryScope:
        """The scope a proposal is filed under.

        Unlike search, ``firm`` defaults to false here and is an explicit claim:
        an agent saying a fact is firm-wide is asserting it holds everywhere,
        which is why that claim always goes to a human.
        """
        if repos or domains:
            return MemoryScope(firm=firm, domains=tuple(domains or ()), repos=tuple(repos or ()))
        if firm:
            return MemoryScope.firm_wide()
        return self._memory.scope


def taxonomy_reference() -> list[dict]:
    """The allowed memory types and what each is for.

    Exposed so an agent can be told the vocabulary rather than guess at it —
    a guessed type is rejected, and a rejected proposal is knowledge lost.
    """
    return [
        {"type": category.name, "alias": category.type.name, "description": category.description}
        for category in CODING_CATEGORIES
    ]


def _citable(memory: Memory) -> dict:
    """A search hit, trimmed to what an agent needs to use and cite it."""
    return {
        "id": memory.id,
        "content": memory.content,
        "type": memory.type.value,
        "scope": memory.scope.to_dict(),
        "confidence": memory.confidence,
        "status": memory.status.value,
        "score": memory.score,
        "source": memory.provenance.source,
        "reference": memory.provenance.reference,
        "evidence": memory.provenance.evidence,
    }


def _error(exc: Exception) -> dict:
    """Render a failure the model can read and move past."""
    return {"error": str(exc), "error_type": type(exc).__name__}


#: Tool name -> description, so the server and the tests agree on the surface.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_search": SEARCH_DESCRIPTION,
    "memory_get": GET_DESCRIPTION,
    "memory_propose": PROPOSE_DESCRIPTION,
    "memory_correct": CORRECT_DESCRIPTION,
}
