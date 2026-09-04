"""Distilling raw material into candidate memories.

This is the only way content enters the platform. There is deliberately no path
that takes a blob of text and stores it: raw material becomes *candidates*,
candidates go to a human, and only approval reaches the provider.

    raw material ─► extract ─► candidates ─► human approval ─► provider.insert()

The extractor's job is judgement, not summarising. It reads an MR discussion, an
incident write-up or an interview transcript and keeps only what stays true
after the task ends — the reason a choice was made, the rule the code cannot
explain, the approach already rejected. The taxonomy and its exclusions come
straight from :mod:`firm_memory.taxonomy`, so extraction and retrieval share one
vocabulary.

Everything it produces is ``PROPOSED`` with the provenance of its source. A fact
that cannot be attributed to a type is discarded rather than guessed at, because
a mistyped memory is unretrievable and a wrong one is worse than none.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..errors import ExtractionError, InvalidInputError, TaxonomyError
from ..models import Memory, MemoryStatus, MemoryTier
from ..provenance import Provenance, Source
from ..scope import MemoryScope
from ..taxonomy import CODING_CATEGORIES, EXCLUSIONS, coerce_type
from .llm import CompletionClient

logger = logging.getLogger(__name__)

#: A candidate the model is less sure of than this is not worth a reviewer's
#: attention; it will be re-proposed if the same conclusion recurs.
DEFAULT_MIN_CONFIDENCE = 0.35

#: Raw material longer than this is truncated. A single source that needs more
#: than this is a document, and documents should be ingested as the decision
#: plus an ``evidence`` pointer, not copied into the pool.
MAX_SOURCE_CHARS = 24_000


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Raw material to distil, with the scope and origin its facts inherit."""

    content: str
    scope: MemoryScope
    provenance: Provenance = field(default_factory=lambda: Provenance(source=Source.DOCUMENT))
    #: What kind of material this is ("merge request discussion", "incident
    #: review"). It steers the model far more than any prompt wording.
    kind: str = "engineering discussion"
    tier: MemoryTier = MemoryTier.DURABLE
    task: str | None = None

    def __post_init__(self) -> None:
        content = (self.content or "").strip()
        if not content:
            raise InvalidInputError("A source document requires non-empty content")
        object.__setattr__(self, "content", content[:MAX_SOURCE_CHARS])


@runtime_checkable
class FactExtractor(Protocol):
    """Turns raw material into candidate memories."""

    def extract(self, document: SourceDocument) -> list[Memory]:
        """Return the candidates found in *document*, all ``PROPOSED``."""
        ...


class LLMFactExtractor:
    """Extracts candidates with a completion model, against the firm taxonomy."""

    def __init__(
        self,
        client: CompletionClient,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_facts: int = 10,
    ) -> None:
        self._client = client
        self._min_confidence = min_confidence
        self._max_facts = max_facts

    def extract(self, document: SourceDocument) -> list[Memory]:
        """Distil *document* into candidate memories."""
        response = self._client.complete_json(build_system_prompt(), build_user_prompt(document))

        facts = response.get("facts")
        if not isinstance(facts, list):
            raise ExtractionError("The extraction response has no 'facts' list")

        candidates: list[Memory] = []
        for entry in facts[: self._max_facts]:
            candidate = self._to_memory(entry, document)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _to_memory(self, entry: object, document: SourceDocument) -> Memory | None:
        """Convert one extracted fact, dropping anything unusable.

        A malformed or low-confidence entry is skipped rather than raised on:
        one bad fact must not cost the caller the other nine.
        """
        if not isinstance(entry, dict):
            return None

        content = str(entry.get("content") or "").strip()
        if not content:
            return None

        confidence = _confidence(entry.get("confidence"))
        if confidence < self._min_confidence:
            logger.debug("Dropping low-confidence candidate (%.2f): %s", confidence, content[:80])
            return None

        try:
            memory_type = coerce_type(str(entry.get("type") or ""))
        except TaxonomyError:
            # The taxonomy is closed: a fact that fits no type is discarded.
            logger.debug("Dropping candidate with unknown type %r: %s", entry.get("type"), content[:80])
            return None

        return Memory(
            content=content,
            type=memory_type,
            scope=document.scope,
            provenance=document.provenance,
            confidence=confidence,
            status=MemoryStatus.PROPOSED,
            tier=document.tier,
            task=document.task,
        )


def build_system_prompt() -> str:
    """The extraction instructions, derived from the firm's taxonomy."""
    categories = "\n".join(f"- {c.name}: {c.description}" for c in CODING_CATEGORIES)
    exclusions = "\n".join(f"- {rule}" for rule in EXCLUSIONS)
    return (
        "You extract durable engineering memory for a software firm. Retain only facts that "
        "remain useful weeks later, on a different task, in the same codebase.\n\n"
        f"Classify each retained fact into exactly one of these categories:\n{categories}\n\n"
        f"Exclusions:\n{exclusions}\n\n"
        "Write each fact as one specific, self-contained sentence that states the reason a "
        "choice was made. It must make sense to someone who has not read the source.\n"
        "Discard any fact that fits no category. Returning an empty list is correct and "
        "expected when the source contains no durable knowledge.\n\n"
        'Respond with JSON: {"facts": [{"content": str, "type": str, "confidence": float}]} '
        "where confidence in 0.0-1.0 is how certain you are that the fact is true and durable."
    )


def build_user_prompt(document: SourceDocument) -> str:
    """The source material, labelled with what it is and what it covers."""
    reach = ", ".join(document.scope.atoms)
    return (
        f"Source type: {document.kind}\n"
        f"These facts will be scoped to: {reach}\n\n"
        f"---\n{document.content}\n---\n\n"
        "Extract the durable engineering knowledge from the source above."
    )


def _confidence(raw: object) -> float:
    """Clamp the model's confidence into a probability, defaulting mid-scale."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(max(value, 0.0), 1.0)


def extract_all(
    extractor: FactExtractor,
    documents: Sequence[SourceDocument],
) -> list[Memory]:
    """Extract from several documents, skipping the ones that fail.

    A batch ingest of fifty MRs must not be lost because one of them upset the
    model.
    """
    candidates: list[Memory] = []
    for document in documents:
        try:
            candidates.extend(extractor.extract(document))
        except ExtractionError:
            logger.warning("Extraction failed for a %s; continuing", document.kind, exc_info=True)
    return candidates
