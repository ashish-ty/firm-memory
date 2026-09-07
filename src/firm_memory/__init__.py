"""firm-memory — the firm's memory contract over a replaceable memory provider.

    CodeGraph answers "what is the code doing?"
    Firm Memory answers "why do we build it this way?"

The platform owns **what a firm memory means** — taxonomy, scope, provenance,
lifecycle. The provider owns **how it is stored and retrieved**. MCP owns **how
AI agents access it**. Keeping those three apart is what lets the firm move from
mem0 to any future memory framework without touching a consumer.

Typical use::

    from firm_memory import FirmMemory, MemoryScope, MemoryType

    memory = FirmMemory.from_env()

    for hit in memory.search("why does OMS reject orders after 15:20"):
        print(hit.id, hit.content)

    memory.propose(
        "Cash strategies stop sending at 15:20 because the exchange rejects after that.",
        type=MemoryType.BUSINESS_RULE,
        scope=MemoryScope(domains=("execution",), repos=("oms",)),
        reference="mr-4821",
    )
"""

from __future__ import annotations

from .config import Settings
from .errors import (
    CandidateStoreError,
    ConfigurationError,
    ExtractionError,
    FirmMemoryError,
    InvalidInputError,
    LifecycleError,
    ProviderError,
    ScopeError,
    TaxonomyError,
    UnknownProviderError,
)
from .ingestion import (
    Amendment,
    ApprovalQueue,
    Candidate,
    CandidateStore,
    CompletionClient,
    FactExtractor,
    LLMFactExtractor,
    PostgresCandidateStore,
    SourceDocument,
)
from .lifecycle import ApprovalDecision, ApprovalPolicy, evaluate, requires_human_approval
from .memory import FirmMemory, Proposal
from .metrics import Metrics
from .models import (
    DEFAULT_SEARCH_STATUSES,
    DEFAULT_SEARCH_TIERS,
    Memory,
    MemoryStatus,
    MemoryTier,
)
from .provenance import Provenance, Source
from .providers import MemoryProvider, MigratableProvider, get_provider, register_provider
from .repo import resolve_repo_slug, slug_from_remote_url
from .scope import MemoryScope
from .taxonomy import CODING_CATEGORIES, Category, MemoryType, fact_extraction_instructions

__version__ = "1.0.0"

__all__ = [
    "CODING_CATEGORIES",
    "DEFAULT_SEARCH_STATUSES",
    "DEFAULT_SEARCH_TIERS",
    "Amendment",
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalQueue",
    "Candidate",
    "CandidateStore",
    "CandidateStoreError",
    "Category",
    "CompletionClient",
    "ConfigurationError",
    "ExtractionError",
    "FactExtractor",
    "FirmMemory",
    "FirmMemoryError",
    "InvalidInputError",
    "LLMFactExtractor",
    "LifecycleError",
    "Memory",
    "MemoryProvider",
    "MemoryScope",
    "MemoryStatus",
    "MemoryTier",
    "MemoryType",
    "Metrics",
    "MigratableProvider",
    "PostgresCandidateStore",
    "Proposal",
    "Provenance",
    "ProviderError",
    "ScopeError",
    "Settings",
    "Source",
    "SourceDocument",
    "TaxonomyError",
    "UnknownProviderError",
    "evaluate",
    "fact_extraction_instructions",
    "get_provider",
    "register_provider",
    "requires_human_approval",
    "resolve_repo_slug",
    "slug_from_remote_url",
]
