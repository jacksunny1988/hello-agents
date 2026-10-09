"""T11：最小 Agent 主循环（examples 演示，框架不内置）。"""

import asyncio

from hello_agents.bridge import model_tools_for, run_tool_call
from hello_agents.model._response import ChatResponse
from hello_agents.model.message import TextBlock
from hello_agents.model.providers._openai_compat import build_model
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit


async def run_agent(
    model,
    toolkit,
    messages,
    *,
    max_rounds: int = 8,
    verbose: bool = True,
) -> ChatResponse:
    """跑工具型 Agent，直到模型不再调工具；返回最后一轮响应。"""
    for round_no in range(1, max_rounds + 1):
        tools = model_tools_for(toolkit)
        response: ChatResponse = await model(
            messages=messages,
            tools=tools or None,
        )
        messages.append(response.to_message())
        text = "".join(
            block.text for block in response.content if isinstance(block, TextBlock)
        )
        if text and verbose:
            print(f"[assistant #{round_no}] {text}")

        calls = response.get_tool_calls()
        if not calls:
            return response

        if verbose:
            for call in calls:
                print(f"[tool-call #{round_no}] {call.name} {call.arguments}")

        results = await asyncio.gather(
            *(run_tool_call(call, toolkit) for call in calls)
        )
        messages.extend(results)
    raise RuntimeError(f"超过最大轮次 {max_rounds}，仍未结束对话。")


def _build_toolkit():
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def add(a: float, b: float) -> float:
        """两数相加。

        Args:
            a: 第一个数
            b: 第二个数
        """
        return a + b

    toolkit.register_function(add)
    return toolkit


async def main() -> None:
    from hello_agents.model.message import Message

    model = build_model("dashscope:qwen3.7-plus")
    toolkit = _build_toolkit()

    messages = [Message.user("请帮我算一下 123 加 456 等于多少，调用 add 工具。")]
    final = await run_agent(model, toolkit, messages)
    answer = "".join(
        block.text for block in final.content if isinstance(block, TextBlock)
    )
    print("最终答案：", answer)


if __name__ == "__main__":
    asyncio.run(main())
