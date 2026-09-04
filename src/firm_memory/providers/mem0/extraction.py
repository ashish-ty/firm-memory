"""Fact extraction using mem0's own extractor, without mem0's write.

mem0's real value on the ingestion side is its extractor: one LLM call that
reads a conversation *together with the memories already in the pool* and
returns only what is genuinely new. Reproducing that dedup-aware behaviour by
hand is the hard part, and this module does not try to.

What it does instead is split ``add()`` in half. ``Memory._add_to_vector_store``
runs seven phases; the first three are read-only —

    0. gather session context
    1. retrieve existing memories relevant to the input
    2. one LLM call: extract new facts, deduplicated against phase 1

— and phases 3 onward embed and persist. This module performs 0-2 with mem0's
own prompt and mem0's configured LLM, and stops. Nothing is embedded, nothing is
written, and the pool is untouched until a human approves.

**Two things mem0's extractor does not give us**, both handled here:

*A type.* Its output schema is ``{"memory": [{"text": ...}]}`` with no category,
and the schema is anchored by a dozen few-shot examples, so custom instructions
cannot reliably add a field. Classification is therefore a second, cheap call
against the firm taxonomy.

*Engineering framing.* ``ADDITIVE_EXTRACTION_PROMPT`` is written for a consumer
assistant — its examples are "User has a dog named Max" and "User was promoted
at Shopify". The firm's ``custom_instructions`` are injected and steer it, but
the examples still pull toward personal-profile facts. Anything that comes back
in that shape is caught by the taxonomy: a fact about a person fits no category
and is dropped. Compare this extractor against
:class:`~firm_memory.ingestion.extraction.LLMFactExtractor`, whose prompt is
built for engineering memory from the start, before committing to either.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...errors import ExtractionError, TaxonomyError
from ...ingestion.extraction import DEFAULT_MIN_CONFIDENCE, SourceDocument
from ...models import Memory, MemoryStatus
from ...taxonomy import CODING_CATEGORIES, MemoryType, coerce_type

logger = logging.getLogger(__name__)

#: How many existing memories to show the extractor as deduplication context.
#: mem0 uses 10 in its own pipeline.
DEFAULT_CONTEXT_LIMIT = 10


class Mem0FactExtractor:
    """Extracts candidates with mem0's extractor, then types them with ours.

    Satisfies :class:`~firm_memory.ingestion.extraction.FactExtractor`, so it
    drops into ``FirmMemory(extractor=...)`` in place of the platform's own.
    """

    def __init__(
        self,
        provider: Any,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_facts: int = 10,
        context_limit: int = DEFAULT_CONTEXT_LIMIT,
    ) -> None:
        self._provider = provider
        self._min_confidence = min_confidence
        self._max_facts = max_facts
        self._context_limit = context_limit

    def extract(self, document: SourceDocument) -> list[Memory]:
        """Distil *document* into candidates. Writes nothing."""
        texts = self._extract_texts(document)
        if not texts:
            return []

        typed = self._classify(texts[: self._max_facts])
        return [
            candidate
            for text, memory_type, confidence in typed
            if (candidate := self._to_memory(text, memory_type, confidence, document)) is not None
        ]

    # --- mem0's extractor, read-only ----------------------------------------

    def _extract_texts(self, document: SourceDocument) -> list[str]:
        """Run mem0's extraction phases and return the new facts it found."""
        prompts = _import_prompts()
        backend = self._provider.backend

        existing = self._provider.extraction_context(
            document.scope, document.content, limit=self._context_limit
        )
        # mem0 maps real ids onto sequential integers before showing them to the
        # model; it is an anti-hallucination measure, and we keep it.
        existing_memories = [{"id": str(index), "text": text} for index, (_, text) in enumerate(existing)]

        user_prompt = prompts.generate_additive_extraction_prompt(
            existing_memories=existing_memories,
            new_messages=[{"role": "user", "content": document.content}],
            custom_instructions=self._provider.extraction_instructions,
        )

        try:
            response = backend.llm.generate_response(
                messages=[
                    {"role": "system", "content": prompts.ADDITIVE_EXTRACTION_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise ExtractionError(f"mem0 fact extraction failed: {exc}") from exc

        return _parse_texts(response)

    # --- typing against the firm taxonomy ------------------------------------

    def _classify(self, texts: list[str]) -> list[tuple[str, str, float]]:
        """Assign a taxonomy type and a confidence to each extracted fact.

        One batched call rather than one per fact: extraction already costs a
        round trip, and a per-fact classifier would multiply that by ten.
        """
        catalogue = "\n".join(f"- {c.name}: {c.description}" for c in CODING_CATEGORIES)
        numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(texts))

        system = (
            "You classify engineering facts into a fixed taxonomy. Assign exactly one "
            f"category to each numbered fact:\n{catalogue}\n\n"
            "A fact that fits no category — anything about an individual person's "
            "preferences, transient state, or raw code — must be given the category "
            '"none" so it can be discarded.\n\n'
            'Respond with JSON: {"classified": [{"index": int, "type": str, "confidence": float}]} '
            "where confidence in 0.0-1.0 is how certain you are the fact is true and durable."
        )

        try:
            response = self._provider.backend.llm.generate_response(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": numbered},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise ExtractionError(f"Classifying extracted facts failed: {exc}") from exc

        return _pair_with_texts(texts, _parse_classifications(response))

    def _to_memory(
        self,
        text: str,
        raw_type: str,
        confidence: float,
        document: SourceDocument,
    ) -> Memory | None:
        """Build one candidate, dropping anything the taxonomy will not accept."""
        if confidence < self._min_confidence:
            logger.debug("Dropping low-confidence candidate (%.2f): %s", confidence, text[:80])
            return None

        try:
            memory_type: MemoryType = coerce_type(raw_type)
        except TaxonomyError:
            # Includes the deliberate "none" escape hatch: mem0's extractor is
            # tuned for personal facts, and this is where those are caught.
            logger.debug("Dropping candidate typed %r: %s", raw_type, text[:80])
            return None

        return Memory(
            content=text,
            type=memory_type,
            scope=document.scope,
            provenance=document.provenance,
            confidence=confidence,
            status=MemoryStatus.PROPOSED,
            tier=document.tier,
            task=document.task,
        )


def _parse_texts(response: Any) -> list[str]:
    """Pull the fact texts out of mem0's ``{"memory": [...]}`` response."""
    payload = _load(response, "extraction")
    entries = payload.get("memory")
    if not isinstance(entries, list):
        raise ExtractionError("mem0's extraction response has no 'memory' list")

    return [
        text
        for entry in entries
        if isinstance(entry, dict) and (text := str(entry.get("text") or "").strip())
    ]


def _parse_classifications(response: Any) -> dict[int, tuple[str, float]]:
    """Pull ``index -> (type, confidence)`` out of the classifier response."""
    payload = _load(response, "classification")
    entries = payload.get("classified")
    if not isinstance(entries, list):
        raise ExtractionError("The classification response has no 'classified' list")

    classified: dict[int, tuple[str, float]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry["index"])
        except (KeyError, TypeError, ValueError):
            continue
        classified[index] = (str(entry.get("type") or ""), _confidence(entry.get("confidence")))
    return classified


def _pair_with_texts(
    texts: list[str],
    classified: dict[int, tuple[str, float]],
) -> list[tuple[str, str, float]]:
    """Join texts to their classification, dropping any the classifier skipped."""
    paired: list[tuple[str, str, float]] = []
    for index, text in enumerate(texts):
        if index not in classified:
            logger.debug("Classifier returned nothing for fact %d; dropping it", index)
            continue
        raw_type, confidence = classified[index]
        paired.append((text, raw_type, confidence))
    return paired


def _load(response: Any, stage: str) -> dict:
    """Parse an LLM response into a JSON object, tolerating a code fence."""
    text = str(response or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    if not text:
        raise ExtractionError(f"The {stage} model returned an empty response")

    try:
        parsed = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"The {stage} model returned unparseable JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ExtractionError(f"Expected a JSON object from the {stage} model, got {type(parsed).__name__}")
    return parsed


def _confidence(raw: object) -> float:
    """Clamp a model-supplied confidence into a probability."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(max(value, 0.0), 1.0)


def _import_prompts() -> Any:
    """Import mem0's prompt module lazily, with an actionable error."""
    try:
        from mem0.configs import prompts
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ExtractionError(
            "Extracting with mem0 requires the mem0ai package. Install it with: pip install 'firm-memory[mem0]'"
        ) from exc
    return prompts
