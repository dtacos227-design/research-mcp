import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_create_list_and_get_status_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("RESEARCH_MCP_DB_PATH", str(tmp_path / "research_mcp.db"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={
            "RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
            "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db"),
        },
    )
    with anyio.fail_after(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                created = await session.call_tool("create_project", {"name": "Integration Test"})
                assert created.is_error is False

                listed = await session.call_tool("list_projects", {})
                assert listed.is_error is False

                status = await session.call_tool(
                    "get_project_status", {"project_id": "nonexistent"}
                )
                assert status.is_error is True
