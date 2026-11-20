import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_MCP_SKIP_LIVE_TESTS") == "1",
    reason="live PDF network test; unset RESEARCH_MCP_SKIP_LIVE_TESTS to run",
)


def parse_list_tool_result(result) -> list[dict]:
    return [json.loads(block.text) for block in result.content]


@pytest.mark.asyncio
async def test_live_arxiv_pdf_is_saved_parsed_and_searchable(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    db_path = tmp_path / "research_mcp.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_mcp.server"],
        env={
            "RESEARCH_MCP_WORKSPACE": str(workspace_root),
            "RESEARCH_MCP_DB_PATH": str(db_path),
        },
    )

    with anyio.fail_after(120):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                created = await session.call_tool("create_project", {"name": "Live PDF"})
                assert created.is_error is False
                project = json.loads(created.content[0].text)

                saved = await session.call_tool(
                    "save_paper_to_project",
                    {
                        "project_id": project["id"],
                        "identifier": "1706.03762v7",
                        "download_pdf": True,
                    },
                )
                assert saved.is_error is False
                paper = json.loads(saved.content[0].text)
                assert paper["download_status"] == "downloaded", paper["warnings"]
                assert paper["section_count"] > 0
                assert Path(paper["pdf_path"]).is_file()

                matches = await session.call_tool(
                    "search_local_library",
                    {"project_id": project["id"], "query": "attention"},
                )
                assert matches.is_error is False
                results = parse_list_tool_result(matches)
                assert results
                assert any(result["citekey"] == paper["citekey"] for result in results)
