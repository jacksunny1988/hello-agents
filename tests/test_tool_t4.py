"""T4：Toolkit 注册中心（注册/注销 · 查找 · 分组 · schema 导出 · 单工具调用），全部离线确定性"""

from typing import ClassVar

import pytest

from hello_agents.tool._adapters import FunctionTool
from hello_agents.tool._base import ToolBase
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


class EchoTool(ToolBase):
    name = "echo"
    description = "回显"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


class BoomTool(ToolBase):
    """同步 call 抛业务异常，用来验证 call_tool 走了 T2 收口。"""

    name = "boom"
    description = "总是抛异常"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def call(self) -> ToolResponse:
        raise RuntimeError("boom")


def add(a: int, b: int = 1) -> int:
    """两数相加。

    Args:
        a: 第一个数。
        b: 第二个数，默认 1、可省略。
    """
    return a + b


# --- 注册与查找 -------------------------------------------------------------


def test_constructor_registers_all_tools():
    toolkit = Toolkit(tools=[EchoTool(), BoomTool()])
    assert [t.name for t in toolkit.list_tools()] == ["echo", "boom"]


def test_get_tool_returns_the_same_instance():
    echo = EchoTool()
    toolkit = Toolkit(tools=[echo])
    assert toolkit.get_tool("echo") is echo


def test_register_function_wraps_the_function():
    toolkit = Toolkit()
    tool = toolkit.register_function(add)
    assert isinstance(tool, FunctionTool)
    assert toolkit.get_tool("add") is tool
    assert tool.name == "add"


async def test_registered_function_is_callable_through_call_tool():
    # T5 起 call_tool 会做确认治理：函数包装出的 FunctionTool 非只读，
    # 需要 approver 放行才能执行
    toolkit = Toolkit(approver=AutoApprover(True))
    toolkit.register_function(add)
    resp = await toolkit.call_tool("add", a=2)  # b 用默认值 1
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "3"


async def test_call_tool_accepts_parameter_named_name():
    # 工具形参恰好叫 name 时，不得与 call_tool 的 name 形参冲突
    # （name 必须是 positional-only，否则 kwargs 会重复绑定 name）
    toolkit = Toolkit(approver=AutoApprover(True))

    def greet(name: str) -> str:
        """打招呼。

        Args:
            name: 名字。
        """
        return f"hi {name}"

    toolkit.register_function(greet)
    resp = await toolkit.call_tool("greet", name="world")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hi world"


def test_duplicate_registration_raises_value_error():
    toolkit = Toolkit(tools=[EchoTool()])
    with pytest.raises(ValueError, match="echo"):
        toolkit.register_tool(EchoTool())


# --- 注销 -------------------------------------------------------------------


def test_unregister_removes_the_tool():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.unregister("echo")
    with pytest.raises(KeyError):
        toolkit.get_tool("echo")


def test_unregister_unknown_name_raises_key_error():
    with pytest.raises(KeyError, match="nope"):
        Toolkit().unregister("nope")


def test_can_reregister_same_name_after_unregister():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.unregister("echo")
    replacement = EchoTool()
    toolkit.register_tool(replacement)
    assert toolkit.get_tool("echo") is replacement


# --- 未注册查找 -------------------------------------------------------------


def test_get_tool_unknown_name_raises_key_error_with_name():
    with pytest.raises(KeyError, match="ghost"):
        Toolkit().get_tool("ghost")


async def test_call_tool_unknown_name_raises_key_error():
    with pytest.raises(KeyError, match="ghost"):
        await Toolkit().call_tool("ghost")


# --- 顺序稳定性 -------------------------------------------------------------


def test_order_follows_registration():
    toolkit = Toolkit(tools=[BoomTool(), EchoTool()])
    toolkit.register_function(add)
    assert [t.name for t in toolkit.list_tools()] == ["boom", "echo", "add"]
    assert [s["function"]["name"] for s in toolkit.get_tool_schemas()] == [
        "boom",
        "echo",
        "add",
    ]


# --- 分组 -------------------------------------------------------------------


def test_default_group_is_basic():
    toolkit = Toolkit(tools=[EchoTool()])
    assert toolkit.list_groups() == ["basic"]


def test_group_filter_selects_only_that_group():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.register_function(add, group="math")
    assert [t.name for t in toolkit.list_tools(groups=["math"])] == ["add"]
    assert [t.name for t in toolkit.list_tools(groups=["basic"])] == ["echo"]


def test_group_filter_accepts_multiple_groups_keeping_registration_order():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.register_function(add, group="math")
    assert [t.name for t in toolkit.list_tools(groups=["math", "basic"])] == [
        "echo",
        "add",
    ]


def test_unknown_group_yields_empty_list():
    toolkit = Toolkit(tools=[EchoTool()])
    assert toolkit.list_tools(groups=["nope"]) == []


def test_list_groups_follows_first_appearance_order():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.register_function(add, group="math")
    toolkit.register_tool(BoomTool())
    assert toolkit.list_groups() == ["basic", "math"]


# --- schema 导出 ------------------------------------------------------------


def test_get_tool_schemas_are_function_schemas():
    schemas = Toolkit(tools=[EchoTool()]).get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "echo"
    assert schemas[0]["function"]["parameters"]["required"] == ["text"]


def test_get_tool_schemas_can_filter_by_group():
    toolkit = Toolkit(tools=[EchoTool()])
    toolkit.register_function(add, group="math")
    schemas = toolkit.get_tool_schemas(groups=["math"])
    assert [s["function"]["name"] for s in schemas] == ["add"]


# --- 调用 -------------------------------------------------------------------


async def test_call_tool_returns_a_tool_response():
    toolkit = Toolkit(tools=[EchoTool()])
    resp = await toolkit.call_tool("echo", text="hi")
    assert isinstance(resp, ToolResponse)
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hi"


async def test_call_tool_goes_through_t2_execution_wrapper():
    """BoomTool 抛 RuntimeError，应被 T2 收口成 ERROR 结果而不是抛给调用方。"""
    toolkit = Toolkit(tools=[BoomTool()], approver=AutoApprover(True))
    resp = await toolkit.call_tool("boom")
    assert resp.status is ToolStatus.ERROR
    assert "RuntimeError" in resp.get_text()


# --- 边界 -------------------------------------------------------------------


def test_empty_toolkit():
    toolkit = Toolkit()
    assert toolkit.list_tools() == []
    assert toolkit.get_tool_schemas() == []
    assert toolkit.list_groups() == []
