"""MCP client adapters over the official `mcp` SDK (D4).

A session is opened per call: connector traffic here is a handful of calls
per job run, and stateless sessions keep the child process free of
connection lifecycle management. Revisit only if a server's per-session
setup cost ever shows up in practice.
"""

from __future__ import annotations

import json
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

# The SDK's own helper for a correctly-configured transport client (SSE
# timeouts, redirects, headers). It lives in a private module but is what
# the SDK's client code itself uses; revisit on `mcp` upgrades.
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent
from pydantic import JsonValue

from pihome.connectors.application.ports import ToolDescriptor
from pihome.connectors.domain.tools import ToolCall, ToolResult


def _content_to_json(blocks: list[Any]) -> JsonValue:
    texts: list[str] = [block.text for block in blocks if isinstance(block, TextContent)]
    if len(texts) == 1:
        try:
            parsed: JsonValue = json.loads(texts[0])
            return parsed
        except json.JSONDecodeError:
            return texts[0]
    fallback: list[JsonValue] = list(texts)
    return fallback


class McpHttpClient:
    """Streamable-HTTP transport: MCP servers running as compose services
    (GitHub, Google Calendar) and Home Assistant's native MCP endpoint."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = headers or {}

    async def list_tools(self) -> list[ToolDescriptor]:
        async with (
            create_mcp_http_client(headers=self._headers) as http,
            streamable_http_client(self._url, http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
        return [
            ToolDescriptor(name=tool.name, description=tool.description or "")
            for tool in result.tools
        ]

    async def call_tool(self, call: ToolCall) -> ToolResult:
        async with (
            create_mcp_http_client(headers=self._headers) as http,
            streamable_http_client(self._url, http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(call.name, dict(call.args))
        return ToolResult(
            tool=call.tool,
            content=_content_to_json(list(result.content)),
            is_error=bool(result.is_error),
        )


class McpStdioClient:
    """Stdio transport: MCP servers spawned as subprocesses."""

    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self._params = StdioServerParameters(command=command, args=args, env=env)

    async def list_tools(self) -> list[ToolDescriptor]:
        async with (
            stdio_client(self._params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
        return [
            ToolDescriptor(name=tool.name, description=tool.description or "")
            for tool in result.tools
        ]

    async def call_tool(self, call: ToolCall) -> ToolResult:
        async with (
            stdio_client(self._params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(call.name, dict(call.args))
        return ToolResult(
            tool=call.tool,
            content=_content_to_json(list(result.content)),
            is_error=bool(result.is_error),
        )
