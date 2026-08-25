"""Exception hierarchy.

Every failure surfaced by this package derives from :class:`FirmMem0Error`, so
callers can wrap all memory concerns in one ``except`` without catching
unrelated bugs.
"""

from __future__ import annotations


class FirmMem0Error(Exception):
    """Base class for every error raised by firm-mem0."""


class ConfigurationError(FirmMem0Error):
    """Environment or settings are missing/invalid. Raised at startup, not mid-request."""


class NamespaceError(FirmMem0Error):
    """The namespace contract was violated (bad identity value, or a layer used without its scope)."""


class InvalidInputError(FirmMem0Error):
    """Caller passed unusable content to a boundary method."""


class MemoryBackendError(FirmMem0Error):
    """The underlying mem0 backend failed. Wraps the original exception as ``__cause__``."""
