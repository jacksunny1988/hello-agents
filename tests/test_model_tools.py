"""M6 工具调用闭环：出站/入站转换、流式拼接、执行回灌。全部离线，不发网络请求"""

import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any, ClassVar, cast

import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice as CompletionChoice
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
)
from openai.types.chat.chat_completion_chunk import (
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from hello_agents.model import (
    ChatModelBase,
    ChatResponse,
    FinishedReason,
    Message,
    Provider,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    get_model_config,
)
from hello_agents.model._formatter import (
    from_completion,
    parse_chunk,
    to_openai_messages,
    to_openai_tools,
)
from hello_agents.model._tool import Tool, execute_tool_calls
from hello_agents.model.providers import OpenAICompatModel


class _EchoTool(Tool):
    """最小工具：把收到的参数原样回显。"""

    name = "echo"
    description = "回显参数"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }

    async def run(self, arguments: dict[str, Any]) -> object:
        return arguments


# --- 出站 Formatter ---


def test_function_spec_shape():
    """function_spec 产出规格 §1.1 的形状，子类不用自己拼。"""
    assert _EchoTool().function_spec() == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "回显参数",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        },
    }


def test_to_openai_tools_maps_each_tool():
    assert to_openai_tools([_EchoTool()]) == [_EchoTool().function_spec()]


def test_assistant_tool_calls_become_tool_calls_field():
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            ToolCallBlock(id="call_1", name="get_weather", arguments='{"city":"Xian"}')
        ],
    )

    assert to_openai_messages([msg]) == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city":"Xian"}',
                    },
                }
            ],
        }
    ]


def test_assistant_text_alongside_tool_calls_is_kept():
    """模型可能「先说一句再调工具」，正文不能丢。"""
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="我先查一下"),
            ToolCallBlock(id="call_1", name="get_weather", arguments="{}"),
        ],
    )

    out = to_openai_messages([msg])[0]

    assert out["content"] == "我先查一下"
    assert out["tool_calls"][0]["id"] == "call_1"


def test_assistant_thinking_is_still_ignored():
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            ThinkingBlock(thinking="想想"),
            ToolCallBlock(id="c", name="t", arguments="{}"),
        ],
    )

    out = to_openai_messages([msg])[0]

    assert out["content"] is None
    assert len(out["tool_calls"]) == 1


def test_tool_message_expands_one_dict_per_result():
    """同一条 Message 里多个结果要展开成多条 role=tool dict（规格 §3.2）。"""
    msg = Message(
        role=Role.TOOL,
        content=[
            ToolResultBlock(tool_call_id="call_1", output="22", name="get_weather"),
            ToolResultBlock(
                tool_call_id="call_2", output="boom", is_error=True, name="get_time"
            ),
        ],
    )

    assert to_openai_messages([msg]) == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "22",
            "name": "get_weather",
        },
        {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": "boom",
            "name": "get_time",
        },
    ]


def test_tool_message_omits_name_when_absent():
    """name 为 None 时不写这个 key，避免给严格校验的端点发 null。"""
    msg = Message(
        role=Role.TOOL, content=[ToolResultBlock(tool_call_id="c", output="ok")]
    )

    assert to_openai_messages([msg]) == [
        {"role": "tool", "tool_call_id": "c", "content": "ok"}
    ]


def test_tool_call_in_user_message_raises():
    msg = Message(
        role=Role.USER, content=[ToolCallBlock(id="c", name="t", arguments="{}")]
    )

    with pytest.raises(ValueError, match="role=assistant"):
        to_openai_messages([msg])


def test_tool_result_in_assistant_message_raises():
    msg = Message(
        role=Role.ASSISTANT, content=[ToolResultBlock(tool_call_id="c", output="ok")]
    )

    with pytest.raises(ValueError, match="role=tool"):
        to_openai_messages([msg])


def test_tool_role_message_without_result_raises():
    msg = Message(role=Role.TOOL, content=[TextBlock(text="不是结果")])

    with pytest.raises(ValueError, match="至少要有一个"):
        to_openai_messages([msg])


