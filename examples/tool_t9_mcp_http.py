"""T9 演示：连本地 Streamable HTTP MCP server，列出工具并调用。

先另开一个终端把 server 起起来：

    uv run python tests/_mcp_http_server.py 8765

再运行本脚本：

    uv run python examples/tool_t9_mcp_http.py
"""

import asyncio

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import HttpServerConfig

URL = "http://127.0.0.1:8765/mcp"


async def main() -> None:
    print(f"连接 {URL}（需要先起 tests/_mcp_http_server.py 8765）...")

    async with MCPClient("local_http", HttpServerConfig(url=URL)) as client:
        tools = await client.list_tools()
        print(f"\n共 {len(tools)} 个工具：")
        for tool in tools:
            print(f"  - {tool.name}")

        toolkit = Toolkit(tools=tools, approver=AutoApprover(True))

        echo = next(t for t in tools if t.name.endswith("echo"))
        print(f"\n调用 {echo.name}(message='hello-http')：")
        resp = await toolkit.call_tool(echo.name, message="hello-http")
        print(f"[{resp.status}] {resp.get_text()}")

        add = next(t for t in tools if t.name.endswith("add"))
        print(f"\n调用 {add.name}(a=2, b=3)：")
        resp = await toolkit.call_tool(add.name, a=2, b=3)
        print(f"[{resp.status}] {resp.get_text()}")


asyncio.run(main())
