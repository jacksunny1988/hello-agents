import json
from typing import Any

from hello_agents.model.message import (
    Message,
    Role,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


def parse_tool_arguments(call: ToolCallBlock) -> dict[str, Any] | None:
    """把模型给的 JSON 字符串参数解析成 dict；非法或非对象返回 None。"""
    try:
        data = json.loads(call.arguments)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def tool_result_message(call: ToolCallBlock, resp: ToolResponse) -> Message:
    """把一次工具执行结果转成可回灌的 role=tool Message。

    状态有损映射：仅 success -> is_error=False；error/denied/interrupted
    均压扁为 is_error=True，原因靠 output 文本传达。
    """
    return Message(
        role=Role.TOOL,
        name=call.name,
        content=[
            ToolResultBlock(
                tool_call_id=call.id,
                output=resp.get_text(),
                is_error=resp.status is not ToolStatus.SUCCESS,
                name=call.name,
            )
        ],
    )


async def run_tool_call(call: ToolCallBlock, toolkit: Toolkit) -> Message:
    """执行模型的一次 tool_call，返回可直接追加进对话历史的 Message。

    - 参数非法 / 工具未注册：回灌 is_error，不抛异常；
    - 审批未配置等 RuntimeError、CancelledError：照常向上传播。
    """
    kwargs = parse_tool_arguments(call)
    if kwargs is None:
        resp = ToolResponse.fail(f"工具参数不是合法 JSON 对象：{call.arguments!r}")
        return tool_result_message(call, resp)

    # 只对「查表未命中」收口：get_tool 是唯一会因未注册抛 KeyError 的地方。
    # 若把 call_tool 整段包进 except KeyError，hook/approver 抛出的 KeyError
    # 会被误报成「未注册的工具」，掩盖真实原因。
    try:
        toolkit.get_tool(call.name)
    except KeyError:
        return tool_result_message(
            call, ToolResponse.fail(f"未注册的工具：{call.name}")
        )

    result = await toolkit.call_tool(call.name, **kwargs)
    return tool_result_message(call, result)