# --- 入站 Formatter ---


def _delta_tool_call(
    index: int, *, id: str | None = None, name: str | None = None, arguments: str = ""
) -> ChoiceDeltaToolCall:
    """流式的一小片 tool_call。续片只有 index + arguments，没有 id/name。"""
    return ChoiceDeltaToolCall(
        index=index,
        id=id,
        type="function",
        function=ChoiceDeltaToolCallFunction(name=name, arguments=arguments),
    )


def _tool_chunk(
    tool_calls: list[ChoiceDeltaToolCall], *, finish_reason: str | None = None
) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chunk_1",
        choices=[
            ChunkChoice(
                index=0,
                delta=ChoiceDelta(tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        created=1,
        model="m",
        object="chat.completion.chunk",
    )


def _text_chunk(text: str, *, finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chunk_t",
        choices=[
            ChunkChoice(
                index=0, delta=ChoiceDelta(content=text), finish_reason=finish_reason
            )
        ],
        created=1,
        model="m",
        object="chat.completion.chunk",
    )


def _carrier_chunk() -> ChatCompletionChunk:
    """usage 载体帧：choices 为空、只带 usage。dashscope 在末片**之后**发它。"""
    return ChatCompletionChunk(
        id="chunk_u",
        choices=[],
        created=1,
        model="m",
        object="chat.completion.chunk",
        usage=CompletionUsage(completion_tokens=9, prompt_tokens=7, total_tokens=16),
    )


def test_from_completion_reads_tool_calls_and_finish_reason():
    tool_call = ChatCompletionMessageToolCall(
        id="call_1",
        type="function",
        function=Function(name="get_weather", arguments='{"city":"Xian"}'),
    )
    completion = ChatCompletion(
        id="x",
        choices=[
            CompletionChoice(
                index=0,
                finish_reason="tool_calls",
                message=ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
            )
        ],
        created=1,
        model="m",
        object="chat.completion",
    )

    response = from_completion(completion, 0.1)

    assert response.finished_reason is FinishedReason.TOOL_CALLS
    assert len(response.content) == 1
    block = response.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name, block.arguments) == (
        "call_1",
        "get_weather",
        '{"city":"Xian"}',
    )
    assert block.index is None  # 非流式没有 index（SDK 类型里就没这个字段）


def test_from_completion_stop_maps_to_completed():
    completion = ChatCompletion(
        id="x",
        choices=[
            CompletionChoice(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content="你好"),
            )
        ],
        created=1,
        model="m",
        object="chat.completion",
    )

    assert from_completion(completion, 0.1).finished_reason is FinishedReason.COMPLETED


def test_parse_chunk_reads_first_fragment():
    delta = parse_chunk(
        _tool_chunk([_delta_tool_call(0, id="call_1", name="get_weather")])
    )

    block = delta.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name, block.arguments, block.index) == (
        "call_1",
        "get_weather",
        "",
        0,
    )


def test_parse_chunk_continuation_fragment_has_empty_id_and_name():
    """续片没有 id/name —— 用空串（匿名），靠 index 归位。"""
    delta = parse_chunk(_tool_chunk([_delta_tool_call(0, arguments='{"city')]))

    block = delta.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name) == ("", "")
    assert (block.arguments, block.index) == ('{"city', 0)


def test_parse_chunk_maps_finish_reason():
    delta = parse_chunk(_tool_chunk([], finish_reason="tool_calls"))

    assert delta.finished_reason is FinishedReason.TOOL_CALLS
    assert delta.content == []


# --- 模型层接口透传 ---


async def _stream_of(deltas: Sequence[ChatResponse]) -> AsyncGenerator[ChatResponse]:
    """把一串增量帧包成异步生成器。"""
    for delta in deltas:
        yield delta


