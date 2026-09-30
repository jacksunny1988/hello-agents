"""Model M6 示例：工具调用闭环，端到端

三部分，都走真实网络：

1. **非流式闭环**——模型声明要调工具 → 我们执行 → 结果回灌 → 模型据此给出最终答案，
   走完 `tool_calls -> tool -> stop` 全程；
2. **并行两个工具**——一次请求触发两个调用，验证 arguments 不串、两条结果都回灌；
3. **流式 tool_calls**——打印分片按 index 到达的过程，收尾的 `finished_reason` 是
   `tool_calls`，拼接出的 arguments 与非流式一致。

主循环写在**本 demo 里**而不是 model 层：「要不要再来一轮」属于上层决策，model 层
只提供到「执行工具调用并生成回灌消息」为止（规格 §3.6）。

需要 .env 里备好对应 provider 的 API key，**会发起真实网络请求并产生费用**。

用法：
    python examples/model_m6_demo.py                       # 默认 deepseek
    python examples/model_m6_demo.py zhipu:glm-5.2
"""

import asyncio
import logging
import sys
from typing import Any, ClassVar

from hello_agents.model import (
    ChatResponse,
    Message,
    Tool,
    ToolCallBlock,
    execute_tool_calls,
)
from hello_agents.model.providers import build_model

# 控制台是 GBK：traceback 与 logging 走 stderr 时中文会乱码，统一导到 stdout。
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)

SPEC = "deepseek:deepseek-flash"
MAX_TURNS = 5
SYSTEM = "你在回答前必须先调用工具查证，不要凭记忆作答。"
WEATHER_PROMPT = "西安天气如何？"
BOTH_PROMPT = "西安天气如何？顺便帮我算一下 12 * 34 等于多少。"


class GetWeather(Tool):
    """假数据天气工具——demo 要演示的是闭环，不是天气服务。"""

    name = "get_weather"
    description = "查询某城市当前天气"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名，如 Xian"}},
        "required": ["city"],
    }

    async def run(self, arguments: dict[str, Any]) -> object:
        fake = {
            "Xian": {"temp": 22, "desc": "晴"},
            "Beijing": {"temp": 18, "desc": "多云"},
        }
        return fake.get(arguments["city"], {"temp": None, "desc": "未知城市"})


class Calculator(Tool):
    name = "calculator"
    description = "计算两个数的加减乘除"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
            "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
        },
        "required": ["a", "b", "op"],
    }

    async def run(self, arguments: dict[str, Any]) -> object:
        a, b, op = arguments["a"], arguments["b"], arguments["op"]
        table = {"add": a + b, "sub": a - b, "mul": a * b, "div": a / b}
        return {"result": table[op]}


def _tools() -> dict[str, Tool]:
    return {"get_weather": GetWeather(), "calculator": Calculator()}


def _text(response: ChatResponse) -> str:
    """把响应里的正文块拼起来（思考块与工具块不算）。"""
    return "".join(b.text for b in response.content if b.type == "text")


def _report_calls(calls: list[ToolCallBlock]) -> None:
    for call in calls:
        print(f"    -> {call.name}({call.arguments})")


async def _report_results(calls, tools) -> list[Message]:
    """执行调用并打印每个结果，返回要回灌的消息。"""
    results = await execute_tool_calls(calls, tools)
    for message in results:
        block = message.content[0]
        print(f"    <- {block.name}: {block.output}  is_error={block.is_error}")
    return results


async def part1_single_tool(spec: str) -> None:
    """非流式闭环：tool_calls -> tool -> stop。"""
    model = build_model(spec)
    tools = _tools()
    messages = [Message.system(SYSTEM), Message.user(WEATHER_PROMPT)]

    for turn in range(1, MAX_TURNS + 1):
        response = await model(messages, tools=list(tools.values()), tool_choice="auto")
        messages.append(response.to_message())
        calls = response.get_tool_calls()
        print(
            f"  [round {turn}] finish={response.finished_reason.value} calls={len(calls)}"
        )

        if not calls:
            print(f"  answer: {_text(response)}")
            assert turn > 1, "第一轮就该调工具，否则这个 demo 没验证到闭环"
            return

        _report_calls(calls)
        messages.extend(await _report_results(calls, tools))

    raise RuntimeError(f"超过 {MAX_TURNS} 轮还没收敛")


async def part2_parallel_tools(spec: str) -> None:
    """并行两个工具：一次请求里两个调用，arguments 不能互相串。"""
    model = build_model(spec)
    tools = _tools()
    messages = [Message.system(SYSTEM), Message.user(BOTH_PROMPT)]

    for turn in range(1, MAX_TURNS + 1):
        response = await model(messages, tools=list(tools.values()), tool_choice="auto")
        messages.append(response.to_message())
        calls = response.get_tool_calls()
        print(
            f"  [round {turn}] finish={response.finished_reason.value} calls={len(calls)}"
        )

        if not calls:
            print(f"  answer: {_text(response)}")
            return

        _report_calls(calls)
        # 每个调用的 arguments 必须各自是完整合法 JSON——串了就会在这里炸。
        names = sorted(call.name for call in calls)
        print(f"  [check] 本轮调用了 {names}")
        messages.extend(await _report_results(calls, tools))

    raise RuntimeError(f"超过 {MAX_TURNS} 轮还没收敛")


async def part3_stream(spec: str) -> None:
    """流式 tool_calls：分片按 index 到达，收尾后走同一套判断。"""
    model = build_model(spec, stream=True)
    tools = _tools()
    messages = [Message.system(SYSTEM), Message.user(WEATHER_PROMPT)]

    for turn in range(1, MAX_TURNS + 1):
        final: ChatResponse | None = None
        async for part in await model(messages, tools=list(tools.values())):
            if part.is_last:
                final = part
                continue
            for block in part.content:
                if isinstance(block, ToolCallBlock):
                    print(f"    [frag] index={block.index} args={block.arguments!r}")
        assert final is not None, "流必须产出收尾的完整响应"

        messages.append(final.to_message())
        calls = final.get_tool_calls()
        print(
            f"  [round {turn}] finish={final.finished_reason.value} calls={len(calls)}"
        )

        if not calls:
            print(f"  answer: {_text(final)}")
            return

        _report_calls(calls)
        messages.extend(await _report_results(calls, tools))

    raise RuntimeError(f"超过 {MAX_TURNS} 轮还没收敛")


async def main(spec: str) -> None:
    print("=" * 60)
    print("[1] 非流式闭环：单工具")
    await part1_single_tool(spec)

    print("=" * 60)
    print("[2] 并行两个工具")
    await part2_parallel_tools(spec)

    print("=" * 60)
    print("[3] 流式 tool_calls")
    await part3_stream(spec)

    print("=" * 60)
    print("ALL PARTS DONE")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else SPEC))
