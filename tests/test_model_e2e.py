"""模型层端到端测试（真实网络）。

与 `tests/test_model_*.py` 的分工：

- 那些是**离线**测试——手写假模型，覆盖分支、边界、错误路径，亚秒级跑完；
- 这个是**端到端**——真的发请求，验证「厂商 SDK → 模型层 → 上层」这条链在真实
  协议下确实成立。手写的 fake 一旦与厂商实际行为漂移，只有这里能发现
  （M4 的 usage 载体帧、M6 的 tool_calls index 分片，都是这种漂移）。

默认**不跑**，因为真实请求会产生费用。显式打开：

    RUN_MODEL_E2E=1 ./.venv/Scripts/python.exe -m pytest tests/test_model_e2e.py -v

换 provider：

    RUN_MODEL_E2E=1 MODEL_E2E_SPECS="deepseek:deepseek-flash,zhipu:glm-5.2" ...

写这类测试的两条纪律（不遵守就会得到一个又贵又会随机变红的测试）：

1. **只断言契约，不断言措辞**。`finish_reason` / `usage` / 块的形状 / id 配对是
   契约；「模型回答里有没有『22』」不是——那是模型自由，写死了就是给自己埋雷。
2. **要确定性，不要赌模型**。

已知的厂商约束（本文件实跑发现，写下来省得下次再撞）：

- **DeepSeek 的思考模式不接受 `tool_choice="required"`，也不接受强制指定函数的
  形式**，两者都回 `400 Thinking mode does not support this tool_choice`。
  它只吃 `auto` / `none` / 不传。所以下面靠**系统提示词**把模型逼进工具调用，
  而不是靠 `required`——实测 `auto` + 强提示在流式与非流式下都稳定触发。
  （`ModelConfig` 目前没有能表达这条差异的能力位，规格 §1.1 也把它当成通用能力；
  要不要补一个 `supports_forced_tool_choice` 是另一个决定。）
"""

import json
import os
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from hello_agents.model import (
    ChatResponse,
    FinishedReason,
    Message,
    Provider,
    Role,
    StructuredStats,
    TextBlock,
    Tool,
    ToolCallBlock,
    execute_tool_calls,
    extract_structured_with_stats,
    get_model_config,
)
from hello_agents.model.providers import build_model

DEFAULT_SPEC = "deepseek:deepseek-flash"

# 逗号分隔可以一次跑多家：MODEL_E2E_SPECS="deepseek:...,zhipu:...,dashscope:..."
SPECS = [
    s.strip()
    for s in os.environ.get("MODEL_E2E_SPECS", DEFAULT_SPEC).split(",")
    if s.strip()
]

SYSTEM = (
    "你不知道任何实时信息。凡涉及天气、温度的问题，你必须先调用 get_weather 工具，"
    "不得凭记忆作答，也不得直接回答。"
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_MODEL_E2E") != "1",
        reason="端到端测试会发真实请求并产生费用；设 RUN_MODEL_E2E=1 显式打开",
    ),
]


@pytest.fixture(params=SPECS, ids=lambda s: s.replace(":", "-"))
def spec(request) -> str:
    return request.param


class _GetWeather(Tool):
    """假数据工具：E2E 要验证的是闭环，不是天气服务。

    记录每次收到的参数，好断言「工具真的被调用过、且参数是模型给的」。
    """

    name = "get_weather"
    description = "查询某城市当前天气"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名，如 Xian"}},
        "required": ["city"],
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, arguments: dict[str, Any]) -> object:
        self.calls.append(arguments)
        return {"city": arguments.get("city"), "temp_c": 22, "desc": "晴"}


def _text(response: ChatResponse) -> str:
    """响应里的正文（思考块与工具块不算）。"""
    return "".join(b.text for b in response.content if isinstance(b, TextBlock))


# --- 基础调用（M3）---


@pytest.mark.asyncio
async def test_non_streaming_call_returns_contract_shaped_response(spec):
    model = build_model(spec)

    response = await model([Message.user("只回复两个字：好的")])

    assert response.is_last is True
    assert response.finished_reason is FinishedReason.COMPLETED
    assert response.id, "非流式响应应当带上厂商给的 id"
    assert _text(response), "应当有正文块"

    assert response.usage is not None
    assert response.usage.input_tokens > 0
    assert response.usage.output_tokens > 0
    assert response.usage.time > 0, "非流式耗时应由调用方计时写入"


# --- 流式聚合（M4）---


@pytest.mark.asyncio
async def test_streaming_deltas_assemble_into_the_final_response(spec):
    """流式聚合的核心契约：逐片产出的正文拼起来 == 收尾响应里的正文。

    这条不依赖模型说什么，只依赖聚合正确——正是 M4 最容易写错的地方。
    """
    model = build_model(spec, stream=True)

    parts = [p async for p in await model([Message.user("从一数到五，只输出数字")])]

    assert parts[-1].is_last is True, "最后一片必须是完整响应"
    deltas = parts[:-1]
    assert deltas, "应当至少产出一片增量"
    assert all(p.is_last is False for p in deltas)

    streamed = "".join(_text(p) for p in deltas)
    final_text = _text(parts[-1])

    assert final_text, "收尾响应应当有正文"
    assert streamed == final_text, "增量拼起来必须等于收尾响应的正文"

    assert parts[-1].usage is not None
    assert parts[-1].usage.time > 0, "总耗时应由驱动流的基类在收尾时补上"


# --- 工具调用闭环（M6）---


