from contextlib import AsyncExitStack
from typing import Self

from mcp import ClientSession, StdioServerParameters, stdio_client

from ._config import StdioServerConfig
from ._mcp_tool import MCPTool


class MCPClient:
    """stdio MCP 的 stateful 客户端：connect → list_tools → close。"""

    def __init__(self, name: str, config: StdioServerConfig):
        self._name = name
        self._config = config
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def connect(self) -> None:
        """连接到 stdio MCP 服务器"""
        if self._stack is not None:
            raise RuntimeError(f"MCP '{self._name}' 已连接")
        cfg = self._config
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            cwd=cfg.cwd,
        )
        stack = AsyncExitStack()
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params)
        )
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
