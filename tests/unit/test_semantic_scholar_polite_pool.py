"""Semantic Scholar must join the polite pool when an email is configured."""

import httpx
import pytest
import respx

from research_mcp.providers import semantic_scholar as module

SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


@pytest.mark.asyncio
@respx.mock
async def test_get_json_sends_mailto_user_agent(monkeypatch):
    monkeypatch.setenv("RESEARCH_MCP_EMAIL", "user@example.org")
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))

    await module._get_json("/paper/search", {"query": "parking"})

    user_agent = route.calls[0].request.headers["user-agent"]
    assert "mailto:user@example.org" in user_agent


@pytest.mark.asyncio
@respx.mock
async def test_get_json_without_email_keeps_plain_user_agent(monkeypatch):
    monkeypatch.delenv("RESEARCH_MCP_EMAIL", raising=False)
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))

    await module._get_json("/paper/search", {"query": "parking"})

    user_agent = route.calls[0].request.headers["user-agent"]
    assert "mailto:" not in user_agent
