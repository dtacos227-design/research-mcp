from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research_mcp.server import create_project, list_projects


@pytest.mark.asyncio
async def test_create_project_wraps_unexpected_errors():
    with patch("research_mcp.server.get_connection", AsyncMock(side_effect=OSError("disk"))):
        with pytest.raises(ToolError, match="OSError: disk"):
            await create_project("x")


@pytest.mark.asyncio
async def test_list_projects_wraps_unexpected_errors():
    with patch("research_mcp.server.get_connection", AsyncMock(side_effect=OSError("disk"))):
        with pytest.raises(ToolError, match="OSError: disk"):
            await list_projects()
