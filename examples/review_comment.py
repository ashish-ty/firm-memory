#!/usr/bin/env python
"""Extract memory facts from one PR review comment, then approve them.

    review comment ─► mem0 extracts facts ─► you approve ─► searchable

Everything is wired: OpenRouter for the LLM, a local embedder (OpenRouter has no
embeddings endpoint), pgvector for storage, and mem0's own extractor running
read-only so nothing is written until you say so.

Setup — two variables:

    export OPENROUTER_API_KEY='sk-or-...'
    export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'

Run:

    python examples/review_comment.py                      # built-in sample comment
    python examples/review_comment.py --comment "..."       # your own
    pbpaste | python examples/review_comment.py             # from stdin
"""

from __future__ import annotations

import argparse
import os
import select
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firm_memory import FirmMemory, MemoryScope, Provenance, Settings, SourceDocument
from firm_memory.providers.mem0 import Mem0FactExtractor, Mem0Provider

SAMPLE_COMMENT = """
This needs to move into OrderRouter, not the strategy. Every venue path goes
through OrderRouter and DropCopy reconciles against what it emitted, so if a
strategy suppresses the order itself we get a gap between what we think we sent
and what the exchange saw.

Also — we already tried queueing after-cut-off orders for the next session, back
in March. It caused duplicate fills and we reverted it the same week. MCX rejects
anything sent after 15:20 for cash strategies, so queueing only moves the
rejection later.
"""


def configure() -> None:
    """Point mem0 at OpenRouter for the LLM and a local model for embeddings.

    Only defaults, so anything already set in your environment still wins.
    OpenRouter has no embeddings endpoint, so embeddings run locally — which
    also keeps the memory content off the network.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("Set OPENROUTER_API_KEY first:\n  export OPENROUTER_API_KEY='sk-or-...'")
    if not os.environ.get("FIRM_MEM0_PG_DSN"):
        sys.exit(
            "Set FIRM_MEM0_PG_DSN first:\n"
            "  export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'"
        )

    os.environ.setdefault("FIRM_MEM0_LLM_PROVIDER", "litellm")
    os.environ.setdefault("FIRM_MEM0_LLM_MODEL", "openrouter/anthropic/claude-3.5-sonnet")
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_PROVIDER", "huggingface")
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5")
    # Must match the model: pgvector fixes the column width at creation.
    os.environ.setdefault("FIRM_MEM0_EMBEDDING_DIMS", "384")


def read_comment(args: argparse.Namespace) -> str:
    """The review comment, from --comment, piped stdin, or the built-in sample.

    stdin is checked without blocking. A plain ``isatty()`` test is not enough:
    when stdin is an open pipe that never receives anything — a CI step, some
    terminals — ``read()`` waits for a writer that never comes and the script
    hangs with no output.
    """
    if args.comment:
        return args.comment

    if not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.0)[0]:
        piped = sys.stdin.read().strip()
        if piped:
            return piped

    print("(no comment given — using the built-in sample)\n")
    return SAMPLE_COMMENT


def choose(count: int, approve_all: bool) -> set[int]:
    """Ask which candidates to approve."""
    if approve_all:
        return set(range(count))

    answer = input(f"\nApprove which? [all / none / e.g. 1,3] (1-{count}): ").strip().lower()
    if answer in ("all", "a"):
        return set(range(count))
    if answer in ("", "none", "n"):
        return set()

    chosen = set()
    for part in answer.replace(" ", "").split(","):
        if part.isdigit() and 1 <= int(part) <= count:
            chosen.add(int(part) - 1)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract memory facts from a PR review comment.")
    parser.add_argument("--comment", help="the review comment text")
    parser.add_argument("--repo", default="oms", help="repo the comment is about (default: oms)")
    parser.add_argument("--domain", default="execution", help="domain it belongs to (default: execution)")
    parser.add_argument("--reference", default="mr-4821", help="the MR/PR this came from")
    parser.add_argument("--yes", action="store_true", help="approve everything without asking")
    args = parser.parse_args()

    configure()
    comment = read_comment(args)

    settings = Settings.from_env(os.environ)
    provider = Mem0Provider.from_settings(settings)
    scope = MemoryScope(domains=(args.domain,), repos=(args.repo,))

    with FirmMemory(
        provider,
        settings=settings,
        scope=MemoryScope.for_query(args.repo, domains=(args.domain,)),
        extractor=Mem0FactExtractor(provider),
    ) as memory:
        print("Extracting…")
        candidates = memory.ingest(
            SourceDocument(
                content=comment,
                scope=scope,
                provenance=Provenance(source="review-comment", reference=args.reference),
                kind="pull request review comment",
            )
        )

        if not candidates:
            print("\nNo durable facts in this comment. That is a normal outcome.")
            return

        print(f"\n{len(candidates)} candidate fact(s) — none stored yet:\n")
        for index, candidate in enumerate(candidates, start=1):
            memo = candidate.memory
            print(f"  {index}. [{memo.type.value}]  confidence {memo.confidence:.2f}")
            print(f"     {memo.content}\n")

        approved_indices = choose(len(candidates), args.yes)
        if not approved_indices:
            print("\nNothing approved. The pool is unchanged.")
            return

        print()
        for index in sorted(approved_indices):
            stored = memory.approvals.approve(candidates[index].id, approver=os.environ.get("USER", "reviewer"))
            print(f"  stored {stored.id}  [{stored.type.value}]")

        print("\nNow retrievable:")
        for hit in memory.search("why do we stop sending orders after the cut-off"):
            print(f"  {hit.content}")
            print(f"    cite {hit.id} — from {hit.provenance.reference}\n")


if __name__ == "__main__":
    main()