class _ScriptedModel(ChatModelBase):
    """按脚本逐个返回响应的假模型，并记录每次 `_call_api` 收到的参数。

    脚本元素：`ChatResponse`（非流式）或 `list[ChatResponse]`（流式的增量序列）。
    """

    def __init__(self, script, **kwargs):
        super().__init__(
            config=get_model_config(Provider.DEEPSEEK),
            client=cast(AsyncOpenAI, None),
            **kwargs,
        )
        self.script = list(script)
        self.seen: list[dict] = []

    async def _call_api(
        self, messages, stream, tools=None, tool_choice=None, response_format=None
    ):
        self.seen.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        item = self.script.pop(0) if self.script else ChatResponse(content=[])
        if stream:
            return _stream_of(item if isinstance(item, list) else [item])
        return item


class _FakeCompletions:
    """记录 create() 收到的 kwargs，返回一个空 completion。"""

    def __init__(self):
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return ChatCompletion(
            id="x", choices=[], created=1, model="m", object="chat.completion"
        )


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def _fake_model(completions: _FakeCompletions) -> OpenAICompatModel:
    return OpenAICompatModel(
        get_model_config(Provider.DEEPSEEK),
        cast(AsyncOpenAI, _FakeClient(completions)),
    )


@pytest.mark.asyncio
async def test_tools_and_tool_choice_reach_call_api():
    """tools / tool_choice 透传到 _call_api，基类不做解释。"""
    model = _ScriptedModel([])
    tools = [_EchoTool()]

    await model([Message.user("hi")], tools=tools, tool_choice="required")

    assert model.seen[0]["tools"] == tools
    assert model.seen[0]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_tools_default_to_none():
    model = _ScriptedModel([])

    await model([Message.user("hi")])

    assert model.seen[0]["tools"] is None
    assert model.seen[0]["tool_choice"] is None


@pytest.mark.asyncio
async def test_streaming_forwards_tools_too():
    model = _ScriptedModel([[]], stream=True)

    async for _ in await model(
        [Message.user("hi")], tools=[_EchoTool()], tool_choice="auto"
    ):
        pass

    assert model.seen[0]["stream"] is True
    assert model.seen[0]["tools"] is not None


@pytest.mark.asyncio
async def test_openai_compat_omits_tools_key_when_not_given():
    """未传 tools 时请求里根本不出现这个 key。"""
    completions = _FakeCompletions()

    await _fake_model(completions)([Message.user("hi")])

    assert "tools" not in completions.kwargs[0]
    assert "tool_choice" not in completions.kwargs[0]


@pytest.mark.asyncio
async def test_openai_compat_omits_tools_key_for_empty_list():
    """空列表同样不发——发一个空数组给端点是另一种语义。"""
    completions = _FakeCompletions()

    await _fake_model(completions)([Message.user("hi")], tools=[])

    assert "tools" not in completions.kwargs[0]


@pytest.mark.asyncio
async def test_openai_compat_sends_tools_when_given():
    completions = _FakeCompletions()

    await _fake_model(completions)(
        [Message.user("hi")], tools=[_EchoTool()], tool_choice="required"
    )

    assert completions.kwargs[0]["tools"] == [_EchoTool().function_spec()]
    assert completions.kwargs[0]["tool_choice"] == "required"


# --- 累加器 ---


def _tool_chunk_delta(*, finish_reason: str | None = None) -> ChatResponse:
    """一个 content 为空、只带 finish_reason 的增量（末片）。"""
    return parse_chunk(_tool_chunk([], finish_reason=finish_reason))


