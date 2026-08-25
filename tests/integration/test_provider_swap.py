"""A second provider must be introducible without changing any consumer.

The pilot's stated success criterion. These tests exercise the seam directly:
the same API calls, the same canonical results, over a provider the platform
has never heard of.
"""

import pytest

from firm_memory import FirmMemory, Memory, MemoryScope, MemoryType, Settings
from firm_memory.errors import UnknownProviderError
from firm_memory.providers import get_provider, register_provider
from firm_memory.providers.base import MemoryProvider, MigratableProvider
from firm_memory.providers.inmemory import InMemoryProvider
from firm_memory.providers.mem0 import Mem0Provider
from tests.conftest import make_memory


class ReversedProvider(InMemoryProvider):
    """A provider the platform has never heard of, with different internals."""

    name = "reversed"

    def insert(self, memory: Memory) -> Memory:
        return super().insert(memory)


def test_both_shipped_providers_satisfy_the_interface():
    assert isinstance(InMemoryProvider(), MemoryProvider)
    assert isinstance(Mem0Provider(object(), settings=_mem0_settings()), MemoryProvider)


def test_the_provider_interface_stays_small():
    """Reranking, query normalisation and federation are deliberately absent."""
    declared = {
        name
        for name, value in vars(MemoryProvider).items()
        if not name.startswith("_") and (callable(value) or name in MemoryProvider.__annotations__)
    }
    assert declared | {"name"} == {"name", "insert", "search", "get", "update"}


def test_selection_is_configuration_driven():
    provider = get_provider(Settings(provider="memory"))
    assert isinstance(provider, InMemoryProvider)


def test_an_unknown_provider_fails_with_the_available_names():
    with pytest.raises(UnknownProviderError, match="mem0"):
        get_provider(Settings(provider="tencentdb"))


def test_a_third_party_provider_can_be_registered_and_selected():
    register_provider("reversed", lambda _settings: ReversedProvider())
    assert isinstance(get_provider(Settings(provider="reversed")), ReversedProvider)


def test_the_api_behaves_identically_over_a_different_provider():
    """The same calls, the same canonical results — no consumer change."""
    register_provider("reversed", lambda _settings: ReversedProvider())
    settings = Settings(provider="reversed", min_score=0.0)

    with FirmMemory(get_provider(settings), settings=settings, scope=MemoryScope.for_repo("oms")) as memory:
        from firm_memory.lifecycle import approve

        stored = memory.commit(
            approve(
                make_memory(
                    content="MCX orders always route through Risk Engine A.",
                    scope=MemoryScope.for_repo("oms"),
                    type=MemoryType.BUSINESS_RULE,
                ),
                approver="lead",
            )
        )
        [hit] = memory.search("mcx orders always route through risk engine a")

        assert hit.id == stored.id
        assert hit.type is MemoryType.BUSINESS_RULE
        assert hit.scope == MemoryScope.for_repo("oms")


def test_migration_moves_a_pool_without_losing_the_canonical_form():
    """Export/import is a migration capability, not part of the runtime interface."""
    source = InMemoryProvider()
    target = ReversedProvider()
    assert isinstance(source, MigratableProvider)

    originals = [
        source.insert(make_memory(content="Order IDs come from the sequencer.")),
        source.insert(make_memory(content="MCX orders route through Risk Engine A.",
                                  type=MemoryType.BUSINESS_RULE,
                                  scope=MemoryScope(firm=True, repos=("oms",)))),
    ]

    assert target.import_all(source.export_all()) == 2

    migrated = {memory.id: memory for memory in target.export_all()}
    for original in originals:
        assert migrated[original.id].to_dict() == original.to_dict()


def test_migration_is_not_required_of_a_provider():
    """Requiring it would raise the bar for adding one."""
    assert not isinstance(Mem0Provider(object(), settings=_mem0_settings()), MigratableProvider)


def _mem0_settings():
    from firm_memory.providers.mem0 import Mem0Settings

    return Mem0Settings(pg_dsn="postgresql://mem0:pw@db.internal:5432/mem0")
