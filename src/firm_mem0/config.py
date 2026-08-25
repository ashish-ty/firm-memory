"""Settings and mem0 backend configuration.

This is the single choke point every application imports. Changing the vector
store, embedding model, reranker, or taxonomy happens here once instead of in
each app.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import ConfigurationError
from .namespace import DEFAULT_FIRM_OWNER
from .taxonomy import fact_extraction_instructions

DEFAULT_COLLECTION = "mem0_firm"
DEFAULT_EMBEDDING_DIMS = 1536
DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDER_PROVIDER = "openai"
DEFAULT_EMBEDDER_MODEL = "text-embedding-3-small"
# Cross-encoder runs locally, so reranking adds no external dependency.
DEFAULT_RERANKER_PROVIDER = "sentence_transformer"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.3

_FALSEY = frozenset({"0", "off", "false", "no"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration for the firm's self-hosted mem0 deployment."""

    pg_dsn: str
    firm_owner: str = DEFAULT_FIRM_OWNER
    collection_name: str = DEFAULT_COLLECTION
    embedding_dims: int = DEFAULT_EMBEDDING_DIMS
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODEL
    embedder_provider: str = DEFAULT_EMBEDDER_PROVIDER
    embedder_model: str = DEFAULT_EMBEDDER_MODEL
    reranker_enabled: bool = True
    reranker_provider: str = DEFAULT_RERANKER_PROVIDER
    reranker_model: str = DEFAULT_RERANKER_MODEL
    default_top_k: int = DEFAULT_TOP_K
    default_threshold: float = DEFAULT_THRESHOLD

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the environment, failing fast on anything unusable."""
        env = env if env is not None else os.environ

        pg_dsn = (env.get("FIRM_MEM0_PG_DSN") or "").strip()
        if not pg_dsn:
            raise ConfigurationError(
                "FIRM_MEM0_PG_DSN is required (PostgreSQL/pgvector connection string for the mem0 store)"
            )

        # No team or engineer identity is read: memory is owned by the repo and
        # the firm, never by the person or team that triggered the call.
        top_k = _positive_int(env, "FIRM_MEM0_TOP_K", DEFAULT_TOP_K)
        threshold = _probability(env, "FIRM_MEM0_THRESHOLD", DEFAULT_THRESHOLD)

        return cls(
            pg_dsn=pg_dsn,
            firm_owner=(env.get("FIRM_MEM0_FIRM_OWNER") or DEFAULT_FIRM_OWNER).strip(),
            collection_name=(env.get("FIRM_MEM0_COLLECTION") or DEFAULT_COLLECTION).strip(),
            embedding_dims=_positive_int(env, "FIRM_MEM0_EMBEDDING_DIMS", DEFAULT_EMBEDDING_DIMS),
            llm_provider=(env.get("FIRM_MEM0_LLM_PROVIDER") or DEFAULT_LLM_PROVIDER).strip(),
            llm_model=(env.get("FIRM_MEM0_LLM_MODEL") or DEFAULT_LLM_MODEL).strip(),
            embedder_provider=(env.get("FIRM_MEM0_EMBEDDER_PROVIDER") or DEFAULT_EMBEDDER_PROVIDER).strip(),
            embedder_model=(env.get("FIRM_MEM0_EMBEDDER_MODEL") or DEFAULT_EMBEDDER_MODEL).strip(),
            reranker_enabled=(env.get("FIRM_MEM0_RERANK") or "on").strip().lower() not in _FALSEY,
            reranker_provider=(env.get("FIRM_MEM0_RERANKER_PROVIDER") or DEFAULT_RERANKER_PROVIDER).strip(),
            reranker_model=(env.get("FIRM_MEM0_RERANKER_MODEL") or DEFAULT_RERANKER_MODEL).strip(),
            default_top_k=top_k,
            default_threshold=threshold,
        )


def build_memory_config(settings: Settings) -> dict:
    """Render the dict accepted by ``mem0.Memory.from_config``.

    Only pgvector's supported keys are emitted — ``PGVectorConfig`` rejects
    extra fields outright.
    """
    config: dict = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "connection_string": settings.pg_dsn,
                "collection_name": settings.collection_name,
                "embedding_model_dims": settings.embedding_dims,
            },
        },
        "llm": {
            "provider": settings.llm_provider,
            "config": {"model": settings.llm_model},
        },
        "embedder": {
            "provider": settings.embedder_provider,
            "config": {"model": settings.embedder_model},
        },
        "custom_instructions": fact_extraction_instructions(),
    }

    if settings.reranker_enabled:
        config["reranker"] = {
            "provider": settings.reranker_provider,
            "config": {"model": settings.reranker_model},
        }

    return config


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"{key} must be greater than 0, got {value}")
    return value


def _probability(env: Mapping[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a number, got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ConfigurationError(f"{key} must be between 0.0 and 1.0, got {value}")
    return value
