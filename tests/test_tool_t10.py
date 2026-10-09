"""T10：薄桥接层——参数解析 · 状态映射 · 执行回灌，全部离线确定性"""

import pytest

from hello_agents.bridge import (
    parse_tool_arguments,
    run_tool_call,
    tool_result_message,
)
from hello_agents.model._formatter import to_openai_messages
from hello_agents.model.message import (
    Message,
    Role,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.tool._governance import AutoApprover, ToolHook
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


def _call(name="echo", arguments='{"message":"hi"}', call_id="call-1"):
    return ToolCallBlock(name=name, arguments=arguments, id=call_id)


def _toolkit(approver=None):
    toolkit = Toolkit(tools=[], approver=approver)

    def echo(message: str) -> str:
        """原样回显。

        Args:
            message: 要回显的文本
        """
        return f"echo:{message}"

    toolkit.register_function(echo)
    return toolkit


# --- parse_tool_arguments ---------------------------------------------------


def test_parse_arguments_decodes_object():
    assert parse_tool_arguments(_call()) == {"message": "hi"}


def test_parse_arguments_rejects_invalid_json():
    assert parse_tool_arguments(_call(arguments="{bad")) is None


@pytest.mark.parametrize("arguments", ["[1,2]", "123", '"str"', "null"])
def test_parse_arguments_rejects_non_object(arguments):
    assert parse_tool_arguments(_call(arguments=arguments)) is None


# --- tool_result_message：状态映射 ------------------------------------------


def test_success_maps_to_not_error():
    msg = tool_result_message(_call(), ToolResponse.succeed("ok"))
    block = msg.content[0]
    assert isinstance(msg, Message)
    assert isinstance(block, ToolResultBlock)
    assert msg.role is Role.TOOL
    assert block.tool_call_id == "call-1"
    assert block.output == "ok"
    assert block.is_error is False
    assert block.name == "echo"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ToolResponse.fail("x"),
        lambda: ToolResponse.denied("x"),
    ],
)
def test_error_and_denied_map_to_error(factory):
    msg = tool_result_message(_call(), factory())
    assert msg.content[0].is_error is True


def test_interrupted_maps_to_error():
    resp = ToolResponse(status=ToolStatus.INTERRUPTED, content=[])
    msg = tool_result_message(_call(), resp)
    assert msg.content[0].is_error is True


# --- run_tool_call：端到端（不经真实模型）-----------------------------------


async def test_run_tool_call_executes_and_builds_message():
    msg = await run_tool_call(_call(), _toolkit(AutoApprover(True)))
    block = msg.content[0]
    assert block.is_error is False
    assert block.output == "echo:hi"
    assert block.tool_call_id == "call-1"  # 锚点保真


async def test_run_tool_call_bad_arguments_does_not_execute():
    msg = await run_tool_call(_call(arguments="oops"), _toolkit(AutoApprover(True)))
    assert msg.content[0].is_error is True
    assert "JSON" in msg.content[0].output


async def test_run_tool_call_unknown_tool():
    msg = await run_tool_call(_call(name="ghost"), _toolkit(AutoApprover(True)))
    assert msg.content[0].is_error is True
    assert "未注册" in msg.content[0].output


async def test_run_tool_call_denied():
    msg = await run_tool_call(_call(), _toolkit(AutoApprover(False)))
    assert msg.content[0].is_error is True


async def test_run_tool_call_missing_approver_raises():
    with pytest.raises(RuntimeError, match="approver"):
        await run_tool_call(_call(), _toolkit(None))


async def test_run_tool_call_tool_whose_parameter_is_named_name():
    # 回归：工具形参名与 call_tool 的 name 冲突时，必须正常执行，不得抛 TypeError
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def greet(name: str) -> str:
        """打招呼。

        Args:
            name: 名字
        """
        return f"hi {name}"

    toolkit.register_function(greet)
    msg = await run_tool_call(
        _call(name="greet", arguments='{"name":"world"}'), toolkit
    )
    assert msg.content[0].is_error is False
    assert msg.content[0].output == "hi world"


async def test_hook_keyerror_is_not_misreported_as_unregistered():
    # 收口只应覆盖「查表未命中」；hook 自身抛的 KeyError 不能被误报成「未注册」
    class Boom(ToolHook):
        def before(self, tool, kwargs):
            raise KeyError("boom-from-hook")

    toolkit = _toolkit(AutoApprover(True))
    toolkit.add_hook(Boom())
    with pytest.raises(KeyError):
        await run_tool_call(_call(), toolkit)


# --- 桥接产物在真实出站链路上的形状 -----------------------------------------


def test_bridge_output_consumable_by_formatter():
    msg = tool_result_message(_call(), ToolResponse.succeed("echo:hi"))
    assert to_openai_messages([msg]) == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "echo:hi",
            "name": "echo",
        }
    ]
