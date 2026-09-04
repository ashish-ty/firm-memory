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
import logging
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


#: What this script needs at runtime, and the package that provides each.
#: mem0 imports its LLM and embedder modules eagerly, so a missing one surfaces
#: deep inside a factory rather than at startup — hence checking up front.
REQUIREMENTS = (
    ("mem0", "mem0ai", "the memory provider"),
    ("psycopg", "psycopg[binary,pool]", "the pgvector store"),
    ("litellm", "litellm", "the OpenRouter LLM"),
    ("fastembed", "fastembed", "local embeddings"),
)


def check_dependencies() -> None:
    """Report every missing package at once, with the command that fixes it.

    Reporting them one at a time means four failed runs, each with a traceback
    from somewhere inside mem0's factories.
    """
    from importlib.util import find_spec

    missing = [(pkg, why) for module, pkg, why in REQUIREMENTS if find_spec(module) is None]
    if not missing:
        return

    lines = [f"Missing {len(missing)} dependency/dependencies for this script:\n"]
    lines += [f"  - {pkg:<24} ({why})" for pkg, why in missing]
    lines.append("\nInstall them all with:\n")
    lines.append("  pip install -e '.[demo]'\n")
    lines.append(f"Running as: {sys.executable}")
    lines.append("If that is not the interpreter you expected, activate your venv")
    lines.append("(or repoint your editor's interpreter) and run again.")
    sys.exit("\n".join(lines))


def load_env_file() -> None:
    """Load ``.env`` from the repo root, if there is one.

    So credentials can live in a gitignored file instead of being typed on every
    run — and, importantly, instead of being hardcoded into this script, which
    is committed to a public repository. Real values already in the environment
    win, so a shell export still overrides the file.
    """
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def configure() -> None:
    """Point mem0 at OpenRouter for the LLM and a local model for embeddings.

    Only defaults, so anything already set in your environment still wins.
    OpenRouter has no embeddings endpoint, so embeddings run locally — which
    also keeps the memory content off the network.
    """
    load_env_file()

    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit(
            "OPENROUTER_API_KEY is not set. Either export it:\n"
            "  export OPENROUTER_API_KEY='sk-or-...'\n"
            "or put it in a .env file at the repo root (already gitignored):\n"
            "  OPENROUTER_API_KEY=sk-or-...\n\n"
            "Do not hardcode it in this file — this repository is public."
        )
    if not os.environ.get("FIRM_MEM0_PG_DSN"):
        sys.exit(
            "FIRM_MEM0_PG_DSN is not set. Either export it:\n"
            "  export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'\n"
            "or add it to your .env file."
        )

    os.environ.setdefault("FIRM_MEM0_LLM_PROVIDER", "litellm")
    os.environ.setdefault("FIRM_MEM0_LLM_MODEL", "openrouter/anthropic/claude-3.5-sonnet")
    # fastembed runs the model over ONNX — no torch, and nothing leaves the network.
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_PROVIDER", "fastembed")
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5")
    # Must match the model: pgvector fixes the column width at creation.
    os.environ.setdefault("FIRM_MEM0_EMBEDDING_DIMS", "384")
    # Cross-encoder reranking is a retrieval-quality feature and pulls in torch
    # via sentence-transformers, which this script does not need to demonstrate
    # extraction and approval. Turn it on for real retrieval work:
    #   pip install -e '.[rerank]' && export FIRM_MEM0_RERANK=on
    os.environ.setdefault("FIRM_MEM0_RERANK", "off")


def quieten_dependencies() -> None:
    """Turn down libraries that narrate their retries.

    psycopg's pool logs every failed connection attempt, twice per host, so an
    unreachable database buries the actual message. The check below reports it
    once, clearly.
    """
    for name in ("psycopg.pool", "httpx", "LiteLLM"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def check_database(provider: Mem0Provider, scope: MemoryScope) -> None:
    """Fail fast, and legibly, when the store is unreachable.

    Without this the run continues: deduplication context comes back empty, the
    write fails only at approval, and the operator sees a wall of pool retries
    with no summary.
    """
    try:
        provider.search("connectivity check", scope, 1)
    except Exception as exc:
        sys.exit(
            f"Could not reach the vector store.\n\n  {type(exc).__name__}: {str(exc).splitlines()[0]}\n\n"
            f"FIRM_MEM0_PG_DSN is {os.environ['FIRM_MEM0_PG_DSN']!r}.\n"
            "Check the database is running and that pgvector is installed in it."
        )


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

    check_dependencies()
    configure()
    comment = read_comment(args)

    quieten_dependencies()

    settings = Settings.from_env(os.environ)
    provider = Mem0Provider.from_settings(settings)
    scope = MemoryScope(domains=(args.domain,), repos=(args.repo,))
    check_database(provider, scope)

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
            print("\nNo durable facts in this comment. That is a normal outcome —")
            print("most review comments contain nothing that stays true after the task.")
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
