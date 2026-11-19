import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_MCP_SKIP_LIVE_TESTS") == "1",
    reason="live network test; set RESEARCH_MCP_SKIP_LIVE_TESTS=0 to run",
)


@pytest.mark.asyncio
async def test_real_cross_source_search_has_no_duplicate_dois(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={
            "RESEARCH_MCP_WORKSPACE": str(tmp_path / "workspaces"),
            "RESEARCH_MCP_DB_PATH": str(tmp_path / "research_mcp.db"),
        },
    )
    with anyio.fail_after(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(
                    "search_papers",
                    {"query": "attention is all you need transformer", "limit": 10},
                )
                assert response.is_error is False
    # Confirmed empirically during Task 3: `structured_content` is None for a tool
    # whose return type is a bare `dict` (as `search_papers` is) — the SDK only
    # populates it when it can build a real output schema from a more specific
    # type. `content[0].text` always carries the JSON-serialized return value
    # regardless, so parse that instead.
    import json

    payload = json.loads(response.content[0].text)
    dois = [r["doi"] for r in payload["results"] if r["doi"]]
    assert len(dois) == len(set(dois))
    assert len(payload["results"]) > 0
    assert len({r["source_provider"] for r in payload["results"]}) >= 2, (
        f"expected at least 2 providers to respond, got: {payload['errors']}"
    )
