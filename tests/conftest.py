"""Shared fixtures.

Tests default to the in-memory provider so that what is under test is the
firm's semantics — taxonomy, scope, lifecycle, reliability — rather than mem0's
behaviour. The mem0 projection has its own unit and contract tests.
"""

from __future__ import annotations

import pytest

from firm_memory import FirmMemory, Memory, MemoryScope, MemoryType, Settings
from firm_memory.providers.inmemory import InMemoryProvider

REPO = "acme-billing-svc"


@pytest.fixture
def provider() -> InMemoryProvider:
    return InMemoryProvider()


@pytest.fixture
def settings() -> Settings:
    """Platform defaults, minus the relevance floor.

    ``min_score`` is calibrated for a provider that scores semantically. The
    in-memory provider scores by lexical term overlap, where a perfectly good
    answer phrased differently scores near zero — so applying the production
    floor here would test the fixture's vocabulary, not the platform. The floor
    itself is covered directly in the API tests.
    """
    return Settings(min_score=0.0)


@pytest.fixture
def memory(provider: InMemoryProvider, settings: Settings) -> FirmMemory:
    api = FirmMemory(provider, settings=settings, scope=MemoryScope.for_query(REPO, domains=("execution",)))
    yield api
    api.close()


def make_memory(**overrides) -> Memory:
    """A valid canonical memory, with fields overridable per test."""
    defaults = {
        "content": "Order IDs come from the sequencer, never the service.",
        "type": MemoryType.ARCHITECTURE_DECISION,
        "scope": MemoryScope.for_repo(REPO),
    }
    return Memory(**{**defaults, **overrides})
