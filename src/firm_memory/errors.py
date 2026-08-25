"""Exception hierarchy.

Every failure raised by this package derives from :class:`FirmMemoryError`, so a
consumer can wrap all memory concerns in one ``except`` without swallowing
unrelated bugs.

Note the split in intent: :class:`ConfigurationError` and :class:`TaxonomyError`
are *programmer* errors and are raised eagerly, while
:class:`ProviderError` is an *operational* error that
:class:`firm_memory.memory.FirmMemory` deliberately absorbs — memory is best
effort and must never fail the agent's request (see HLD §11).
"""

from __future__ import annotations


class FirmMemoryError(Exception):
    """Base class for every error raised by firm-memory."""


class ConfigurationError(FirmMemoryError):
    """Environment or settings are missing/invalid. Raised at startup, not mid-request."""


class TaxonomyError(FirmMemoryError):
    """A memory was given a type outside the firm's taxonomy."""


class ScopeError(FirmMemoryError):
    """The scope contract was violated (bad slug, or a memory scoped to nothing)."""


class LifecycleError(FirmMemoryError):
    """An illegal status transition, or an approval that policy forbids."""


class InvalidInputError(FirmMemoryError):
    """Caller passed unusable content to a boundary method."""


class ProviderError(FirmMemoryError):
    """The underlying memory provider failed. Wraps the original exception as ``__cause__``."""


class UnknownProviderError(ConfigurationError):
    """Configuration named a provider that is not registered."""
