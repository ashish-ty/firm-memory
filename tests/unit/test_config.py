"""Platform settings must stay provider-independent."""

import pytest

from firm_memory.config import DEFAULT_PROVIDER, Settings
from firm_memory.errors import ConfigurationError


def test_defaults_need_no_environment_at_all():
    """Memory is optional infrastructure; it must not refuse to start."""
    settings = Settings.from_env({})
    assert settings.provider == DEFAULT_PROVIDER
    assert settings.auto_approve_enabled is False


def test_no_caller_identity_is_configurable():
    """Memory is owned by the repo and the firm, never by a person or a team."""
    settings = Settings.from_env({"USER": "ashish", "FIRM_MEMORY_TEAM": "platform"})
    assert not hasattr(settings, "team")
    assert not hasattr(settings, "engineer")


def test_provider_is_selected_by_configuration():
    assert Settings.from_env({"FIRM_MEMORY_PROVIDER": "memory"}).provider == "memory"


def test_platform_settings_name_no_provider_concept():
    """A pgvector or embedding setting here would defeat provider replaceability."""
    fields = set(Settings.__dataclass_fields__)
    assert not any(term in field for field in fields for term in ("pg_", "dsn", "embed", "rerank", "collection"))


def test_domains_are_parsed_so_cross_repo_knowledge_is_reachable():
    settings = Settings.from_env({"FIRM_MEMORY_DOMAINS": "execution, mcx ,"})
    assert settings.default_domains == ("execution", "mcx")


def test_legacy_retrieval_settings_are_still_honoured():
    """An existing deployment must not silently change behaviour on upgrade."""
    settings = Settings.from_env({"FIRM_MEM0_TOP_K": "8", "FIRM_MEM0_THRESHOLD": "0.45"})
    assert settings.default_limit == 8
    assert settings.min_score == 0.45


def test_platform_names_win_over_legacy_ones():
    settings = Settings.from_env({"FIRM_MEMORY_LIMIT": "3", "FIRM_MEM0_TOP_K": "8"})
    assert settings.default_limit == 3


@pytest.mark.parametrize("bad", ["many", "0", "-3"])
def test_unusable_limits_are_rejected_naming_the_variable(bad):
    with pytest.raises(ConfigurationError, match="FIRM_MEMORY_LIMIT"):
        Settings.from_env({"FIRM_MEMORY_LIMIT": bad})


@pytest.mark.parametrize("bad", ["-0.1", "1.5", "lots"])
def test_min_score_must_be_a_probability(bad):
    with pytest.raises(ConfigurationError, match="FIRM_MEMORY_MIN_SCORE"):
        Settings.from_env({"FIRM_MEMORY_MIN_SCORE": bad})


def test_timeout_must_be_positive():
    with pytest.raises(ConfigurationError, match="TIMEOUT"):
        Settings.from_env({"FIRM_MEMORY_TIMEOUT_SECONDS": "0"})


def test_the_approval_policy_follows_the_settings():
    settings = Settings.from_env({"FIRM_MEMORY_AUTO_APPROVE": "on", "FIRM_MEMORY_AUTO_APPROVE_THRESHOLD": "0.9"})
    assert settings.approval_policy.auto_approve_enabled is True
    assert settings.approval_policy.auto_approve_threshold == 0.9


def test_the_provider_extractor_is_the_default():
    """mem0's dedup-aware extraction is the reason to run a memory framework."""
    assert Settings.from_env({}).extractor == "provider"


def test_the_extractor_is_selectable():
    assert Settings.from_env({"FIRM_MEMORY_EXTRACTOR": "llm"}).extractor == "llm"
    assert Settings.from_env({"FIRM_MEMORY_EXTRACTOR": "none"}).extractor == "none"
