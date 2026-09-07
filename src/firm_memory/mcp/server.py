"""The MCP server — a thin transport adapter and nothing more.

    OpenCode -> MCP -> Firm Memory -> MemoryProvider -> mem0

Every decision of substance is made below this file. The server's only jobs are
to register the five tools, hand their arguments to
:class:`~firm_memory.mcp.tools.MemoryTools`, and return the result. Any other
internal agent uses the same interface.

The MCP SDK is an optional dependency (``uv sync --extra mcp``) and is imported
lazily, so a consumer embedding the Python API directly does not carry it.

Both SDK majors are supported. The class the server is built on was renamed in
mcp 2.x — ``FastMCP`` became ``MCPServer`` — while the three pieces this file
uses (the ``tool`` and ``resource`` decorators, and ``run``) kept their shapes.
So the version is resolved at import time and nothing below it changes. Pinning
to one major instead would mean an installation whose SDK version is decided by
whatever else the consumer depends on can fail to start.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from ..errors import ConfigurationError
from ..memory import FirmMemory
from ..models import MemoryTier
from ..provenance import Source
from .tools import DEFAULT_SOURCE_KIND, TOOL_DESCRIPTIONS, MemoryTools, taxonomy_reference

logger = logging.getLogger(__name__)

SERVER_NAME = "firm-memory"


def build_server(memory: FirmMemory | None = None, *, name: str = SERVER_NAME) -> Any:
    """Create a ``FastMCP`` server exposing the five memory tools.

    *memory* is injectable so a consumer can supply an already-configured API
    (or a fake, in tests); by default it is built from the environment.
    """
    mcp_server = _mcp_server(name)
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

    @mcp_server.tool(name="memory_ingest", description=TOOL_DESCRIPTIONS["memory_ingest"])
    def memory_ingest(
        content: str,
        kind: str = DEFAULT_SOURCE_KIND,
        repos: list[str] | None = None,
        domains: list[str] | None = None,
        firm: bool = False,
        reference: str | None = None,
        author: str | None = None,
        evidence: str | None = None,
        source: str = Source.DOCUMENT,
        task: str | None = None,
        tier: str = MemoryTier.DURABLE.value,
    ) -> dict:
        return tools.memory_ingest(
            content,
            kind=kind,
            repos=repos,
            domains=domains,
            firm=firm,
            reference=reference,
            author=author,
            evidence=evidence,
            source=source,
            task=task,
            tier=tier,
        )

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


def _mcp_server(name: str) -> Any:
    """Build the SDK's server object, whichever major is installed.

    The two failures are told apart on purpose. "Not installed" and "installed
    but this version moved the class" need different fixes, and reporting the
    second as the first sends the reader to run an install that has already
    succeeded — which is exactly the wrong place to look.
    """
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise ConfigurationError(
            "The MCP server requires the 'mcp' package. Install it with: uv sync --extra mcp"
        ) from exc

    for module_name, attribute in (("mcp.server.mcpserver", "MCPServer"), ("mcp.server.fastmcp", "FastMCP")):
        try:
            module = import_module(module_name)
        except ImportError:
            continue
        server_class = getattr(module, attribute, None)
        if server_class is not None:
            return server_class(name)

    raise ConfigurationError(
        f"The installed 'mcp' package ({_sdk_version()}) exposes neither "
        "mcp.server.mcpserver.MCPServer (2.x) nor mcp.server.fastmcp.FastMCP (1.x). "
        "The package is present, so this is a version problem, not a missing install. "
        "Pin a supported one with: uv add 'mcp>=1.2'"
    )


def _sdk_version() -> str:
    """The installed SDK version, for an error message that names it."""
    try:
        return version("mcp")
    except PackageNotFoundError:  # pragma: no cover - installed without metadata
        return "unknown version"


def main() -> None:  # pragma: no cover - process entry point
    """Run the server over stdio."""
    logging.basicConfig(level=logging.INFO)
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
