"""mem0 backend configuration is the single choke point for the deployment."""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.providers.mem0 import Mem0Settings, build_memory_config

MINIMAL_ENV = {"FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0"}


def test_missing_dsn_fails_fast_at_startup():
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_PG_DSN"):
        Mem0Settings.from_env({})


def test_the_pre_platform_pool_owner_variable_is_still_honoured():
    """An existing deployment must not silently repartition on upgrade."""
    settings = Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_FIRM_OWNER": "acme-eng"})
    assert settings.pool_owner == "acme-eng"


def test_no_caller_identity_is_read():
    settings = Mem0Settings.from_env({**MINIMAL_ENV, "USER": "ashish", "FIRM_MEM0_TEAM": "platform"})
    assert not hasattr(settings, "team")
    assert not hasattr(settings, "engineer")


def test_config_targets_pgvector_and_the_real_schema_accepts_it():
    """PGVectorConfig rejects extra fields outright, so validate against it."""
    pgvector_config = pytest.importorskip(
        "mem0.configs.vector_stores.pgvector", reason="mem0ai not installed"
    )
    config = build_memory_config(Mem0Settings.from_env(MINIMAL_ENV))

    assert config["vector_store"]["provider"] == "pgvector"
    # Constructing it is the assertion: an unsupported key raises here.
    pgvector_config.PGVectorConfig(**config["vector_store"]["config"])


def test_index_choice_is_configurable():
    config = build_memory_config(Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_HNSW": "off"}))
    assert config["vector_store"]["config"]["hnsw"] is False


def test_config_carries_the_firm_taxonomy_not_mem0_defaults():
    instructions = build_memory_config(Mem0Settings.from_env(MINIMAL_ENV))["custom_instructions"]
    assert "architecture_decisions" in instructions
    assert "business_rules" in instructions
    assert "secret" in instructions.lower()


def test_reranking_is_local_by_default_so_nothing_leaves_the_network():
    """The policy lives in the settings; whether it is emitted depends on the
    optional package being installed, which is covered separately below."""
    settings = Mem0Settings.from_env(MINIMAL_ENV)
    assert settings.reranker_enabled is True
    assert settings.reranker_provider == "sentence_transformer"
    assert "cross-encoder/" in settings.reranker_model


def test_reranker_can_be_disabled():
    config = build_memory_config(Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANK": "off"}))
    assert "reranker" not in config


def test_unparseable_numeric_override_is_rejected():
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_EMBEDDING_DIMS"):
        Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_EMBEDDING_DIMS": "many"})


def test_a_missing_reranker_dependency_disables_it_rather_than_failing():
    """Memory is best-effort: an absent ranking model must not stop it starting."""
    settings = Mem0Settings.from_env(MINIMAL_ENV)
    assert settings.reranker_enabled is True

    config = build_memory_config(settings)
    import importlib.util

    if importlib.util.find_spec("sentence_transformers") is None:
        assert "reranker" not in config, "a missing optional dependency must not be emitted"
    else:  # pragma: no cover - only when the rerank extra is installed
        assert config["reranker"]["provider"] == "sentence_transformer"


def test_an_unrecognised_reranker_provider_is_left_alone():
    """We do not know its dependency, so we must not silently drop it."""
    settings = Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANKER_PROVIDER": "zero_entropy"})
    assert build_memory_config(settings)["reranker"]["provider"] == "zero_entropy"
