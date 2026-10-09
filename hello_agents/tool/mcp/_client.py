from contextlib import AsyncExitStack
from typing import Self

import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from ._config import HttpServerConfig, StdioServerConfig
from ._mcp_tool import MCPTool


class MCPClient:
    """MCP 客户端：支持 stdio / streamable-http / sse，统一 stateful 生命周期。"""

    def __init__(self, name: str, config: StdioServerConfig | HttpServerConfig):
        self._name = name
        self._config = config
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    def _build_transport(self, stack: AsyncExitStack):
        """构建 stdio 传输"""
        cfg = self._config
        if isinstance(cfg, StdioServerConfig):
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env,
                cwd=cfg.cwd,
            )
            return stdio_client(params)
        # HTTP：URL 以 /sse 结尾走 SSE，否则走 Streamable HTTP
        if cfg.url.rstrip("/").endswith("/sse"):
            return sse_client(cfg.url, headers=cfg.headers)
        http_client = None
        if cfg.headers:
            http_client = httpx.AsyncClient(headers=cfg.headers)
            # 随连接关闭一起释放，杜绝泄漏
            stack.push_async_callback(http_client.aclose)
        return streamable_http_client(cfg.url, http_client=http_client)

    async def connect(self) -> None:
        """连接到 stdio MCP 服务器"""
        if self._stack is not None:
            raise RuntimeError(f"MCP '{self._name}' 已连接")
        stack = AsyncExitStack()
        await stack.__aenter__()
        transport = self._build_transport(stack)
        read_stream, write_stream = await stack.enter_async_context(transport)
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        self._stack = stack
        self._session = session

    async def list_tools(self) -> list[MCPTool]:
        """列出所有工具"""
        if self._session is None:
            raise RuntimeError(f"MCP '{self._name}' 未连接，先 connect()")
        result = await self._session.list_tools()
        return [
            MCPTool(server_name=self._name, raw_tool=tool, session=self._session)
            for tool in result.tools
        ]

    async def close(self) -> None:
        """关闭连接"""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
