"""The configuration the end-to-end example processes share.

Three processes take part in one run — the script that ingests over MCP, the
MCP server it spawns, and the long-lived review UI — and they only compose if
all three agree on two things: which memory pool they are writing to, and where
the candidate queue lives. Keeping that agreement in one module is what stops a
run "succeeding" against a queue nobody is looking at.

Everything here is a *default*. Anything already set in the environment wins, so
pointing the example at a different database or model needs no edit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

#: The queue must be reachable from more than one process, so the example uses
#: Postgres rather than the in-process default — the same reason the real
#: deployment does. It reuses the pgvector database; the table is separate.
CANDIDATES_FROM_POOL_DSN = True


def load_dotenv() -> None:
    """Read ``.env`` into the environment without overriding what is already set."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configure() -> None:
    """Apply the example's defaults, failing early on a missing credential."""
    load_dotenv()

    if not os.environ.get("OPENROUTER_API_KEY"):
        _fail(
            "OPENROUTER_API_KEY is not set. Extraction calls a model, so this "
            "example cannot run without it.\n"
            "  cp .env.example .env      # then fill it in"
        )
    if not os.environ.get("FIRM_MEM0_PG_DSN"):
        _fail(
            "FIRM_MEM0_PG_DSN is not set. Start one with:\n"
            "  docker compose -f examples/docker-compose.yml up -d\n"
            "  export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'"
        )

    # One gateway key for any model, so the firm is not tied to a vendor SDK.
    os.environ.setdefault("FIRM_MEM0_LLM_PROVIDER", "litellm")
    os.environ.setdefault("FIRM_MEM0_LLM_MODEL", "openrouter/anthropic/claude-haiku-4.5")

    # Embeddings run locally: the pool holds trading knowledge, and the
    # embedder is the component most likely to quietly send it somewhere.
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_PROVIDER", "fastembed")
    os.environ.setdefault("FIRM_MEM0_EMBEDDER_MODEL", "BAAI/bge-small-en-v1.5")
    os.environ.setdefault("FIRM_MEM0_EMBEDDING_DIMS", "384")

    # Reranking needs torch. Off by default; uv sync --extra rerank to try it.
    os.environ.setdefault("FIRM_MEM0_RERANK", "off")

    # mem0 reports usage to PostHog unless told not to. A pool of trading
    # knowledge has no business phoning home, so the example turns it off and
    # any real deployment should set this too.
    os.environ.setdefault("MEM0_TELEMETRY", "False")

    # The queue the MCP server writes to and the review UI reads from.
    if CANDIDATES_FROM_POOL_DSN:
        os.environ.setdefault("FIRM_MEMORY_CANDIDATES_URL", os.environ["FIRM_MEM0_PG_DSN"])


def exported() -> dict[str, str]:
    """The configured environment, for handing to a subprocess."""
    return dict(os.environ)


def _fail(message: str) -> None:
    """Stop with an actionable message rather than a traceback from deep inside."""
    print(f"\n{message}\n", file=sys.stderr)
    raise SystemExit(1)
