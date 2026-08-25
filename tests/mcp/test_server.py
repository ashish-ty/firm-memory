"""The server is transport only; its job is to expose the four tools."""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.mcp import server


def test_the_server_exposes_exactly_the_designed_tools():
    assert set(server.tool_names()) == {
        "memory_search",
        "memory_get",
        "memory_propose",
        "memory_correct",
    }


def test_the_sdk_is_optional_and_its_absence_says_how_to_fix_it():
    try:
        import mcp.server.fastmcp  # noqa: F401
    except ImportError:
        with pytest.raises(ConfigurationError, match="pip install"):
            server.build_server()
    else:  # pragma: no cover - only when the extra is installed
        pytest.skip("mcp extra is installed, so absence cannot be exercised")


def test_building_a_server_registers_every_tool(memory):
    pytest.importorskip("mcp.server.fastmcp", reason="mcp extra not installed")
    built = server.build_server(memory)
    registered = {tool.name for tool in built._tool_manager.list_tools()}
    assert registered == set(server.tool_names())
