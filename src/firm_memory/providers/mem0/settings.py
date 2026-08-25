"""mem0 backend configuration.

The single choke point for the firm's self-hosted mem0 deployment: vector store,
embedding model, reranker, and the taxonomy-derived extraction instructions.
Changing any of them happens here once instead of in each application.

The deployment is deliberately self-hosted with no egress. Business rules like
"MCX orders always route through Risk Engine A" are closer to strategy IP than
to ordinary code comments, and the pool inherits the union of access control
across every repo feeding it — so the embedding model choice is the thing most
likely to quietly break that property.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...errors import ConfigurationError
from ...taxonomy import fact_extraction_instructions
from .namespace import DEFAULT_POOL_OWNER

DEFAULT_COLLECTION = "mem0_firm"
DEFAULT_EMBEDDING_DIMS = 1536
DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDER_PROVIDER = "openai"
DEFAULT_EMBEDDER_MODEL = "text-embedding-3-small"
# Cross-encoder runs locally, so reranking adds no external dependency.
DEFAULT_RERANKER_PROVIDER = "sentence_transformer"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_FALSEY = frozenset({"0", "off", "false", "no"})


@dataclass(frozen=True, slots=True)
class Mem0Settings:
    """Validated configuration for the firm's self-hosted mem0 deployment."""

    pg_dsn: str
    pool_owner: str = DEFAULT_POOL_OWNER
    collection_name: str = DEFAULT_COLLECTION
    embedding_dims: int = DEFAULT_EMBEDDING_DIMS
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODEL
    embedder_provider: str = DEFAULT_EMBEDDER_PROVIDER
    embedder_model: str = DEFAULT_EMBEDDER_MODEL
    reranker_enabled: bool = True
    reranker_provider: str = DEFAULT_RERANKER_PROVIDER
    reranker_model: str = DEFAULT_RERANKER_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Mem0Settings:
        """Build settings from the environment, failing fast on anything unusable."""
        import os

        env = env if env is not None else os.environ

        pg_dsn = (env.get("FIRM_MEM0_PG_DSN") or "").strip()
        if not pg_dsn:
            raise ConfigurationError(
                "FIRM_MEM0_PG_DSN is required (PostgreSQL/pgvector connection string for the mem0 store)"
            )

        # No team or engineer identity is read: memory is owned by the repo and
        # the firm, never by the person or team that triggered the call.
        return cls(
            pg_dsn=pg_dsn,
            # FIRM_MEM0_FIRM_OWNER is the pre-platform spelling, still honoured.
            pool_owner=(
                env.get("FIRM_MEM0_POOL_OWNER") or env.get("FIRM_MEM0_FIRM_OWNER") or DEFAULT_POOL_OWNER
            ).strip(),
            collection_name=(env.get("FIRM_MEM0_COLLECTION") or DEFAULT_COLLECTION).strip(),
            embedding_dims=_positive_int(env, "FIRM_MEM0_EMBEDDING_DIMS", DEFAULT_EMBEDDING_DIMS),
            llm_provider=(env.get("FIRM_MEM0_LLM_PROVIDER") or DEFAULT_LLM_PROVIDER).strip(),
            llm_model=(env.get("FIRM_MEM0_LLM_MODEL") or DEFAULT_LLM_MODEL).strip(),
            embedder_provider=(env.get("FIRM_MEM0_EMBEDDER_PROVIDER") or DEFAULT_EMBEDDER_PROVIDER).strip(),
            embedder_model=(env.get("FIRM_MEM0_EMBEDDER_MODEL") or DEFAULT_EMBEDDER_MODEL).strip(),
            reranker_enabled=(env.get("FIRM_MEM0_RERANK") or "on").strip().lower() not in _FALSEY,
            reranker_provider=(env.get("FIRM_MEM0_RERANKER_PROVIDER") or DEFAULT_RERANKER_PROVIDER).strip(),
            reranker_model=(env.get("FIRM_MEM0_RERANKER_MODEL") or DEFAULT_RERANKER_MODEL).strip(),
        )


def build_memory_config(settings: Mem0Settings) -> dict:
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