@pytest.mark.asyncio
async def test_tool_loop_reaches_a_final_answer(spec):
    """完整闭环：调工具 → 执行 → 回灌 → 模型收尾。

    用 `auto` 而不是 `required`，因为 DeepSeek 思考模式不支持 `required`（见模块
    docstring）。确定性靠系统提示词兜住：实测稳定触发。**若这条哪天开始随机失败，
    正确做法是把提示词写得更死，而不是把断言放宽**——放宽了它就什么也证明不了。
    """
    model = build_model(spec)
    weather = _GetWeather()
    # 注意两种形状：`model(tools=...)` 要序列，`execute_tool_calls(calls, tools)` 要映射
    tool_list = [weather]
    tool_map = {weather.name: weather}
    messages = [Message.system(SYSTEM), Message.user("西安现在多少度？")]

    first = await model(messages, tools=tool_list, tool_choice="auto")
    messages.append(first.to_message())
    calls = first.get_tool_calls()

    assert calls, "强提示下模型应当请求工具"
    assert first.finished_reason is FinishedReason.TOOL_CALLS
    assert weather.name in {c.name for c in calls}
    for call in calls:
        json.loads(call.arguments)  # 不是合法 JSON 会在这里炸

    results = await execute_tool_calls(calls, tool_map)
    assert weather.calls, "工具真的被执行了"
    assert all(not m.content[0].is_error for m in results), "假数据工具不该失败"
    messages.extend(results)

    # 之后放开，让模型自己决定何时收尾
    for _ in range(3):
        response = await model(messages, tools=tool_list, tool_choice="auto")
        messages.append(response.to_message())
        if not response.get_tool_calls():
            break
        messages.extend(await execute_tool_calls(response.get_tool_calls(), tool_map))
    else:
        pytest.fail("三轮之内没有收敛到最终答案")

    assert response.finished_reason is FinishedReason.COMPLETED
    assert _text(response), "最终答案应当是正文"

    # assistant(tool_calls) 与 role=tool 结果按 id ↔ tool_call_id 配对
    tool_msg = next(m for m in messages if m.role is Role.TOOL)
    assert tool_msg.content[0].tool_call_id == calls[0].id
    assert messages[-1].role is Role.ASSISTANT


@pytest.mark.asyncio
async def test_streaming_tool_calls_assemble_into_valid_json(spec):
    """流式分片按 index 归位后，arguments 必须是完整合法 JSON。

    M6 之前最容易错的地方：并行多路分片交错到达，串了就拼不出合法 JSON。
    """
    model = build_model(spec, stream=True)
    weather = _GetWeather()
    tools = [weather]
    messages = [Message.system(SYSTEM), Message.user("西安现在多少度？")]

    final: ChatResponse | None = None
    fragments: list[ToolCallBlock] = []
    async for part in await model(messages, tools=tools, tool_choice="auto"):
        if part.is_last:
            final = part
        else:
            fragments.extend(b for b in part.content if isinstance(b, ToolCallBlock))

    assert final is not None, "流必须产出收尾的完整响应"
    assert final.finished_reason is FinishedReason.TOOL_CALLS

    calls = final.get_tool_calls()
    assert calls, "这一轮应当是在请求工具"
    assert weather.name in {c.name for c in calls}

    for call in calls:
        assert call.id, "收尾的 tool_call 必须带上厂商给的 id"
        assert json.loads(call.arguments), "分片拼出来的 arguments 必须是合法 JSON"

    # 分片是真实到达的（不是厂商一次性给全）——否则这个用例测不到拼接逻辑
    if fragments:
        assert len(fragments) >= len(calls), "每路调用至少一片"


@pytest.mark.asyncio
async def test_tool_choice_none_suppresses_tool_calls(spec):
    """`tool_choice="none"` 必须真的抑制工具调用——规格 §6 自检清单第 7 条。"""
    model = build_model(spec)
    weather = _GetWeather()

    response = await model(
        [Message.system(SYSTEM), Message.user("西安现在多少度？")],
        tools=[weather],
        tool_choice="none",
    )

    assert response.get_tool_calls() == []
    assert not weather.calls, "工具不该被执行"
    assert _text(response), "被抑制后应当直接给正文"


# --- 结构化输出（M7）---


class _Person(BaseModel):
    """M7 的目标 schema：与规格 §7 一致。"""

    name: str
    age: int
    city: str


async def _extract(spec: str, mode: str) -> tuple[_Person, StructuredStats]:
    model = build_model(spec)
    return await extract_structured_with_stats(
        model,
        [Message.user("从这句话里抽取人物信息：老张今年三十岁，住在西安。")],
        _Person,
        mode=mode,
    )


@pytest.mark.asyncio
async def test_structured_tool_mode_returns_a_validated_object(spec):
    """只断言契约：类型对、字段齐、确实发生了至少一次请求。"""
    person, stats = await _extract(spec, "tool")

    assert isinstance(person, _Person)
    assert person.name and person.city
    assert stats.attempts, "至少要发过一次请求"


@pytest.mark.asyncio
async def test_structured_json_object_mode_returns_a_validated_object(spec):
    person, _ = await _extract(spec, "json_object")

    assert isinstance(person, _Person)
    assert person.name and person.city


@pytest.mark.asyncio
async def test_structured_json_schema_mode_when_the_model_supports_it(spec):
    """能力位为 False 时跳过——`extract_structured` 自己会抛错，那是离线测过的事。"""
    provider = Provider(spec.split(":", 1)[0])
    if not get_model_config(provider).supports_native_json_schema:
        pytest.skip(
            f"{spec} 的 supports_native_json_schema=False（先跑 model_m7_probe）"
        )

    person, _ = await _extract(spec, "json_schema")

    assert isinstance(person, _Person)
    assert person.name and person.city
