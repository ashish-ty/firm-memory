"""The MCP server — a thin transport adapter and nothing more.

    OpenCode -> MCP -> Firm Memory -> MemoryProvider -> mem0

Every decision of substance is made below this file. The server's only jobs are
to register the four tools, hand their arguments to
:class:`~firm_memory.mcp.tools.MemoryTools`, and return the result. Any other
internal agent uses the same interface.

The MCP SDK is an optional dependency (``uv sync --extra mcp``) and
is imported lazily, so a consumer embedding the Python API directly does not
carry it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..errors import ConfigurationError
from ..memory import FirmMemory
from ..models import MemoryTier
from .tools import TOOL_DESCRIPTIONS, MemoryTools, taxonomy_reference

logger = logging.getLogger(__name__)

SERVER_NAME = "firm-memory"


def build_server(memory: FirmMemory | None = None, *, name: str = SERVER_NAME) -> Any:
    """Create a ``FastMCP`` server exposing the four memory tools.

    *memory* is injectable so a consumer can supply an already-configured API
    (or a fake, in tests); by default it is built from the environment.
    """
    mcp_server = _fast_mcp(name)
    api = memory if memory is not None else FirmMemory.from_env()
    tools = MemoryTools(api)

    @mcp_server.tool(name="memory_search", description=TOOL_DESCRIPTIONS["memory_search"])
    def memory_search(
        query: str,
        repos: list[str] | None = None,
        domains: list[str] | None = None,
        firm: bool = True,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> dict:
        return tools.memory_search(
            query, repos=repos, domains=domains, firm=firm, types=types, limit=limit
        )

    @mcp_server.tool(name="memory_get", description=TOOL_DESCRIPTIONS["memory_get"])
    def memory_get(memory_id: str) -> dict:
        return tools.memory_get(memory_id)

    @mcp_server.tool(name="memory_propose", description=TOOL_DESCRIPTIONS["memory_propose"])
    def memory_propose(
        content: str,
        type: str,
        repos: list[str] | None = None,
        domains: list[str] | None = None,
        firm: bool = False,
        confidence: float | None = None,
        reference: str | None = None,
        author: str | None = None,
        evidence: str | None = None,
        task: str | None = None,
        tier: str = MemoryTier.DURABLE.value,
    ) -> dict:
        return tools.memory_propose(
            content,
            type,
            repos=repos,
            domains=domains,
            firm=firm,
            confidence=confidence,
            reference=reference,
            author=author,
            evidence=evidence,
            task=task,
            tier=tier,
        )

    @mcp_server.tool(name="memory_correct", description=TOOL_DESCRIPTIONS["memory_correct"])
    def memory_correct(memory_id: str, reason: str, reporter: str | None = None) -> dict:
        return tools.memory_correct(memory_id, reason, reporter=reporter)

    @mcp_server.resource("firm-memory://taxonomy")
    def taxonomy() -> list[dict]:
        """The memory types an agent may propose, and what each is for."""
        return taxonomy_reference()

    return mcp_server


def tool_names() -> Sequence[str]:
    """The tools this server exposes. Used by tests and by deployment checks."""
    return tuple(TOOL_DESCRIPTIONS)


def _fast_mcp(name: str) -> Any:
    """Import the MCP SDK, failing with an actionable message when it is absent."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigurationError(
            "The MCP server requires the 'mcp' package. Install it with: uv sync --extra mcp"
        ) from exc
    return FastMCP(name)


def main() -> None:  # pragma: no cover - process entry point
    """Run the server over stdio."""
    logging.basicConfig(level=logging.INFO)
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
