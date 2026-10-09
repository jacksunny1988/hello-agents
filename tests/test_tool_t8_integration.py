"""T8 集成测试：真实 uvx 起 MCP server（需联网，首次会下载 server）。

默认**不跑**——pyproject 的 `addopts` 排除了 integration 标记。显式打开：

    uv run pytest -m integration tests/test_tool_t8_integration.py -q
"""

from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import load_mcp_config

pytestmark = pytest.mark.integration

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mcp_servers.yaml"


async def test_real_everything_server():
    # 直接读 mcp_servers.yaml，和 examples/tool_t8_mcp.py 共用同一份声明
    cfg = load_mcp_config(CONFIG_PATH)["everything"]
    async with MCPClient("everything", cfg) as client:
        tools = await client.list_tools()
        assert len(tools) > 0

        # everything 的 echo 参数名是 message（不是 text）
        echo = next(t for t in tools if t.name.endswith("echo"))
        toolkit = Toolkit(tools=[echo], approver=AutoApprover(True))
        resp = await toolkit.call_tool(echo.name, message="hello-mcp")
        assert "hello-mcp" in resp.get_text()
