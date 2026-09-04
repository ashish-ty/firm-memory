"""A corrected fastembed embedder.

mem0 2.0.14's ``FastEmbedEmbedding.embed`` returns ``embeddings[0]`` straight
from fastembed, which is a numpy ``ndarray`` — despite its own docstring
promising a list, and unlike every sibling embedder, which call ``.tolist()``.
psycopg cannot adapt an ndarray, so writing to pgvector fails with::

    psycopg.ProgrammingError: cannot adapt type 'ndarray' using placeholder '%s'

The failure lands at insert time, after extraction and after a human has
approved — the most expensive possible moment to discover it.

This subclass coerces the result to a list. If a later mem0 release fixes the
bug the coercion becomes a no-op, so this stays safe to keep.
"""

from __future__ import annotations

import logging
from typing import Any

from ...errors import ConfigurationError

logger = logging.getLogger(__name__)

#: The provider name this is registered under, replacing mem0's own.
FASTEMBED_PROVIDER = "fastembed"

_CLASS_PATH = "firm_memory.providers.mem0.embedders.ListFastEmbedEmbedding"


def _as_list(vector: Any) -> Any:
    """Return *vector* as a plain list of floats, whatever shape it arrives in."""
    tolist = getattr(vector, "tolist", None)
    return tolist() if callable(tolist) else vector


def _base_class() -> Any:
    """Import mem0's fastembed embedder, with an actionable error if absent."""
    try:
        from mem0.embeddings.fastembed import FastEmbedEmbedding
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigurationError(
            "The fastembed embedder requires the fastembed package. Install it with: "
            "pip install 'firm-memory[fastembed]'"
        ) from exc
    return FastEmbedEmbedding


try:  # pragma: no cover - exercised only where fastembed is installed
    _FastEmbedBase = _base_class()
except ConfigurationError:
    _FastEmbedBase = object


class ListFastEmbedEmbedding(_FastEmbedBase):  # type: ignore[misc,valid-type]
    """fastembed, returning the list its base class already promises."""

    def embed(self, text, memory_action=None):
        """Embed *text*, coercing the vector to a list psycopg can bind."""
        return _as_list(super().embed(text, memory_action))

    def embed_batch(self, texts, memory_action="add"):
        """Embed *texts*, coercing every vector."""
        return [_as_list(vector) for vector in super().embed_batch(texts, memory_action)]


def register() -> None:
    """Point mem0's embedder factory at the corrected class.

    Registered under mem0's own ``fastembed`` name rather than a new one, so an
    existing configuration keeps working and simply stops being broken. Calling
    this more than once is harmless.
    """
    try:
        from mem0.utils.factory import EmbedderFactory
    except ImportError:  # pragma: no cover - mem0 absent; nothing to correct
        return

    current = EmbedderFactory.provider_to_class.get(FASTEMBED_PROVIDER)
    if current == _CLASS_PATH:
        return

    EmbedderFactory.provider_to_class[FASTEMBED_PROVIDER] = _CLASS_PATH
    logger.debug("Registered %s as the %r embedder", _CLASS_PATH, FASTEMBED_PROVIDER)
