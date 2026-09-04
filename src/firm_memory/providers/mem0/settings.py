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

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.util import find_spec

from ...errors import ConfigurationError
from ...taxonomy import fact_extraction_instructions
from .embedders import FASTEMBED_PROVIDER
from .embedders import register as register_fastembed
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

logger = logging.getLogger(__name__)

#: The import each reranker provider needs. Reranking is a retrieval-quality
#: enhancement, not a correctness requirement, so a missing one is a warning
#: rather than a failure to start.
_RERANKER_IMPORTS: dict[str, str] = {
    "sentence_transformer": "sentence_transformers",
    "huggingface": "sentence_transformers",
    "cohere": "cohere",
}


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
    #: Credentials. mem0's LiteLLM class ignores ``api_key`` and reads litellm's
    #: own environment variables instead, so these matter for the OpenAI-shaped
    #: providers — including an OpenAI-compatible gateway such as a LiteLLM proxy.
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    embedder_api_key: str | None = None
    embedder_base_url: str | None = None
    #: pgvector index. HNSW is the sane default; DiskANN needs the extension and
    #: only applies below 2000 dimensions.
    hnsw: bool = True
    diskann: bool = False

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
            llm_api_key=_optional(env, "FIRM_MEM0_LLM_API_KEY"),
            llm_base_url=_optional(env, "FIRM_MEM0_LLM_BASE_URL"),
            # One gateway key can serve both halves: FIRM_MEM0_API_KEY/BASE_URL
            # is the fallback so a LiteLLM proxy needs configuring only once.
            embedder_api_key=_optional(env, "FIRM_MEM0_EMBEDDER_API_KEY") or _optional(env, "FIRM_MEM0_API_KEY"),
            embedder_base_url=_optional(env, "FIRM_MEM0_EMBEDDER_BASE_URL") or _optional(env, "FIRM_MEM0_BASE_URL"),
            hnsw=(env.get("FIRM_MEM0_HNSW") or "on").strip().lower() not in _FALSEY,
            diskann=(env.get("FIRM_MEM0_DISKANN") or "off").strip().lower() not in _FALSEY,
        )


def build_memory_config(settings: Mem0Settings) -> dict:
    """Render the dict accepted by ``mem0.Memory.from_config``.

    Only pgvector's supported keys are emitted — ``PGVectorConfig`` rejects
    extra fields outright.
    """
    llm_config: dict = {"model": settings.llm_model}
    if settings.llm_api_key:
        llm_config["api_key"] = settings.llm_api_key
    if settings.llm_base_url:
        llm_config["openai_base_url"] = settings.llm_base_url

    if settings.embedder_provider == FASTEMBED_PROVIDER:
        # mem0's fastembed embedder returns a numpy array, which psycopg cannot
        # bind. Swap in the corrected subclass before the factory resolves it.
        register_fastembed()

    embedder_config: dict = {"model": settings.embedder_model, "embedding_dims": settings.embedding_dims}
    if settings.embedder_api_key:
        embedder_config["api_key"] = settings.embedder_api_key
    if settings.embedder_base_url:
        embedder_config["openai_base_url"] = settings.embedder_base_url

    config: dict = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "connection_string": settings.pg_dsn,
                "collection_name": settings.collection_name,
                "embedding_model_dims": settings.embedding_dims,
                "hnsw": settings.hnsw,
                "diskann": settings.diskann,
            },
        },
        "llm": {
            "provider": settings.llm_provider,
            "config": llm_config,
        },
        "embedder": {
            "provider": settings.embedder_provider,
            "config": embedder_config,
        },
        "custom_instructions": fact_extraction_instructions(),
    }

    if settings.reranker_enabled and _reranker_available(settings.reranker_provider):
        config["reranker"] = {
            "provider": settings.reranker_provider,
            "config": {"model": settings.reranker_model},
        }

    return config


def _reranker_available(provider: str) -> bool:
    """Whether *provider*'s dependency is installed.

    Memory is best-effort infrastructure. Refusing to start the whole memory
    system because an optional ranking model is absent trades a small quality
    loss for a total outage, which is the wrong way round — so a missing
    dependency degrades to no reranking and says so.
    """
    required = _RERANKER_IMPORTS.get(provider)
    if required is None:
        return True

    if find_spec(required) is not None:
        return True

    logger.warning(
        "Reranking is enabled but %r is not installed, so it is disabled for this session. "
        "Retrieval still works, ranked by the vector store alone. Install it with: "
        "pip install 'firm-memory[rerank]'",
        required,
    )
    return False


def _optional(env: Mapping[str, str], key: str) -> str | None:
    """Read a setting that is legitimately absent, normalising blanks to ``None``."""
    return (env.get(key) or "").strip() or None


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
