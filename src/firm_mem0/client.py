"""The facade every application imports.

Applications never construct ``mem0.Memory`` directly. Going through
:class:`FirmMemory` means scoping cannot be forgotten: ``remember`` and
``recall`` inject the namespace themselves, so an unscoped write is not
expressible.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .config import Settings, build_memory_config
from .errors import InvalidInputError, MemoryBackendError
from .layers import layered_filters
from .namespace import Layer, Namespace
from .repo import resolve_repo_slug

#: Recall spans this codebase's knowledge and the firm's standards. There is no
#: per-engineer or per-team layer to union in — see :mod:`firm_mem0.namespace`.
DEFAULT_RECALL_LAYERS: tuple[Layer, ...] = (Layer.REPO, Layer.FIRM)

DEFAULT_SOURCE = "sdk"


class FirmMemory:
    """Namespace-enforcing wrapper around a mem0 ``Memory`` instance."""

    def __init__(self, memory: Any, namespace: Namespace, settings: Settings) -> None:
        self._memory = memory
        self._namespace = namespace
        self._settings = settings

    @classmethod
    def from_env(
        cls,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        memory_factory: Callable[[dict], Any] | None = None,
    ) -> FirmMemory:
        """Build a facade from the environment and the current git checkout.

        ``memory_factory`` is injectable for tests; by default the real mem0 SDK
        is imported lazily so importing this package stays cheap.
        """
        settings = Settings.from_env(env)
        namespace = Namespace(
            repo=resolve_repo_slug(cwd, env=env),
            firm_owner=settings.firm_owner,
        )

        if memory_factory is None:

            def memory_factory(config: dict) -> Any:
                from mem0 import Memory  # imported lazily: keeps import cost off the hot path

                return Memory.from_config(config)

        try:
            memory = memory_factory(build_memory_config(settings))
        except Exception as exc:
            raise MemoryBackendError(f"Failed to initialise the mem0 backend: {exc}") from exc

        return cls(memory=memory, namespace=namespace, settings=settings)

    @property
    def namespace(self) -> Namespace:
        """The namespace this facade writes to and reads from."""
        return self._namespace

    def for_task(self, task: str) -> FirmMemory:
        """Return a facade scoped to *task*, leaving this one untouched."""
        return FirmMemory(
            memory=self._memory,
            namespace=self._namespace.with_task(task),
            settings=self._settings,
        )

    def remember(
        self,
        messages: str | Sequence[Mapping[str, str]],
        *,
        layer: Layer = Layer.REPO,
        task: str | None = None,
        memory_type: str | None = None,
        infer: bool = True,
        **metadata: str,
    ) -> dict:
        """Write a memory into *layer*.

        Pass ``infer=False`` for curated facts that should be stored verbatim
        (firm conventions), and leave it ``True`` for conversational capture.
        """
        if not messages or (isinstance(messages, str) and not messages.strip()):
            raise InvalidInputError("remember() requires non-empty content")

        namespace = self._namespace.with_task(task) if task else self._namespace
        payload_metadata = self._build_metadata(namespace, layer, memory_type, metadata)

        try:
            return self._memory.add(
                messages,
                **namespace.entity_kwargs(layer),
                metadata=payload_metadata,
                infer=infer,
            )
        except Exception as exc:
            raise MemoryBackendError(f"Failed to write memory to layer {layer.value!r}: {exc}") from exc

    def recall(
        self,
        query: str,
        *,
        layers: Sequence[Layer] = DEFAULT_RECALL_LAYERS,
        top_k: int | None = None,
        threshold: float | None = None,
        rerank: bool | None = None,
        metadata_filters: Mapping[str, str] | None = None,
    ) -> list[dict]:
        """Search across *layers* in one backend call and return ranked memories."""
        if not query or not query.strip():
            raise InvalidInputError("recall() requires a non-empty query")

        filters = layered_filters(self._namespace, layers, metadata_filters=metadata_filters)

        try:
            response = self._memory.search(
                query,
                filters=filters,
                top_k=top_k if top_k is not None else self._settings.default_top_k,
                threshold=threshold if threshold is not None else self._settings.default_threshold,
                rerank=rerank if rerank is not None else self._settings.reranker_enabled,
            )
        except Exception as exc:
            raise MemoryBackendError(f"Failed to search memory: {exc}") from exc

        return _normalise_results(response)

    def forget(self, memory_id: str) -> Any:
        """Delete a single memory by id."""
        if not memory_id or not memory_id.strip():
            raise InvalidInputError("forget() requires a memory id")
        try:
            return self._memory.delete(memory_id)
        except Exception as exc:
            raise MemoryBackendError(f"Failed to delete memory {memory_id!r}: {exc}") from exc

    def _build_metadata(
        self,
        namespace: Namespace,
        layer: Layer,
        memory_type: str | None,
        overrides: Mapping[str, str],
    ) -> dict[str, str]:
        """Stamp provenance so memories stay auditable and prunable later."""
        metadata: dict[str, str] = {
            **namespace.metadata,
            "source": DEFAULT_SOURCE,
            **overrides,
            # Scope-derived values are written last: callers may not forge them.
            "layer": layer.value,
        }
        if memory_type:
            metadata["type"] = memory_type
        if layer is not Layer.FIRM and namespace.repo:
            metadata["repo"] = namespace.repo
        return metadata


def _normalise_results(response: Any) -> list[dict]:
    """Flatten mem0's response shapes (``v1.1`` envelope or a bare list)."""
    if isinstance(response, Mapping):
        results = response.get("results", [])
        return list(results) if isinstance(results, Sequence) else []
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return list(response)
    return []