def test_parallel_stream_fragments_are_aligned_by_index():
    """规格 §2 那个多片例子：并行两路 arguments 不能互相串。"""
    acc = ChatResponse(content=[], is_last=True)

    # 片1：index=0 给出 id 与 name
    acc.append_chat_response(
        parse_chunk(_tool_chunk([_delta_tool_call(0, id="call_1", name="get_weather")]))
    )
    # 片2：index=0 的 arguments 增量
    acc.append_chat_response(
        parse_chunk(_tool_chunk([_delta_tool_call(0, arguments='{"city')]))
    )
    # 片3：index=0 继续，同时 index=1 开始第二个并行调用
    acc.append_chat_response(
        parse_chunk(
            _tool_chunk(
                [
                    _delta_tool_call(0, arguments='":"Xian"}'),
                    _delta_tool_call(1, id="call_2", name="get_time"),
                ]
            )
        )
    )
    # 片4：index=1 的 arguments
    acc.append_chat_response(
        parse_chunk(_tool_chunk([_delta_tool_call(1, arguments='{"tz":"CST"}')]))
    )
    # 末片：finish_reason
    acc.append_chat_response(_tool_chunk_delta(finish_reason="tool_calls"))

    calls = acc.get_tool_calls()

    assert [c.id for c in calls] == ["call_1", "call_2"]
    assert [c.name for c in calls] == ["get_weather", "get_time"]
    assert [c.arguments for c in calls] == ['{"city":"Xian"}', '{"tz":"CST"}']
    assert acc.finished_reason is FinishedReason.TOOL_CALLS


def test_usage_carrier_after_finish_reason_does_not_reset_it():
    """载体帧在末片**之后**到达（dashscope），不能把 TOOL_CALLS 冲回 COMPLETED。"""
    acc = ChatResponse(content=[], is_last=True)

    acc.append_chat_response(_tool_chunk_delta(finish_reason="tool_calls"))
    acc.append_chat_response(parse_chunk(_carrier_chunk()))

    assert acc.finished_reason is FinishedReason.TOOL_CALLS
    assert acc.usage is not None


def test_tool_calls_without_index_merge_by_id():
    """非流式路径没有 index，按 id 合并。"""
    acc = ChatResponse(content=[], is_last=True)

    acc.append_chat_response(
        ChatResponse(
            content=[ToolCallBlock(id="c1", name="t", arguments='{"a"')], is_last=False
        )
    )
    acc.append_chat_response(
        ChatResponse(
            content=[ToolCallBlock(id="c1", name="", arguments=":1}")], is_last=False
        )
    )

    calls = acc.get_tool_calls()
    assert len(calls) == 1
    assert calls[0].arguments == '{"a":1}'
    assert calls[0].name == "t"  # 后片的空 name 不能把已有的名字冲掉


def test_tool_result_block_in_a_response_still_raises():
    """工具结果出现在响应方向是编程错误，不是「M6 待实现」。

    `ChatResponse.content` 的联合类型在**构造时**就挡住了 ToolResultBlock，
    所以正常路径到不了这个分支。这里用 `model_construct` 造一个类型上非法的对象，
    钉住「宁可报错也不静默丢弃」这条兜底。
    """
    acc = ChatResponse(content=[], is_last=True)
    invalid = ChatResponse.model_construct(
        content=[ToolResultBlock(tool_call_id="c", output="ok")], is_last=False
    )

    with pytest.raises(NotImplementedError, match="ToolResultBlock"):
        acc.append_chat_response(invalid)


def test_to_message_keeps_blocks_and_role():
    response = ChatResponse(
        content=[
            TextBlock(text="我先查一下"),
            ToolCallBlock(id="call_1", name="get_weather", arguments="{}"),
        ]
    )

    msg = response.to_message()

    assert msg.role is Role.ASSISTANT
    assert [type(b).__name__ for b in msg.content] == ["TextBlock", "ToolCallBlock"]


@pytest.mark.asyncio
async def test_stream_final_reason_is_tool_calls():
    """收尾对象不能把末片写进去的 TOOL_CALLS 冲回 COMPLETED。"""
    model = _ScriptedModel(
        [[_tool_chunk_delta(finish_reason="tool_calls")]], stream=True
    )

    parts = [p async for p in await model([Message.user("hi")])]

    assert parts[-1].is_last is True
    assert parts[-1].finished_reason is FinishedReason.TOOL_CALLS


