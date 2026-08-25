"""Provider abstraction: the seam between firm semantics and storage.

Firm Memory owns *what a memory means* — taxonomy, scope, provenance,
lifecycle. A provider owns *how it is stored and retrieved*, including
embeddings, vector search and ranking. Keeping that line sharp is what lets the
firm change memory engines without touching OpenCode or the MCP contract.
"""

from __future__ import annotations

from .base import MemoryProvider, MigratableProvider
from .registry import available_providers, get_provider, register_provider

__all__ = [
    "MemoryProvider",
    "MigratableProvider",
    "available_providers",
    "get_provider",
    "register_provider",
]
