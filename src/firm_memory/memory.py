"""The Firm Memory API — the thin, firm-owned contract over a provider.

This is what agents and applications import. It owns the firm's semantics and
nothing else:

* construct and enforce **scope** on every read and write;
* enforce the **taxonomy** before anything is stored;
* stamp **provenance** so a memory can be traced and corrected;
* apply the **lifecycle** gate so nothing becomes active without approval;
* keep memory **best effort**, so a provider outage is invisible to the agent.

Retrieval mechanics — embeddings, vector search, ranking — belong to the
provider and are deliberately not re-implemented here (HLD §9).

**Reads never raise.** A timeout or a provider error yields an empty result and
a recorded metric, because a failed recall must not fail a code review. Writes
*do* raise: silently dropping a memory an engineer just approved would be worse
than a visible error.

**The codebase always wins.** Nothing here can enforce that, but it is the rule
consumers must carry in their system prompt: memory is a hypothesis about the
code, and where they disagree the code is right and the memory should be
corrected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from time import perf_counter
from typing import Any, TypeVar

from . import metrics as metric_names
from .config import Settings
from .errors import ConfigurationError, InvalidInputError, ProviderError
from .ingestion.approval import ApprovalQueue, Candidate
from .ingestion.extraction import FactExtractor, LLMFactExtractor, SourceDocument, extract_all
from .ingestion.selection import candidate_store_from_url
from .ingestion.store import CandidateStore, InMemoryCandidateStore, JsonFileCandidateStore
from .lifecycle import ApprovalDecision, MemoryStatus, evaluate
from .lifecycle import dispute as dispute_memory
from .metrics import Metrics
from .models import (
    DEFAULT_SEARCH_STATUSES,
    DEFAULT_SEARCH_TIERS,
    Memory,
    MemoryTier,
)
from .provenance import Provenance, Source
from .providers.base import MemoryProvider
from .providers.registry import get_provider
from .repo import resolve_repo_slug
from .scope import MemoryScope
from .taxonomy import MemoryType, coerce_type

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _default_extractor(settings: Settings, provider: MemoryProvider) -> FactExtractor | None:
    """Build the configured extractor, or none.

    Absent configuration is not an error: a deployment that only searches and
    curates by hand never ingests, and should not be made to install an LLM
    client to start up.
    """
    if settings.extractor == "none":
        return None

    if settings.extractor == "provider":
        extractor = _provider_extractor(provider)
        if extractor is not None:
            return extractor
        # Fall through: a provider without its own extractor is a reason to use
        # the platform's, not a reason to refuse to start.
        logger.info(
            "Provider %r has no extractor; falling back to the platform's",
            getattr(provider, "name", type(provider).__name__),
        )

    if not settings.extraction_model:
        return None

    from .ingestion.llm import LiteLLMClient

    return LLMFactExtractor(
        LiteLLMClient(
            settings.extraction_model,
            api_key=settings.extraction_api_key,
            api_base=settings.extraction_api_base,
        )
    )


def _provider_extractor(provider: MemoryProvider) -> FactExtractor | None:
    """Return the provider's own extractor, if it has one."""
    if not hasattr(provider, "backend"):
        return None

    from .providers.mem0.extraction import Mem0FactExtractor

    return Mem0FactExtractor(provider)


def _default_candidate_store(settings: Settings) -> CandidateStore:
    """The queue that spans the widest gap the deployment actually has.

    A configured queue URL wins, because it is the only setting that can reach
    across machines — the bot proposing from CI and the engineer approving later
    are not on the same host. A file when only a path is given, for a single
    host. A dict otherwise, for tests and for one long-lived process.
    """
    if settings.candidates_url:
        return candidate_store_from_url(settings.candidates_url)
    if settings.candidates_path:
        return JsonFileCandidateStore(settings.candidates_path)
    return InMemoryCandidateStore()


class Proposal:
    """The outcome of proposing a candidate memory.

    A proposal is never an active memory (HLD §8). It is either queued for a
    human — in which case ``candidate_id`` is the handle a reviewer acts on —
    or, once automation is switched on and policy allows, activated. Either
    way the caller is told which, and why.
    """

    __slots__ = ("candidate_id", "decision", "memory")

    def __init__(self, memory: Memory, decision: ApprovalDecision, candidate_id: str | None = None) -> None:
        self.memory = memory
        self.decision = decision
        self.candidate_id = candidate_id

    @property
    def accepted(self) -> bool:
        """Whether the memory is now active rather than awaiting a human."""
        return self.memory.status is MemoryStatus.ACTIVE

    def to_dict(self) -> dict:
        """Render for an MCP response."""
        return {
            "memory": self.memory.to_dict(),
            "candidate_id": self.candidate_id,
            "status": self.memory.status.value,
            "accepted": self.accepted,
            "requires_human_approval": self.decision.needs_human,
            "reason": self.decision.reason,
        }


