import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_research_mcp_server_completes_initialize_handshake_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "research_mcp.server"])
    with anyio.fail_after(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                assert result.server_info.name == "research-mcp"
