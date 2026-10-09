"""T8：MCP 接入（stdio）——配置解析 · MCPTool 适配 · 客户端，全部离线确定性"""

from pathlib import Path

import mcp.types

from hello_agents.tool._response import ToolStatus
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import StdioServerConfig, load_mcp_config
from hello_agents.tool.mcp._mcp_tool import MCPTool


def _raw_tool(name="echo", schema=None, read_only=False):
    """用真实的 mcp.types.Tool，而不是 SimpleNamespace。

    手写 fake 会与 SDK 漂移：SDK 的字段是 snake_case，camelCase 只是序列化别名，
    用 SimpleNamespace 按 wire 名搭一个"看起来一样"的对象，正好把这个差异盖住。
    """
    return mcp.types.Tool(
        name=name,
        description="a tool",
        inputSchema=schema
        or {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        annotations=mcp.types.ToolAnnotations(readOnlyHint=read_only),
    )


class _FakeSession:
    def __init__(self, text="hi", is_error=False):
        self._text = text
        self._is_error = is_error

    async def call_tool(self, name, arguments=None):
        return mcp.types.CallToolResult(
            isError=self._is_error,
            content=[mcp.types.TextContent(type="text", text=self._text)],
        )


def test_tool_name_is_namespaced():
    tool = MCPTool("srv", _raw_tool(), _FakeSession())
    assert tool.name == "mcp__srv__echo"


def test_tool_name_sanitizes_illegal_chars():
    tool = MCPTool("srv", _raw_tool(name="a.b:c"), _FakeSession())
    assert tool.name == "mcp__srv__axbxc"


def test_input_schema_is_preserved():
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/X"}},
        "$defs": {"X": {"type": "string"}},
    }
    tool = MCPTool("srv", _raw_tool(schema=schema), _FakeSession())
    assert "$defs" in tool.input_schema
    assert tool.input_schema["properties"]["x"]["$ref"] == "#/$defs/X"


def test_read_only_hint():
    tool = MCPTool("srv", _raw_tool(read_only=True), _FakeSession())
    assert tool.is_read_only is True


async def test_call_success_extracts_text():
    tool = MCPTool("srv", _raw_tool(), _FakeSession(text="hello"))
    resp = await tool(message="hello")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hello"


async def test_call_error_returns_fail():
    tool = MCPTool("srv", _raw_tool(), _FakeSession(text="boom", is_error=True))
    resp = await tool(message="x")
    assert resp.status is ToolStatus.ERROR
    assert "boom" in resp.get_text()


class _FakeListSession:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return mcp.types.ListToolsResult(tools=self._tools)


async def test_client_namespaces_tools_by_server_name():
    """list_tools 必须取 result.tools。

    直接迭代 ListToolsResult 得到的是 pydantic 的 (字段名, 值) 元组，不是工具。
    """
    raw = mcp.types.Tool(
        name="echo",
        description="a tool",
        inputSchema={"type": "object", "properties": {}},
    )
    client = MCPClient("everything", StdioServerConfig(command="npx"))
    client._session = _FakeListSession([raw])

    tools = await client.list_tools()

    assert [t.name for t in tools] == ["mcp__everything__echo"]


def test_load_mcp_config(tmp_path: Path):
    yaml_file = tmp_path / "mcp.yaml"
    yaml_file.write_text(
        "servers:\n  everything:\n    transport: stdio\n"
        "    command: uvx\n    args: [mcp-server-everything]\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(yaml_file)
    assert "everything" in configs
    assert configs["everything"].command == "uvx"
    assert configs["everything"].args == ["mcp-server-everything"]


def test_load_mcp_config_missing_servers(tmp_path: Path):
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("{}\n", encoding="utf-8")
    assert load_mcp_config(yaml_file) == {}
