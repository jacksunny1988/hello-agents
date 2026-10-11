"""C2：`ChatModelBase.count_tokens` 的粗估口径（离线、确定性）。

口径 = 「会出站的字符串」按顺序拼接，UTF-8 字节数 ÷ 4 四舍五入。
本文件把这条口径**钉死**：改口径就是改契约，必须同步改这里与设计文档。
"""

import json
from typing import Any, ClassVar, cast

from openai import AsyncOpenAI

from hello_agents.model import (
    ChatModelBase,
    Message,
    Provider,
    Role,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolCallBlock,
    ToolResultBlock,
    get_model_config,
)


class _StubModel(ChatModelBase):
    """只为调用 `count_tokens` 而生：真发请求就该报错。"""

    async def _call_api(
        self, messages, stream, tools=None, tool_choice=None, response_format=None
    ):
        raise AssertionError("count_tokens 不应触达 _call_api")


class _EchoTool(Tool):
    name = "echo"
    description = "回显文本"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, arguments: dict) -> str:
        return str(arguments.get("text", ""))


class _OverriddenModel(_StubModel):
    """验证「换更准的 tokenizer」这个扩展点确实生效。"""

    async def count_tokens(self, messages, tools=None) -> int:
        return 12345


def _model() -> ChatModelBase:
    return _StubModel(
        config=get_model_config(Provider.DEEPSEEK),
        client=cast(AsyncOpenAI, None),
    )


def _assistant(*blocks) -> Message:
    return Message(role=Role.ASSISTANT, content=list(blocks))


def _estimate(*chunks: str) -> int:
    """按实现同一条口径独立复算期望值：UTF-8 字节数 ÷ 4 四舍五入。"""
    return int(len("".join(chunks).encode("utf-8")) / 4 + 0.5)


async def test_empty_input_is_zero() -> None:
    assert await _model().count_tokens([]) == _estimate() == 0


async def test_formula_is_pinned_by_hand_computation() -> None:
    model = _model()

    # "user" + "abcd" = 8 字节 → 8/4 + 0.5 = 2.5 → int → 2
    assert _estimate("user", "abcd") == 2
    assert await model.count_tokens([Message.user("abcd")]) == 2

    # "user" + "中文" = 4 + 6 = 10 字节 → 10/4 + 0.5 = 3.0 → 3
    assert _estimate("user", "中文") == 3
    assert await model.count_tokens([Message.user("中文")]) == 3


async def test_thinking_block_is_not_counted() -> None:
    """思考块出站时会被 formatter 丢弃，因此不该计入估算。"""
    model = _model()

    plain = [_assistant(TextBlock(text="答案"))]
    with_thinking = [
        _assistant(
            ThinkingBlock(thinking="很长的一段思考" * 100),
            TextBlock(text="答案"),
        )
    ]

    # 加了一整块思考文本，估算一字不加。
    assert await model.count_tokens(with_thinking) == await model.count_tokens(plain)
    # 唯一起作用的正文是 "答案"。
    assert await model.count_tokens(plain) == _estimate("assistant", "答案")


async def test_tool_call_and_result_are_counted() -> None:
    model = _model()
    base = [_assistant(TextBlock(text="读文件"))]
    call_arguments = '{"path": "a.txt"}'
    with_call = [
        _assistant(
            TextBlock(text="读文件"),
            ToolCallBlock(name="read_file", arguments=call_arguments, id="c1"),
        )
    ]
    with_result = [
        *with_call,
        Message(
            role=Role.TOOL,
            content=[ToolResultBlock(tool_call_id="c1", output="内容" * 10)],
        ),
    ]

    assert await model.count_tokens(base) == _estimate("assistant", "读文件")
    assert await model.count_tokens(with_call) == _estimate(
        "assistant", "读文件", "read_file", call_arguments
    )
    assert await model.count_tokens(with_result) == _estimate(
        "assistant", "读文件", "read_file", call_arguments, "tool", "内容" * 10
    )


async def test_tools_schema_is_counted() -> None:
    model = _model()
    messages = [Message.user("hi")]

    without = await model.count_tokens(messages)
    with_tools = await model.count_tokens(messages, [_EchoTool()])

    # 与出站同一个函数产出 schema；中文描述按 UTF-8 字节计入。
    tools_json = json.dumps([_EchoTool().function_spec()], ensure_ascii=False)

    assert without == _estimate("user", "hi")
    assert with_tools == _estimate("user", "hi", tools_json)
    assert with_tools > without


async def test_adding_messages_never_decreases_the_estimate() -> None:
    model = _model()
    messages = [Message.user("你好")]
    previous = await model.count_tokens(messages)

    for index in range(5):
        messages.append(_assistant(TextBlock(text=f"第 {index} 条回复")))
        current = await model.count_tokens(messages)
        # 粗估要四舍五入，偶有相等；但绝不允许回退。
        assert current >= previous
        previous = current


async def test_count_tokens_is_overridable() -> None:
    model = _OverriddenModel(
        config=get_model_config(Provider.DEEPSEEK),
        client=cast(AsyncOpenAI, None),
    )

    assert await model.count_tokens([Message.user("x")]) == 12345
