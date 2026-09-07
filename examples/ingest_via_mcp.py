"""Ingest raw material through the MCP server, exactly as an agent would.

    python examples/ingest_via_mcp.py

This is the agent half of the round trip. It speaks real MCP over stdio to a
real ``firm-memory-mcp`` subprocess — no in-process shortcut — so what it proves
is the contract an OpenCode bot in GitLab CI actually meets:

1. the server offers the five designed tools;
2. ``memory_ingest`` accepts a raw merge request discussion and distils it;
3. nothing it produced is retrievable, because a person has not endorsed it yet.

Then a human opens the review UI and approves. Run ``examples/run_review_ui.py``
in another terminal for that half.

The material below is invented. It is written the way a real MR discussion is
written — decisions half-argued, a rejected approach, an aside nobody wrote
down — because material that reads like a summary makes any extractor look good.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from typing import Any

from _environment import configure, exported

from firm_memory.mcp.server import SERVER_NAME

SERVER_COMMAND = "firm-memory-mcp"

#: Scope the facts inherit. Named explicitly rather than left to the checkout,
#: because this discussion is about the trading repos, not about firm-memory.
REPOS = ["oms", "gateway"]
DOMAINS = ["execution"]
REFERENCE = "mr-4821"

RAW_MATERIAL = """\
Merge request !4821 — "Reject cash orders after the exchange cutoff"

priya (author):
This adds a hard time check in OrderValidator so cash strategies stop sending at
15:20. We had a batch of rejects on MCX last Thursday that all traced back to
orders leaving after the exchange had stopped accepting them. I put the check in
the OMS rather than in each strategy because I do not want six strategies each
carrying their own copy of the cutoff.

ashish (reviewer):
Agreed on placing it in the OMS. Two things.

First, the cutoff goes in config, not a constant. KRX is 15:30 and we will need
that before the quarter is out.

Second, and this is the important one: route the rejection through Risk Engine A.
All MCX order flow goes through Risk Engine A, never B. B has never had the MCX
contract specs loaded, and the last time someone routed MCX through it we spent a
day chasing phantom margin breaches. None of that is written down anywhere, which
is exactly the problem.

priya:
Done — cutoff moved to config, routed through Risk Engine A.

I also tried doing this in DropCopy instead, since it already sees every order.
That does not work: DropCopy sits downstream of the gateway, so by the time it
sees an order the exchange has already rejected it. Reverted that approach.

sanjay:
While you are in here — do not extend PositionManagerV1. It is deprecated.
UnifiedPositionService replaced it in Q1, and V1 is only still compiled because
two backtest fixtures import it. Anything new goes through UnifiedPositionService.

ashish:
Good catch. One more worth recording: CBE (Contract Based Execution) orders are
exempt from the 15:20 cutoff. They settle on a different schedule and the exchange
accepts them until 16:00. That exemption has caught us out twice.
"""


async def main() -> int:
    """Run the ingest round trip, returning a process exit code."""
    configure()

    if shutil.which(SERVER_COMMAND) is None:
        print(
            f"'{SERVER_COMMAND}' is not on PATH. Install the MCP extra and run through uv:\n"
            "  uv sync --extra mcp\n"
            "  uv run python examples/ingest_via_mcp.py",
            file=sys.stderr,
        )
        return 1

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=SERVER_COMMAND, args=[], env=exported())

    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        _report_surface(await session.list_tools())

        candidates = _report_ingest(
            await session.call_tool(
                "memory_ingest",
                {
                    "content": RAW_MATERIAL,
                    "kind": "merge request discussion",
                    "repos": REPOS,
                    "domains": DOMAINS,
                    "reference": REFERENCE,
                    "source": "merge-request",
                },
            )
        )

        _report_not_yet_retrievable(
            await session.call_tool(
                "memory_search",
                {"query": "when do cash strategies stop sending orders", "repos": REPOS},
            ),
            candidates,
        )

    _report_next_step(candidates)
    return 0


# --- reporting ---------------------------------------------------------------


def _report_surface(listing: Any) -> None:
    """Show what the server offers, so a surface change is visible here."""
    _heading(f"1. Connected to the '{SERVER_NAME}' MCP server over stdio")
    for tool in listing.tools:
        print(f"   {tool.name}")


def _report_ingest(result: Any) -> list[dict]:
    """Show what the extractor made of the material."""
    payload = _payload(result)

    _heading("2. Handed it one merge request discussion")
    if "error" in payload:
        print(f"   Extraction reported: {payload['error']}")
        return []

    candidates = payload.get("candidates", [])
    print(f"   {payload['count']} candidate(s) distilled, none of them stored:\n")
    for index, candidate in enumerate(candidates, start=1):
        memory = candidate["memory"]
        print(f"   {index}. [{memory['type']}] {memory['content']}")
        print(f"      confidence {memory['confidence']:.2f}   {candidate['candidate_id']}")
        print(f"      scope {_scope(memory['scope'])}\n")

    if not candidates:
        print("   Nothing durable in it. That is a normal outcome, not a failure.\n")
    return candidates


def _report_not_yet_retrievable(result: Any, candidates: list[dict]) -> None:
    """The point of the gate: extraction did not make anything searchable.

    The check is that none of the candidates *just extracted* comes back — not
    that the pool is empty. A pool with approved knowledge already in it is the
    normal case, and a test that only passes against an empty database would
    stop meaning anything the first time someone approved something.
    """
    payload = _payload(result)
    hits = payload.get("memories", [])
    proposed = {candidate["memory"]["content"] for candidate in candidates}
    leaked = [hit for hit in hits if hit["content"] in proposed]

    _heading("3. Searched for what it just extracted")
    if hits:
        print(f"   {len(hits)} result(s), every one of them approved before this run:")
        for hit in hits:
            print(f"     · {hit['content'][:88]}…")
    else:
        print("   0 results — nothing has been approved into this pool yet.")

    if leaked:
        print(f"\n   FAILED: {len(leaked)} candidate(s) are retrievable without approval.")
        for hit in leaked:
            print(f"     · {hit['content'][:88]}…")
    else:
        print(
            f"\n   None of the {len(candidates)} candidate(s) just extracted is among them.\n"
            "   Extraction produced candidates, not knowledge — nothing reaches the pool\n"
            "   until a person endorses it."
        )


def _report_next_step(candidates: list[dict]) -> None:
    """Hand over to the human half of the round trip."""
    _heading("4. Your turn")
    if candidates:
        print(f"   {len(candidates)} candidate(s) are waiting in the review queue.")
    print("   Open the review UI, approve the ones that are right, and search again.")


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _scope(scope: dict) -> str:
    """Render a scope the way the review UI does."""
    parts = ["firm"] if scope.get("firm") else []
    parts += [f"domain:{d}" for d in scope.get("domains", [])]
    parts += [f"repo:{r}" for r in scope.get("repos", [])]
    return " ".join(parts) or "—"


def _payload(result: Any) -> dict:
    """The tool's JSON, from whichever field this SDK version populated.

    Newer servers return ``structuredContent``; older ones only put the JSON in
    a text block. Reading both keeps the example working across versions rather
    than failing with an empty result that looks like an extraction problem.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"error": text}
    return {"error": "The tool returned nothing readable."}


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