@pytest.mark.asyncio
async def test_stream_final_reason_stays_completed_without_finish_frame():
    """没有 finish 帧时仍是 COMPLETED（守住 M5 的既有行为）。"""
    model = _ScriptedModel([[parse_chunk(_text_chunk("你"))]], stream=True)

    parts = [p async for p in await model([Message.user("hi")])]

    assert parts[-1].finished_reason is FinishedReason.COMPLETED


# --- execute_tool_calls ---


class _BoomTool(Tool):
    name = "boom"
    description = "总是抛错"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> object:
        raise RuntimeError("炸了")


class _StrTool(Tool):
    name = "as_text"
    description = "返回字符串"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> object:
        return "已经是字符串"


def _call(id: str, name: str, arguments: str = "{}") -> ToolCallBlock:
    return ToolCallBlock(id=id, name=name, arguments=arguments)


@pytest.mark.asyncio
async def test_success_result_is_json_dumped():
    msgs = await execute_tool_calls(
        [_call("c1", "echo", '{"city":"Xian"}')], {"echo": _EchoTool()}
    )

    assert len(msgs) == 1
    assert msgs[0].role is Role.TOOL
    block = msgs[0].content[0]
    assert block.is_error is False
    assert json.loads(block.output) == {"city": "Xian"}
    assert block.tool_call_id == "c1"
    assert block.name == "echo"


@pytest.mark.asyncio
async def test_string_result_is_used_as_is():
    msgs = await execute_tool_calls([_call("c1", "as_text")], {"as_text": _StrTool()})

    assert msgs[0].content[0].output == "已经是字符串"


@pytest.mark.asyncio
async def test_empty_tool_calls_returns_empty_list():
    assert await execute_tool_calls([], {}) == []


@pytest.mark.asyncio
async def test_unknown_tool_becomes_error_result():
    msgs = await execute_tool_calls([_call("c1", "nope")], {})

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "nope" in block.output
    assert block.tool_call_id == "c1"


@pytest.mark.asyncio
async def test_empty_arguments_string_becomes_error_result():
    """`json.loads("")` 抛 JSONDecodeError——必须转成 is_error，不能让闭环崩掉。"""
    msgs = await execute_tool_calls([_call("c1", "echo", "")], {"echo": _EchoTool()})

    assert msgs[0].content[0].is_error is True


@pytest.mark.asyncio
async def test_non_object_arguments_becomes_error_result():
    msgs = await execute_tool_calls(
        [_call("c1", "echo", '["不是对象"]')], {"echo": _EchoTool()}
    )

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "JSON 对象" in block.output


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result():
    msgs = await execute_tool_calls([_call("c1", "boom")], {"boom": _BoomTool()})

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "RuntimeError" in block.output
    assert "炸了" in block.output


@pytest.mark.asyncio
async def test_unserializable_result_becomes_error_result():
    """工具返回不可 JSON 序列化的对象时，不能把 TypeError 冒到闭环外。"""

    class _BadTool(Tool):
        name = "bad"
        description = "返回不可序列化对象"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

        async def run(self, arguments: dict[str, Any]) -> object:
            return object()

    msgs = await execute_tool_calls([_call("c1", "bad")], {"bad": _BadTool()})

    assert msgs[0].content[0].is_error is True


@pytest.mark.asyncio
async def test_results_keep_input_order():
    """并发执行，但返回顺序与入参一致——id ↔ tool_call_id 才能正确配对。"""
    calls = [
        _call("c1", "echo", '{"n":1}'),
        _call("c2", "echo", '{"n":2}'),
        _call("c3", "echo", '{"n":3}'),
    ]

    msgs = await execute_tool_calls(calls, {"echo": _EchoTool()})

    assert [m.content[0].tool_call_id for m in msgs] == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_tool_calls_run_concurrently():
    """并行工具是并发执行的——串行实现会让 peak 停在 1。"""
    running = 0
    peak = 0

    class _GatedTool(Tool):
        name = "gated"
        description = "每个调用都让出一次控制权"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

        async def run(self, arguments: dict[str, Any]) -> object:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1
            return "ok"

    calls = [_call(f"c{i}", "gated") for i in range(3)]

    await execute_tool_calls(calls, {"gated": _GatedTool()})

    assert peak == 3


