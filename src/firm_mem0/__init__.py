"""firm-mem0 — the firm's memory namespace contract over self-hosted mem0 OSS.

Applications import ``FirmMemory`` and nothing else from mem0 directly::

    from firm_mem0 import FirmMemory, Layer

    memory = FirmMemory.from_env()
    context = memory.recall("how do we handle database migrations here")
    memory.remember(
        "Rejected the write-through cache: cache-stampede risk under bulk imports.",
        memory_type="architecture_decisions",
    )
"""

from __future__ import annotations

from .client import DEFAULT_RECALL_LAYERS, FirmMemory
from .config import Settings, build_memory_config
from .errors import (
    ConfigurationError,
    FirmMem0Error,
    InvalidInputError,
    MemoryBackendError,
    NamespaceError,
)
from .layers import layer_filter, layered_filters
from .namespace import Layer, Namespace
from .repo import resolve_repo_slug, slug_from_remote_url
from .taxonomy import CODING_CATEGORIES, Category, fact_extraction_instructions

__version__ = "0.1.0"

__all__ = [
    "CODING_CATEGORIES",
    "DEFAULT_RECALL_LAYERS",
    "Category",
    "ConfigurationError",
    "FirmMem0Error",
    "FirmMemory",
    "InvalidInputError",
    "Layer",
    "MemoryBackendError",
    "Namespace",
    "NamespaceError",
    "Settings",
    "build_memory_config",
    "fact_extraction_instructions",
    "layer_filter",
    "layered_filters",
    "resolve_repo_slug",
    "slug_from_remote_url",
]
