#!/usr/bin/env python
"""Run the ingestion pipeline end to end and watch what it does.

    raw material ─► extract ─► candidates ─► human approval ─► searchable

Three modes, in increasing order of what they need:

    --mode offline    no credentials at all. A canned extractor, so you can see
                      the flow and the approval gate before spending a token.

    --mode llm        needs an LLM key only. Runs the platform's own extractor
                      against a real model; memories are held in process.

    --mode provider   needs an LLM key and pgvector. Runs mem0's own extractor
                      (its read-only phases) against your real pool, so its
                      deduplication has something to deduplicate against.

    --mode compare    runs the llm and provider extractors over the same
                      sources and prints what each returned.

Examples::

    python examples/try_extraction.py --mode offline

    export OPENROUTER_API_KEY=sk-or-...
    python examples/try_extraction.py --mode llm \\
        --model openrouter/anthropic/claude-3.5-sonnet

    export FIRM_MEM0_PG_DSN=postgresql://mem0:pw@localhost:5432/mem0
    python examples/try_extraction.py --mode compare \\
        --model openrouter/anthropic/claude-3.5-sonnet

Nothing here writes to the pool without an explicit approval step, and
``--approve`` is off by default.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sample_sources import sample_documents

from firm_memory import (
    FirmMemory,
    MemoryScope,
    Settings,
    SourceDocument,
)
from firm_memory.ingestion.extraction import LLMFactExtractor
from firm_memory.ingestion.llm import LiteLLMClient
from firm_memory.providers.inmemory import InMemoryProvider

QUERIES = (
    "why does the OMS stop sending after the cut-off",
    "what caused the analytics 504s under bulk import",
    "have we tried queueing orders for the next session",
)


# --- offline: a canned extractor, so the flow runs with no credentials -------


class CannedClient:
    """Returns a fixed extraction, so --mode offline needs no model."""

    # Keyed on distinctive content, not on a reference number: "4821" appears
    # in both the merge request and the incident, and matching on it attributed
    # one document's facts to the other.
    RESPONSES: ClassVar[dict[str, list[dict]]] = {
        "15:20": [
            {
                "content": (
                    "Cash-strategy orders are dropped after 15:20 because MCX rejects them, "
                    "so queueing would only move the rejection later."
                ),
                "type": "business_rules",
                "confidence": 0.92,
            },
            {
                "content": (
                    "Queueing after-cut-off orders for the next session was tried and reverted: "
                    "it produced duplicate fills."
                ),
                "type": "anti_patterns",
                "confidence": 0.88,
            },
            {
                "content": (
                    "The cut-off check belongs in OrderRouter rather than a strategy, because "
                    "DropCopy reconciles against what OrderRouter emitted."
                ),
                "type": "architecture_decisions",
                "confidence": 0.85,
            },
        ],
        "cache stampede": [
            {
                "content": (
                    "Incident 4821: AnalyticsAPI returned 504s during bulk position import; "
                    "root cause was a cache stampede in CacheManager, fixed with single-flight coalescing."
                ),
                "type": "production_issues",
                "confidence": 0.94,
            },
            {
                "content": (
                    "Raising the connection pool size was rejected as a fix for the cache stampede: "
                    "it would hide the stampede and starve the Risk Engine, which shares the pool."
                ),
                "type": "anti_patterns",
                "confidence": 0.8,
            },
        ],
    }

    def complete_json(self, system: str, user: str) -> dict:
        for marker, facts in self.RESPONSES.items():
            if marker in user:
                return {"facts": facts}
        # The routine and noisy threads hold nothing durable. That is the
        # correct outcome, not a failure.
        return {"facts": []}


# --- wiring ------------------------------------------------------------------


def build(mode: str, model: str | None, args: argparse.Namespace) -> FirmMemory:
    """Build a FirmMemory for *mode*, with the matching provider and extractor."""
    settings = Settings(min_score=args.min_score, default_limit=args.limit, env=os.environ)
    scope = MemoryScope.for_query("oms", domains=("execution",))

    if mode == "offline":
        return FirmMemory(
            InMemoryProvider(),
            settings=settings,
            scope=scope,
            extractor=LLMFactExtractor(CannedClient()),
        )

    if mode == "llm":
        _require_model(model)
        return FirmMemory(
            _provider(args),
            settings=settings,
            scope=scope,
            extractor=LLMFactExtractor(
                LiteLLMClient(model, api_key=args.api_key, api_base=args.api_base)
            ),
        )

    if mode == "provider":
        from firm_memory.providers.mem0.extraction import Mem0FactExtractor

        provider = _mem0_provider()
        return FirmMemory(provider, settings=settings, scope=scope, extractor=Mem0FactExtractor(provider))

    raise SystemExit(f"Unknown mode {mode!r}")


def _provider(args: argparse.Namespace):
    """pgvector when a DSN is configured, in-process otherwise."""
    if os.environ.get("FIRM_MEM0_PG_DSN") and not args.in_memory:
        return _mem0_provider()
    return InMemoryProvider()


def _mem0_provider():
    """The real mem0 provider, which needs FIRM_MEM0_PG_DSN."""
    from firm_memory.providers.mem0 import Mem0Provider

    if not os.environ.get("FIRM_MEM0_PG_DSN"):
        raise SystemExit(
            "This mode needs a vector store. Set FIRM_MEM0_PG_DSN, for example:\n"
            "  export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'"
        )
    return Mem0Provider.from_settings(Settings.from_env(os.environ))


def _require_model(model: str | None) -> None:
    if not model:
        raise SystemExit("This mode needs --model, e.g. --model openrouter/anthropic/claude-3.5-sonnet")


# --- the run ------------------------------------------------------------------


def ingest_and_report(memory: FirmMemory, documents: list[SourceDocument], label: str) -> list:
    """Ingest, then show what was proposed and prove the pool is untouched."""
    print(f"\n{'=' * 72}\n  INGEST — {label}\n{'=' * 72}")
    print(f"Sources: {len(documents)}")

    candidates = memory.ingest(*documents)

    print(f"\nExtracted {len(candidates)} candidate(s). None of them is in the pool yet.\n")
    for candidate in candidates:
        memo = candidate.memory
        print(f"  [{memo.type.value:<24}] conf={memo.confidence:.2f}  {candidate.id}")
        print(f"      {memo.content}")
        print(f"      scope={','.join(memo.scope.atoms)}  from={memo.provenance.reference}\n")

    print("Proof the gate holds — searching before any approval:")
    for query in QUERIES:
        hits = memory.search(query)
        print(f"  {len(hits)} hit(s)  <- {query!r}")
    return candidates


def approve_and_report(memory: FirmMemory, candidates: list, approver: str) -> None:
    """Approve everything queued, then search again."""
    print(f"\n{'-' * 72}\n  APPROVAL — as {approver}\n{'-' * 72}")
    for candidate in candidates:
        approved = memory.approvals.approve(candidate.id, approver=approver)
        print(f"  approved {approved.id}  [{approved.type.value}]")

    print("\nSearching again, now that a human has approved:")
    for query in QUERIES:
        hits = memory.search(query)
        print(f"\n  {query!r}")
        for hit in hits:
            score = f"{hit.score:.3f}" if hit.score is not None else "n/a"
            print(f"    score={score}  [{hit.type.value}]  {hit.content[:88]}")
            print(f"      cite: {hit.id}  from {hit.provenance.reference}")
        if not hits:
            print("    (nothing above the relevance floor)")


def compare(args: argparse.Namespace, documents: list[SourceDocument]) -> None:
    """Run both extractors over the same sources and show the difference."""
    for mode, label in (("llm", "platform extractor"), ("provider", "mem0's extractor")):
        try:
            with build(mode, args.model, args) as memory:
                ingest_and_report(memory, documents, label)
        except SystemExit as exc:
            print(f"\n[skipped {label}] {exc}")
        except Exception as exc:
            print(f"\n[failed {label}] {type(exc).__name__}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="offline", choices=("offline", "llm", "provider", "compare"))
    parser.add_argument("--model", default=os.environ.get("FIRM_MEMORY_EXTRACTION_MODEL"))
    parser.add_argument("--api-key", default=os.environ.get("FIRM_MEMORY_EXTRACTION_API_KEY"))
    parser.add_argument("--api-base", default=os.environ.get("FIRM_MEMORY_EXTRACTION_API_BASE"))
    parser.add_argument("--approve", action="store_true", help="approve every candidate and search again")
    parser.add_argument("--approver", default="script-operator")
    parser.add_argument("--in-memory", action="store_true", help="never touch pgvector, even if a DSN is set")
    parser.add_argument("--min-score", type=float, default=0.0, help="relevance floor (default 0 to show all)")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    documents = sample_documents()

    if args.mode == "compare":
        compare(args, documents)
        return

    with build(args.mode, args.model, args) as memory:
        candidates = ingest_and_report(memory, documents, args.mode)
        if args.approve and candidates:
            approve_and_report(memory, candidates, args.approver)
        elif candidates:
            print("\nRe-run with --approve to approve these and see them become searchable.")


if __name__ == "__main__":
    main()
