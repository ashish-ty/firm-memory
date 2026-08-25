"""mem0 backend configuration is the single choke point for the deployment."""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.providers.mem0 import Mem0Settings, build_memory_config

MINIMAL_ENV = {"FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0"}


def test_missing_dsn_fails_fast_at_startup():
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_PG_DSN"):
        Mem0Settings.from_env({})


def test_no_caller_identity_is_read():
    settings = Mem0Settings.from_env({**MINIMAL_ENV, "USER": "ashish", "FIRM_MEM0_TEAM": "platform"})
    assert not hasattr(settings, "team")
    assert not hasattr(settings, "engineer")


def test_config_targets_pgvector_with_only_supported_keys():
    """PGVectorConfig rejects extra fields outright."""
    config = build_memory_config(Mem0Settings.from_env(MINIMAL_ENV))
    assert config["vector_store"]["provider"] == "pgvector"
    assert set(config["vector_store"]["config"]) <= {
        "connection_string",
        "collection_name",
        "embedding_model_dims",
    }


def test_config_carries_the_firm_taxonomy_not_mem0_defaults():
    instructions = build_memory_config(Mem0Settings.from_env(MINIMAL_ENV))["custom_instructions"]
    assert "architecture_decisions" in instructions
    assert "business_rules" in instructions
    assert "secret" in instructions.lower()


def test_reranking_is_local_by_default_so_nothing_leaves_the_network():
    config = build_memory_config(Mem0Settings.from_env(MINIMAL_ENV))
    assert config["reranker"]["provider"] == "sentence_transformer"


def test_reranker_can_be_disabled():
    config = build_memory_config(Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANK": "off"}))
    assert "reranker" not in config


def test_unparseable_numeric_override_is_rejected():
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_EMBEDDING_DIMS"):
        Mem0Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_EMBEDDING_DIMS": "many"})
