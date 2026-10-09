"""T10 演示：薄桥接层把模型的一次 tool_call 变成「执行 + 可回灌 Message」。

离线模拟，不连真实模型：手工造 ToolCallBlock，走 run_tool_call，
观察成功 / 坏参数 / 未注册三种回灌，并确认产物能被 formatter 出站。
"""

import asyncio

from hello_agents.bridge import run_tool_call
from hello_agents.model._formatter import to_openai_messages
from hello_agents.model.message import ToolCallBlock
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit


def build_toolkit() -> Toolkit:
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def echo(message: str) -> str:
        """原样回显。

        Args:
            message: 要回显的文本
        """
        return f"echo:{message}"

    toolkit.register_function(echo)
    return toolkit


def show(label: str, call: ToolCallBlock, msg) -> None:
    block = msg.content[0]
    print(f"[{label}] tool_call_id={block.tool_call_id!r}")
    print(f"    is_error={block.is_error}  output={block.output!r}")


async def main() -> None:
    toolkit = build_toolkit()

    # 1) 正常调用：模型给的 arguments 是 JSON 字符串
    ok = ToolCallBlock(name="echo", arguments='{"message":"hi"}', id="call-1")
    msg = await run_tool_call(ok, toolkit)
    show("成功", ok, msg)

    # 桥接产物在真实出站链路上的形状（role=tool 一条 dict）
    print(f"    出站: {to_openai_messages([msg])}")

    # 2) 坏参数：不是合法 JSON 对象，不执行工具，直接回灌 is_error
    bad = ToolCallBlock(name="echo", arguments="oops", id="call-2")
    show("坏参数", bad, await run_tool_call(bad, toolkit))

    # 3) 未注册工具：回灌 is_error，告诉模型没有该工具
    ghost = ToolCallBlock(name="ghost", arguments="{}", id="call-3")
    show("未注册", ghost, await run_tool_call(ghost, toolkit))


asyncio.run(main())
