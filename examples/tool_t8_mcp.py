"""T8 演示：从 mcp_servers.yaml 连上真实 MCP server，列出工具并调用。

需要联网——首次运行 uvx 会把 server 下载到本地缓存。

    uv run python examples/tool_t8_mcp.py
"""

import asyncio
import json
from pathlib import Path

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import load_mcp_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    configs = load_mcp_config(PROJECT_ROOT / "mcp_servers.yaml")
    print(f"配置里的 server：{list(configs)}")

    for name, cfg in configs.items():
        print(f"\n=== [{name}] {cfg.command} {' '.join(cfg.args)} ===")
        async with MCPClient(name, cfg) as client:
            tools = await client.list_tools()
            print(f"共 {len(tools)} 个工具：")
            for tool in tools:
                flag = "只读" if tool.is_read_only else "有副作用"
                print(f"  - {tool.name}（{flag}）")
                print(
                    f"    schema: {json.dumps(tool.input_schema, ensure_ascii=False)}"
                )

            echo = next((t for t in tools if t.name.endswith("echo")), None)
            if echo is None:
                print("\n没有以 echo 结尾的工具，跳过调用")
                continue

            print(f"\n调用 {echo.name}(message='hello-mcp')：")
            toolkit = Toolkit(tools=[echo], approver=AutoApprover(True))
            resp = await toolkit.call_tool(echo.name, message="hello-mcp")
            print(f"[{resp.status}] {resp.get_text()}")


asyncio.run(main())
