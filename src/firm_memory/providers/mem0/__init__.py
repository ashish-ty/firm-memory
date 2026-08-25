"""mem0 provider: the firm's canonical memory projected onto mem0 OSS.

Everything mem0-shaped lives under this package — entity axes, payload keys,
filter dialect, backend configuration. Nothing above it imports mem0.
"""

from __future__ import annotations

from .namespace import DEFAULT_FIRM_OWNER, Layer, Namespace, Partition
from .provider import Mem0Provider
from .settings import Mem0Settings, build_memory_config

__all__ = [
    "DEFAULT_FIRM_OWNER",
    "Layer",
    "Mem0Provider",
    "Mem0Settings",
    "Namespace",
    "Partition",
    "build_memory_config",
]
