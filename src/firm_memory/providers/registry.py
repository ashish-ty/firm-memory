"""Provider selection, driven by configuration rather than by imports.

    provider = get_provider(settings)

with::

    memory:
      provider: mem0

Built-in providers are registered lazily so that selecting one does not pay the
import cost of the others — importing ``firm_memory`` must not pull in the mem0
SDK, or a consumer that has swapped providers still carries the old dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..errors import UnknownProviderError
from .base import MemoryProvider

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import Settings

#: name -> factory taking Settings and returning a provider instance.
ProviderFactory = Callable[["Settings"], MemoryProvider]

_REGISTRY: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register *factory* under *name*, replacing any previous registration."""
    _REGISTRY[name.strip().lower()] = factory


def available_providers() -> tuple[str, ...]:
    """Names that :func:`get_provider` will accept, sorted."""
    _register_builtins()
    return tuple(sorted(_REGISTRY))


def get_provider(settings: Settings) -> MemoryProvider:
    """Build the provider named by *settings*."""
    _register_builtins()
    name = settings.provider.strip().lower()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise UnknownProviderError(
            f"Unknown memory provider {settings.provider!r}. Available: {', '.join(sorted(_REGISTRY))}"
        )
    return factory(settings)


def _register_builtins() -> None:
    """Register the providers shipped with the package, importing each on demand."""
    if "mem0" not in _REGISTRY:
        register_provider("mem0", _build_mem0)
    if "memory" not in _REGISTRY:
        register_provider("memory", _build_inmemory)


def _build_mem0(settings: Settings) -> MemoryProvider:
    from .mem0 import Mem0Provider

    return Mem0Provider.from_settings(settings)


def _build_inmemory(settings: Settings) -> MemoryProvider:
    from .inmemory import InMemoryProvider

    return InMemoryProvider()
