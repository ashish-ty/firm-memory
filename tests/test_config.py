"""Settings are the single choke point for backend config across every app."""

import pytest

from firm_mem0.config import Settings, build_memory_config
from firm_mem0.errors import ConfigurationError

MINIMAL_ENV = {
    "FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0",
}


def test_from_env_reads_required_values():
    settings = Settings.from_env(MINIMAL_ENV)
    assert settings.pg_dsn == "postgresql://mem0:pw@db.internal:5432/mem0"
    assert settings.firm_owner == "firm"


def test_missing_dsn_fails_fast():
    env = {k: v for k, v in MINIMAL_ENV.items() if k != "FIRM_MEM0_PG_DSN"}
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_PG_DSN"):
        Settings.from_env(env)


def test_caller_identity_is_not_part_of_configuration():
    """No team or engineer axis exists, so neither is read or stored."""
    settings = Settings.from_env({**MINIMAL_ENV, "USER": "ashish", "FIRM_MEM0_TEAM": "platform"})
    assert not hasattr(settings, "team")
    assert not hasattr(settings, "engineer")


def test_reranking_is_on_by_default_and_can_be_disabled():
    assert Settings.from_env(MINIMAL_ENV).reranker_enabled is True
    assert Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANK": "off"}).reranker_enabled is False
    assert Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANK": "false"}).reranker_enabled is False


def test_numeric_overrides_are_parsed():
    settings = Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_TOP_K": "8", "FIRM_MEM0_THRESHOLD": "0.45"})
    assert settings.default_top_k == 8
    assert settings.default_threshold == 0.45


def test_unparseable_numeric_override_is_rejected():
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_TOP_K"):
        Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_TOP_K": "many"})


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_top_k_must_be_positive(bad):
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_TOP_K"):
        Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_TOP_K": bad})


@pytest.mark.parametrize("bad", ["-0.1", "1.5"])
def test_threshold_must_be_a_probability(bad):
    with pytest.raises(ConfigurationError, match="FIRM_MEM0_THRESHOLD"):
        Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_THRESHOLD": bad})


def test_memory_config_targets_pgvector_with_only_supported_keys():
    config = build_memory_config(Settings.from_env(MINIMAL_ENV))
    assert config["vector_store"]["provider"] == "pgvector"
    assert set(config["vector_store"]["config"]) <= {
        "connection_string",
        "collection_name",
        "embedding_model_dims",
    }
    assert config["vector_store"]["config"]["connection_string"] == MINIMAL_ENV["FIRM_MEM0_PG_DSN"]


def test_memory_config_carries_coding_fact_extraction_instructions():
    config = build_memory_config(Settings.from_env(MINIMAL_ENV))
    instructions = config["custom_instructions"]
    assert "architecture_decisions" in instructions
    assert "secret" in instructions.lower()


def test_reranker_is_local_when_enabled():
    config = build_memory_config(Settings.from_env(MINIMAL_ENV))
    assert config["reranker"]["provider"] == "sentence_transformer"


def test_reranker_absent_when_disabled():
    config = build_memory_config(Settings.from_env({**MINIMAL_ENV, "FIRM_MEM0_RERANK": "off"}))
    assert "reranker" not in config