@pytest.mark.asyncio
async def test_deeply_nested_arguments_becomes_error_result():
    """`json.loads` 对超深嵌套会抛 RecursionError，同样要被兜住。"""
    msgs = await execute_tool_calls(
        [_call("c1", "echo", "[" * 3000 + "]" * 3000)], {"echo": _EchoTool()}
    )

    assert msgs[0].content[0].is_error is True


@pytest.mark.asyncio
async def test_deeply_nested_result_becomes_error_result():
    """`json.dumps` 同理——工具返回超深嵌套对象也不能把整批炸掉。"""

    class _DeepTool(Tool):
        name = "deep"
        description = "返回超深嵌套列表"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

        async def run(self, arguments: dict[str, Any]) -> object:
            deep: object = []
            for _ in range(3000):
                deep = [deep]
            return deep

    msgs = await execute_tool_calls([_call("c1", "deep")], {"deep": _DeepTool()})

    assert msgs[0].content[0].is_error is True


def test_tool_protocol_is_exported_from_package():
    from hello_agents.model import Tool as ExportedTool
    from hello_agents.model import execute_tool_calls as exported_execute

    assert ExportedTool is Tool
    assert exported_execute is execute_tool_calls


# --- 离线闭环（规格 §3.6 的主循环）---


@pytest.mark.asyncio
async def test_offline_tool_loop_reaches_final_answer():
    """假模型按脚本先返回 tool_calls、再返回 stop，验证多轮配对与闭环。"""
    model = _ScriptedModel(
        [
            ChatResponse(
                content=[
                    ToolCallBlock(id="call_1", name="echo", arguments='{"city":"Xian"}')
                ],
                finished_reason=FinishedReason.TOOL_CALLS,
            ),
            ChatResponse(content=[TextBlock(text="西安 22 度")]),
        ]
    )
    tools = {"echo": _EchoTool()}

    messages = [Message.system("你是助手"), Message.user("西安天气如何？")]
    while True:
        response = await model(messages, tools=list(tools.values()), tool_choice="auto")
        messages.append(response.to_message())
        calls = response.get_tool_calls()
        if not calls:
            break
        messages.extend(await execute_tool_calls(calls, tools))

    assert [m.role for m in messages] == [
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    # assistant(tool_calls) 与 role=tool 结果靠 id ↔ tool_call_id 配对
    assert messages[2].get_tool_calls()[0].id == messages[3].content[0].tool_call_id
    assert messages[4].get_text_blocks()[0].text == "西安 22 度"
    assert len(model.seen) == 2
    assert model.seen[1]["messages"][3].role is Role.TOOL


@pytest.mark.asyncio
async def test_offline_streaming_tool_loop():
    """流式版本：消费完流拿到收尾响应，再走同一套判断（规格 §3.6 末句）。"""
    model = _ScriptedModel(
        [
            [
                parse_chunk(
                    _tool_chunk([_delta_tool_call(0, id="call_1", name="echo")])
                ),
                parse_chunk(
                    _tool_chunk([_delta_tool_call(0, arguments='{"city":"Xian"}')])
                ),
                _tool_chunk_delta(finish_reason="tool_calls"),
            ],
            [parse_chunk(_text_chunk("西安 22 度"))],
        ],
        stream=True,
    )
    tools = {"echo": _EchoTool()}
    messages = [Message.user("西安天气如何？")]

    while True:
        final = None
        async for part in await model(messages, tools=list(tools.values())):
            if part.is_last:
                final = part
        messages.append(final.to_message())
        calls = final.get_tool_calls()
        if not calls:
            break
        messages.extend(await execute_tool_calls(calls, tools))

    assert messages[1].get_tool_calls()[0].arguments == '{"city":"Xian"}'
    assert messages[1].get_tool_calls()[0].id == "call_1"
    assert messages[3].get_text_blocks()[0].text == "西安 22 度"