class FirmMemory:
    """Firm-wide engineering memory, over a replaceable provider."""

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        settings: Settings | None = None,
        scope: MemoryScope | None = None,
        candidates: CandidateStore | None = None,
        extractor: FactExtractor | None = None,
    ) -> None:
        self._provider = provider
        self._extractor = extractor
        self._settings = settings or Settings()
        self._scope = scope or MemoryScope.firm_wide()
        self._metrics = Metrics()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="firm-memory")
        self._approvals = ApprovalQueue(
            self.commit,
            store=candidates or _default_candidate_store(self._settings),
            policy=self._settings.approval_policy,
        )

    @classmethod
    def from_env(
        cls,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        provider: MemoryProvider | None = None,
        extractor: FactExtractor | None = None,
    ) -> FirmMemory:
        """Build the API from configuration and the current checkout.

        The default scope is this repo, its configured domains, and the firm —
        which is what an agent working in a checkout should see. Firm knowledge
        is always included: a convention that applies everywhere applies here
        too, and omitting it is the most common way a recall loses the answer.
        """
        settings = Settings.from_env(env)
        scope = MemoryScope.for_query(
            resolve_repo_slug(cwd, env=settings.env),
            domains=settings.default_domains,
        )
        selected = provider or get_provider(settings)
        return cls(
            selected,
            settings=settings,
            scope=scope,
            extractor=extractor if extractor is not None else _default_extractor(settings, selected),
        )

    # --- properties ----------------------------------------------------------

    @property
    def scope(self) -> MemoryScope:
        """The scope reads and writes default to."""
        return self._scope

    @property
    def settings(self) -> Settings:
        """The platform configuration in force."""
        return self._settings

    @property
    def metrics(self) -> Metrics:
        """Failure and latency counters for this instance."""
        return self._metrics

    @property
    def approvals(self) -> ApprovalQueue:
        """The queue of candidates awaiting a human."""
        return self._approvals

    # --- ingestion -----------------------------------------------------------

    def ingest(self, *documents: SourceDocument) -> list[Candidate]:
        """Distil raw material into candidates awaiting review.

        The only way content enters the platform. Nothing here writes to the
        provider: extraction produces candidates, and a candidate becomes firm
        knowledge only when a person approves it. An ingestion run that a
        reviewer never looks at leaves the pool exactly as it was.

        Returns the queued candidates, or an empty list when the source held no
        durable knowledge — which is a normal and frequent outcome.
        """
        if self._extractor is None:
            raise ConfigurationError(
                "Ingestion requires an extractor. Pass one to FirmMemory(extractor=...), "
                "or configure FIRM_MEMORY_EXTRACTION_MODEL. If dependencies are missing, run: uv sync"
            )
        if not documents:
            return []

        extracted = extract_all(self._extractor, documents)
        return [self._approvals.submit(candidate) for candidate in extracted]

    def for_scope(self, scope: MemoryScope) -> FirmMemory:
        """Return a view scoped to *scope*, sharing this instance's provider and queue."""
        clone = FirmMemory(self._provider, settings=self._settings, scope=scope)
        clone._metrics = self._metrics
        clone._executor = self._executor
        clone._approvals = self._approvals
        clone._extractor = self._extractor
        return clone

    # --- retrieval -----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        limit: int | None = None,
        types: Sequence[MemoryType | str] | None = None,
        tiers: Sequence[MemoryTier] = DEFAULT_SEARCH_TIERS,
        statuses: Sequence[MemoryStatus] = DEFAULT_SEARCH_STATUSES,
        task: str | None = None,
        min_score: float | None = None,
    ) -> list[Memory]:
        """Return active memories relevant to *query*. Never raises.

        The default ``tiers`` exclude episodic memory. That default is load
        bearing: to a vector store an absent task filter means "don't care", not
        "unset", so without it every task's scratch state would surface in
        ordinary recall.
        """
        if not query or not query.strip():
            return []

        effective_scope = scope or self._scope
        effective_limit = limit if limit is not None else self._settings.default_limit
        wanted_types = tuple(coerce_type(value) for value in (types or ()))
        floor = min_score if min_score is not None else self._settings.min_score

        results = self._guarded(
            metric_names.SEARCH,
            lambda: self._provider.search(
                query.strip(),
                effective_scope,
                effective_limit,
                types=wanted_types or None,
                tiers=tuple(tiers),
                statuses=tuple(statuses),
                task=task,
            ),
            default=[],
        )

        return [memory for memory in results if memory.score is None or memory.score >= floor]

    def get(self, memory_id: str) -> Memory | None:
        """Return one memory by canonical id, for citation and verification. Never raises."""
        if not memory_id or not memory_id.strip():
            return None
        return self._guarded(
            metric_names.GET,
            lambda: self._provider.get(memory_id.strip()),
            default=None,
        )

    # --- writes --------------------------------------------------------------

    def propose(
        self,
        content: str,
        *,
        type: MemoryType | str,
        scope: MemoryScope | None = None,
        confidence: float | None = None,
        tier: MemoryTier = MemoryTier.DURABLE,
        task: str | None = None,
        source: str = Source.AGENT,
        reference: str | None = None,
        author: str | None = None,
        evidence: str | None = None,
        doc_sha: str | None = None,
        **metadata: str,
    ) -> Proposal:
        """Put forward a candidate memory. Does **not** create an active memory.

        The candidate is validated against the taxonomy and the scope contract,
        stamped with provenance, and then run through the approval gate. Under
        the V1 posture the gate always defers to a human, so the memory is
        stored as ``proposed`` and waits.
        """
        if not content or not content.strip():
            raise InvalidInputError("propose() requires non-empty content")

        candidate = Memory(
            content=content,
            type=coerce_type(type),
            scope=scope or self._scope,
            provenance=Provenance(
                source=source,
                reference=reference,
                author=author,
                evidence=evidence,
                doc_sha=doc_sha,
            ),
            confidence=confidence if confidence is not None else 0.5,
            status=MemoryStatus.PROPOSED,
            tier=tier,
            task=task,
            metadata=metadata,
        )

        decision = evaluate(candidate, self._settings.approval_policy)
        if decision.needs_human:
            # A candidate is held outside the provider until a human endorses
            # it, so nothing unreviewed can ever be returned by a recall.
            queued: Candidate = self._approvals.submit(candidate)
            return Proposal(queued.memory, decision, candidate_id=queued.id)

        activated = Memory.from_dict({**candidate.to_dict(), "status": MemoryStatus.ACTIVE.value})
        return Proposal(self.commit(activated), decision)

    def commit(self, memory: Memory) -> Memory:
        """Store an already-approved memory verbatim.

        The path used by the human approval queue and by seeded knowledge, which
        have satisfied the gate elsewhere. Bypassing :meth:`propose` is the
        point; bypassing the taxonomy and scope contract is not, and the
        canonical model still enforces both.
        """
        return self._insert(memory)

    def correct(
        self,
        memory_id: str,
        *,
        reason: str,
        reporter: str | None = None,
    ) -> Memory | None:
        """Report a memory as stale or wrong.

        Demotes rather than deletes: the reporter may themselves be mistaken,
        and the record of a decision having been made — and unmade — is often
        the most valuable thing in the pool. Returns ``None`` when there is no
        such memory.
        """
        if not reason or not reason.strip():
            raise InvalidInputError("correct() requires a reason; an unexplained dispute cannot be resolved")

        existing = self.get(memory_id)
        if existing is None:
            return None

        disputed = dispute_memory(existing, reporter=reporter, reason=reason.strip())
        started = perf_counter()
        try:
            updated = self._provider.update(disputed)
        except Exception as exc:
            self._metrics.record(metric_names.UPDATE, seconds=perf_counter() - started, failed=True)
            raise ProviderError(f"Failed to record correction for {memory_id!r}: {exc}") from exc
        self._metrics.record(metric_names.UPDATE, seconds=perf_counter() - started)
        return updated

    # --- internals -----------------------------------------------------------

    def _insert(self, memory: Memory) -> Memory:
        """Write through the provider, recording latency and surfacing failures."""
        started = perf_counter()
        try:
            stored = self._provider.insert(memory)
        except ProviderError:
            self._metrics.record(metric_names.INSERT, seconds=perf_counter() - started, failed=True)
            raise
        except Exception as exc:
            self._metrics.record(metric_names.INSERT, seconds=perf_counter() - started, failed=True)
            raise ProviderError(f"Failed to store memory: {exc}") from exc
        self._metrics.record(metric_names.INSERT, seconds=perf_counter() - started)
        return stored

    def _guarded(self, operation: str, call: Callable[[], T], *, default: T) -> T:
        """Run *call* under a bounded timeout, absorbing every failure.

        A timed-out call is abandoned, not cancelled — a thread cannot be
        interrupted mid-query — so the provider may still be working when this
        returns. That is acceptable: the cost is a wasted query, and the
        alternative is holding up an agent behind a degraded database.
        """
        started = perf_counter()
        future = self._executor.submit(call)
        try:
            result = future.result(timeout=self._settings.timeout_seconds)
        except FutureTimeout:
            self._metrics.record(operation, seconds=perf_counter() - started, failed=True, timed_out=True)
            logger.warning(
                "Memory %s timed out after %.2fs; continuing without memory",
                operation,
                self._settings.timeout_seconds,
            )
            return default
        except Exception:
            self._metrics.record(operation, seconds=perf_counter() - started, failed=True)
            logger.warning("Memory %s failed; continuing without memory", operation, exc_info=True)
            return default

        self._metrics.record(operation, seconds=perf_counter() - started)
        return result if result is not None else default

    def close(self) -> None:
        """Release the worker threads used for bounded calls."""
        self._executor.shutdown(wait=False)

    def __enter__(self) -> FirmMemory:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
