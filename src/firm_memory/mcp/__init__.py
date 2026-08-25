"""MCP: how AI agents access firm memory.

The server is a thin transport adapter. All four tools are implemented as plain
functions in :mod:`firm_memory.mcp.tools`, which has no dependency on the MCP
SDK — so the agent-facing contract can be tested directly, and a consumer that
speaks something other than MCP can call the same functions.
"""

from __future__ import annotations

from .tools import MemoryTools

__all__ = ["MemoryTools"]
