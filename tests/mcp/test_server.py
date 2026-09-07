"""The server is transport only; its job is to expose the five tools."""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.mcp import server


def sdk_installed() -> bool:
    """Whether either supported SDK major is importable."""
    for module in ("mcp.server.mcpserver", "mcp.server.fastmcp"):
        try:
            __import__(module)
        except ImportError:
            continue
        return True
    return False


def test_the_server_exposes_exactly_the_designed_tools():
    assert set(server.tool_names()) == {
        "memory_search",
        "memory_get",
        "memory_ingest",
        "memory_propose",
        "memory_correct",
    }


def test_the_sdk_is_optional_and_its_absence_says_how_to_fix_it():
    if sdk_installed():  # pragma: no cover - only without the extra
        pytest.skip("mcp extra is installed, so absence cannot be exercised")
    with pytest.raises(ConfigurationError, match="uv sync"):
        server.build_server()


def test_building_a_server_registers_every_tool(memory):
    if not sdk_installed():
        pytest.skip("mcp extra not installed")
    built = server.build_server(memory)
    registered = {tool.name for tool in built._tool_manager.list_tools()}
    assert registered == set(server.tool_names())


def test_either_sdk_major_can_carry_the_server(memory):
    """FastMCP became MCPServer in mcp 2.x; both must start.

    An installation whose SDK version is decided by whatever else the consumer
    depends on must not fail to start, and the failure it produced before this
    was a message telling the reader to install a package they already had.
    """
    if not sdk_installed():
        pytest.skip("mcp extra not installed")
    assert type(server.build_server(memory)).__name__ in {"MCPServer", "FastMCP"}


def test_a_version_problem_is_not_reported_as_a_missing_install(monkeypatch):
    """The two need different fixes, so they must not share a message."""
    if not sdk_installed():
        pytest.skip("mcp extra not installed")

    def hide(name: str):
        raise ImportError(f"no module named {name}")

    monkeypatch.setattr("firm_memory.mcp.server.import_module", hide)

    with pytest.raises(ConfigurationError, match="version problem, not a missing install"):
        server.build_server()
