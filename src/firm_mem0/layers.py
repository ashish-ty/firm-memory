"""Filter construction for layered retrieval.

A useful recall almost always spans layers: the repo's conventions and the
firm's standards. All layers are unioned into one filter tree and served by a
single ``search()`` call rather than N round trips.

Two OSS constraints shape the output here, both verified against
``Memory._process_metadata_filters`` and ``pgvector._build_filter_conditions``:

1. **``OR`` branches must be flat dicts.** Top-level ``AND`` is flattened by the
   preprocessor, but an ``AND`` *nested inside* ``OR`` is passed through
   verbatim, and pgvector then compiles the literal key into
   ``payload->>'AND' = ANY(...)``, which matches nothing. Keys within a branch
   are implicitly ANDed, so the flat form is both correct and simpler.
2. **Metadata is filtered by flat top-level keys.** ``Memory._create_memory``
   flattens the caller's metadata straight into the payload. The Platform's
   nested ``{"metadata": {"type": ...}}`` form raises
   ``Unsupported metadata filter operator: type`` on OSS.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .errors import NamespaceError
from .namespace import Layer, Namespace

#: Scoping axes a caller may never override through metadata filters.
RESERVED_FILTER_KEYS = frozenset({"user_id", "agent_id", "run_id"})


def layer_filter(
    namespace: Namespace,
    layer: Layer,
    *,
    metadata_filters: Mapping[str, str] | None = None,
) -> dict:
    """Build the flat filter selecting *layer* for *namespace*.

    Keys are ANDed by the vector store. Metadata keys are flat because OSS
    flattens metadata into the payload on write.
    """
    conditions: dict = dict(namespace.entity_kwargs(layer))

    for key, value in (metadata_filters or {}).items():
        if key in RESERVED_FILTER_KEYS:
            raise NamespaceError(
                f"Metadata filter key {key!r} would override layer scoping; use the namespace/layer arguments instead"
            )
        conditions[key] = value

    return conditions


def layered_filters(
    namespace: Namespace,
    layers: Iterable[Layer],
    *,
    metadata_filters: Mapping[str, str] | None = None,
) -> dict:
    """Build one filter tree covering every layer in *layers*.

    Duplicate layers are collapsed while preserving first-seen order. A single
    layer returns its bare flat filter — wrapping one branch in ``OR`` only
    costs the vector store work.
    """
    ordered_unique: list[Layer] = list(dict.fromkeys(layers))
    if not ordered_unique:
        raise ValueError("At least one layer is required for retrieval")

    branches = [layer_filter(namespace, layer, metadata_filters=metadata_filters) for layer in ordered_unique]

    if len(branches) == 1:
        return branches[0]
    return {"OR": branches}
