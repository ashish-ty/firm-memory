"""The fastembed correction: mem0 <2.0.20 returns arrays psycopg cannot bind."""

import pytest

from firm_memory.providers.mem0.embedders import FASTEMBED_PROVIDER, _as_list, register

pytest.importorskip("mem0.utils.factory", reason="mem0ai not installed")


class FakeArray:
    """Stands in for numpy's ndarray: convertible, but not a list."""

    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return self._values


def test_an_array_is_converted_to_a_list():
    assert _as_list(FakeArray([0.1, 0.2])) == [0.1, 0.2]


def test_a_list_passes_through_untouched():
    """So the correction is a no-op on a mem0 release that already returns lists."""
    assert _as_list([0.1, 0.2]) == [0.1, 0.2]


def test_registration_points_mem0_at_the_corrected_class():
    from mem0.utils.factory import EmbedderFactory

    original = EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER)
    try:
        register()
        assert EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER].startswith("firm_memory.")
    finally:
        if original is not None:
            EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER] = original


def test_registering_twice_is_harmless():
    from mem0.utils.factory import EmbedderFactory

    original = EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER)
    try:
        register()
        register()
        assert EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER].startswith("firm_memory.")
    finally:
        if original is not None:
            EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER] = original


def test_selecting_fastembed_registers_the_correction():
    from mem0.utils.factory import EmbedderFactory

    from firm_memory.providers.mem0 import Mem0Settings, build_memory_config

    original = EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER)
    try:
        build_memory_config(
            Mem0Settings(pg_dsn="postgresql://x@y/z", embedder_provider="fastembed", embedding_dims=384)
        )
        assert EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER].startswith("firm_memory.")
    finally:
        if original is not None:
            EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER] = original


def test_another_embedder_leaves_the_registry_alone():
    from mem0.utils.factory import EmbedderFactory

    from firm_memory.providers.mem0 import Mem0Settings, build_memory_config

    original = EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER)
    build_memory_config(Mem0Settings(pg_dsn="postgresql://x@y/z", embedder_provider="openai"))
    assert EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER) == original
