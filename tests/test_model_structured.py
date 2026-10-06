"""M7 结构化输出：strict schema、三模式请求与取值、修复闭环、统计。全部离线"""

import json
from typing import Literal, cast

import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice as CompletionChoice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from pydantic import BaseModel, Field, ValidationError

from hello_agents.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
    Message,
    Provider,
    Role,
    TextBlock,
    ToolCallBlock,
    get_model_config,
)
from hello_agents.model._formatter import (
    from_completion,
    to_response_format_json_object,
    to_response_format_json_schema,
)
from hello_agents.model._structured import (
    SUBMIT_TOOL_NAME,
    NativeJsonSchemaUnsupportedError,
    StructuredOutputError,
    extract_structured,
    extract_structured_with_stats,
    strict_json_schema,
)
from hello_agents.model.providers import OpenAICompatModel


class Address(BaseModel):
    """嵌套模型：用来验证 `$defs` 里的对象也被递归改写。"""

    city: str
    zip_code: str | None = None


class Person(BaseModel):
    """目标 schema：带约束 / 默认值 / 可空 / 列表 / 枚举，覆盖 strict 化的全部要点。"""

    name: str
    age: int = Field(ge=0, le=150)
    city: str
    address: Address | None = None
    tags: list[str] = []
    kind: Literal["person", "robot"] = "person"


# --- strict schema 转换 ---


def test_strict_schema_forbids_extra_properties():
    assert strict_json_schema(Person)["additionalProperties"] is False


def test_strict_schema_requires_every_property():
    """strict 模式没有「可省略」：带默认值的字段也进 required。"""
    assert strict_json_schema(Person)["required"] == [
        "name",
        "age",
        "city",
        "address",
        "tags",
        "kind",
    ]


def test_strict_schema_recurses_into_defs():
    address = strict_json_schema(Person)["$defs"]["Address"]
    assert address["additionalProperties"] is False
    assert address["required"] == ["city", "zip_code"]


def test_strict_schema_strips_title_and_default():
    schema = strict_json_schema(Person)
    assert "title" not in schema
    assert "title" not in schema["properties"]["name"]
    assert "default" not in schema["properties"]["kind"]


def test_strict_schema_keeps_constraints_and_nullable():
    """只改 strict 相关形状，数值约束、枚举、anyOf 原样保留。"""
    props = strict_json_schema(Person)["properties"]
    assert props["age"]["minimum"] == 0
    assert props["age"]["maximum"] == 150
    assert props["kind"]["enum"] == ["person", "robot"]
    assert props["address"]["anyOf"][1] == {"type": "null"}


def test_strict_schema_handles_objects_inside_array_items():
    class Team(BaseModel):
        members: list[Address]

    schema = strict_json_schema(Team)
    assert schema["$defs"]["Address"]["additionalProperties"] is False


def test_strict_schema_does_not_mutate_the_model_schema():
    """转换必须是拷贝：Pydantic 的 model_json_schema 有缓存，改它等于污染全局。"""
    before = Person.model_json_schema()
    strict_json_schema(Person)
    assert Person.model_json_schema() == before


# --- response_format 构造 ---


def test_json_object_response_format_shape():
    assert to_response_format_json_object() == {"type": "json_object"}


def test_json_schema_response_format_shape():
    schema = strict_json_schema(Person)
    assert to_response_format_json_schema("person", schema) == {
        "type": "json_schema",
        "json_schema": {"name": "person", "schema": schema, "strict": True},
    }


# --- 假模型与假客户端 ---


class _ScriptedModel(ChatModelBase):
    """按脚本逐个返回响应的假模型，并记录每次 `_call_api` 收到的参数。

    脚本元素：`ChatResponse`（非流式）或 `list[ChatResponse]`（流式的增量序列）。
    """

    def __init__(self, script, config=None, **kwargs):
        super().__init__(
            config=config or get_model_config(Provider.DEEPSEEK),
            client=cast(AsyncOpenAI, None),
            **kwargs,
        )
        self.script = list(script)
        self.seen: list[dict] = []

    async def _call_api(
        self,
        messages,
        stream,
        tools=None,
        tool_choice=None,
        response_format=None,
    ):
        self.seen.append(
            {
                "messages": messages,
                "stream": stream,
                "tools": tools,
                "tool_choice": tool_choice,
                "response_format": response_format,
            }
        )
        item = self.script.pop(0) if self.script else ChatResponse(content=[])
        if stream:
            return _stream_of(item if isinstance(item, list) else [item])
        return item


async def _stream_of(items):
    for item in items:
        yield item


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


# --- response_format 透传 ---


@pytest.mark.asyncio
async def test_response_format_reaches_call_api():
    model = _ScriptedModel([])

    await model([Message.user("hi")], response_format={"type": "json_object"})

    assert model.seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_response_format_defaults_to_none():
    model = _ScriptedModel([])

    await model([Message.user("hi")])

    assert model.seen[0]["response_format"] is None


@pytest.mark.asyncio
async def test_response_format_passes_through_in_stream():
    """流式虽然不用于结构化输出，签名一致透传不能破（规格 §7.1）。"""
    model = _ScriptedModel([], stream=True)

    async for _ in await model(
        [Message.user("hi")], response_format={"type": "json_object"}
    ):
        pass

    assert model.seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compat_sends_response_format():
    completions = _FakeCompletions()

    await _fake_model(completions)(
        [Message.user("hi")], response_format={"type": "json_object"}
    )

    assert completions.kwargs[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_compat_omits_response_format_when_none():
    """不传就不写这个 key，让端点用自己的默认值（同 M6 的 tools 规则）。"""
    completions = _FakeCompletions()

    await _fake_model(completions)([Message.user("hi")])

    assert "response_format" not in completions.kwargs[0]


# --- FinishedReason.LENGTH（承接 M6 §8 的遗留项）---


def test_from_completion_maps_length_finish_reason():
    completion = ChatCompletion(
        id="x",
        created=1,
        model="m",
        object="chat.completion",
        choices=[
            CompletionChoice(
                index=0,
                finish_reason="length",
                message=ChatCompletionMessage(role="assistant", content='{"na'),
            )
        ],
    )

    assert from_completion(completion, 0.0).finished_reason is FinishedReason.LENGTH


# --- extract_structured：入口校验与 tool 模式 ---


def _submit_call(
    arguments: str, *, name: str = SUBMIT_TOOL_NAME, call_id: str = "call_1"
):
    """一轮 tool 模式响应：模型调用了提交工具。"""
    return ChatResponse(
        content=[ToolCallBlock(name=name, arguments=arguments, id=call_id)],
        finished_reason=FinishedReason.TOOL_CALLS,
    )


VALID_PERSON = '{"name": "老张", "age": 30, "city": "西安"}'


@pytest.mark.asyncio
async def test_tool_mode_returns_validated_object():
    model = _ScriptedModel([_submit_call(VALID_PERSON)])

    person = await extract_structured(model, [Message.user("抽取人物")], Person)

    assert isinstance(person, Person)
    assert (person.name, person.age) == ("老张", 30)


@pytest.mark.asyncio
async def test_tool_mode_sends_submit_tool_with_plain_schema():
    """工具参数用**原样** schema，不做 strict 化（strict 只服务 json_schema 模式）。"""
    model = _ScriptedModel([_submit_call(VALID_PERSON)])

    await extract_structured(model, [Message.user("抽取人物")], Person)

    seen = model.seen[0]
    assert seen["tool_choice"] == "auto"
    assert [tool.name for tool in seen["tools"]] == [SUBMIT_TOOL_NAME]
    assert seen["tools"][0].parameters == Person.model_json_schema()


@pytest.mark.asyncio
async def test_tool_mode_prepends_forcing_system_prompt():
    """决策 2：不用 required，靠强系统提示把模型逼进工具调用。"""
    model = _ScriptedModel([_submit_call(VALID_PERSON)])
    messages = [Message.user("抽取人物")]

    await extract_structured(model, messages, Person)

    sent = model.seen[0]["messages"]
    assert sent[0].role is Role.SYSTEM
    assert SUBMIT_TOOL_NAME in sent[0].get_text_blocks()[0].text
    assert sent[1].get_text_blocks()[0].text == "抽取人物"


@pytest.mark.asyncio
async def test_caller_messages_are_not_mutated():
    """调用方复用同一个列表跑多种模式（demo 正是这么干的），不能被污染。"""
    model = _ScriptedModel([_submit_call(VALID_PERSON)])
    messages = [Message.user("抽取人物")]

    await extract_structured(model, messages, Person)

    assert len(messages) == 1


@pytest.mark.asyncio
async def test_stats_reports_single_attempt_on_first_try_success():
    usage = ChatUsage(input_tokens=10, output_tokens=3)
    response = _submit_call(VALID_PERSON)
    response.usage = usage
    model = _ScriptedModel([response])

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert stats.mode == "tool"
    assert stats.ok_first_try is True
    assert stats.rounds == 0
    assert len(stats.attempts) == 1
    assert stats.attempts[0].error is None
    assert stats.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_streaming_model_is_rejected():
    model = _ScriptedModel([], stream=True)

    with pytest.raises(ValueError, match="非流式"):
        await extract_structured(model, [Message.user("抽取人物")], Person)

    assert model.seen == []


@pytest.mark.asyncio
async def test_unknown_mode_is_rejected_before_any_request():
    model = _ScriptedModel([])

    with pytest.raises(ValueError, match="mode"):
        await extract_structured(model, [Message.user("抽取人物")], Person, mode="yaml")

    assert model.seen == []


@pytest.mark.asyncio
async def test_non_pydantic_output_model_is_rejected():
    model = _ScriptedModel([])

    with pytest.raises(TypeError):
        await extract_structured(model, [Message.user("抽取人物")], dict)


# --- 修复闭环 ---


INCOMPLETE_PERSON = '{"name": "老张"}'  # 缺 age / city


@pytest.mark.asyncio
async def test_validation_failure_is_repaired_on_the_second_round():
    model = _ScriptedModel(
        [_submit_call(INCOMPLETE_PERSON), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    person, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert person.city == "西安"
    assert stats.rounds == 1
    assert [attempt.error is None for attempt in stats.attempts] == [False, True]
    assert "age" in stats.attempts[0].error


@pytest.mark.asyncio
async def test_tool_repair_feedbacks_the_original_call_and_a_paired_result():
    """协议要求：assistant 的每个 tool_call 都必须有一条配对的 tool 结果。"""
    model = _ScriptedModel(
        [_submit_call(INCOMPLETE_PERSON), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    await extract_structured(model, [Message.user("抽取人物")], Person)

    repair = model.seen[1]["messages"]
    assistant, tool_result = repair[-2], repair[-1]
    assert assistant.role is Role.ASSISTANT
    assert [call.id for call in assistant.get_tool_calls()] == ["call_1"]
    assert tool_result.role is Role.TOOL
    block = tool_result.content[0]
    assert block.tool_call_id == "call_1"
    assert block.is_error is True
    assert "age" in block.output


@pytest.mark.asyncio
async def test_repair_keeps_the_original_request_in_context():
    """修复不能让上下文越修越偏：原始需求与模式提示必须在。"""
    model = _ScriptedModel(
        [_submit_call(INCOMPLETE_PERSON), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    await extract_structured(model, [Message.user("从这段话抽取人物")], Person)

    second_round = model.seen[1]["messages"]
    assert second_round[0].role is Role.SYSTEM
    assert SUBMIT_TOOL_NAME in second_round[0].get_text_blocks()[0].text
    assert second_round[1].get_text_blocks()[0].text == "从这段话抽取人物"


@pytest.mark.asyncio
async def test_broken_json_arguments_trigger_a_repair():
    model = _ScriptedModel(
        [_submit_call('{"name": "老张"'), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    person, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert person.name == "老张"
    assert "合法 JSON" in stats.attempts[0].error


@pytest.mark.asyncio
async def test_non_object_arguments_trigger_a_repair():
    model = _ScriptedModel(
        [_submit_call('["老张", 30]'), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert "JSON 对象" in stats.attempts[0].error


@pytest.mark.asyncio
async def test_text_only_response_is_repaired_as_a_user_message():
    """模型没调工具时没有 tool_call_id 可配对，只能退回 user 消息回灌。"""
    text_only = ChatResponse(content=[TextBlock(text="老张今年三十，住在西安")])
    model = _ScriptedModel([text_only, _submit_call(VALID_PERSON, call_id="call_2")])

    person, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert person.city == "西安"
    assert "没有调用" in stats.attempts[0].error
    repair = model.seen[1]["messages"]
    assert repair[-1].role is Role.USER
    assert "没有调用" in repair[-1].get_text_blocks()[0].text


@pytest.mark.asyncio
async def test_other_tool_calls_get_a_paired_unknown_tool_result():
    """模型幻觉调了别的工具也要回灌结果，否则请求本身不合法。"""
    stray_call = ChatResponse(
        content=[ToolCallBlock(name="get_weather", arguments="{}", id="call_9")],
        finished_reason=FinishedReason.TOOL_CALLS,
    )
    model = _ScriptedModel([stray_call, _submit_call(VALID_PERSON, call_id="call_2")])

    person = await extract_structured(model, [Message.user("抽取人物")], Person)

    assert person.name == "老张"
    repair = model.seen[1]["messages"]
    block = repair[-1].content[0]
    assert block.tool_call_id == "call_9"
    assert block.is_error is True
    assert "未知工具" in block.output


@pytest.mark.asyncio
async def test_exhausted_rounds_raise_with_the_last_error_as_cause():
    model = _ScriptedModel([_submit_call(INCOMPLETE_PERSON)] * 3)

    with pytest.raises(StructuredOutputError) as excinfo:
        await extract_structured(
            model, [Message.user("抽取人物")], Person, max_fix_rounds=2
        )

    assert len(model.seen) == 3, "max_fix_rounds=2 应当共发 3 次请求"
    assert isinstance(excinfo.value.__cause__, ValidationError)
    assert "age" in str(excinfo.value)


@pytest.mark.asyncio
async def test_zero_fix_rounds_means_a_single_attempt():
    model = _ScriptedModel([_submit_call(INCOMPLETE_PERSON)] * 3)

    with pytest.raises(StructuredOutputError):
        await extract_structured(
            model, [Message.user("抽取人物")], Person, max_fix_rounds=0
        )

    assert len(model.seen) == 1


@pytest.mark.asyncio
async def test_negative_fix_rounds_degrade_to_no_repair():
    """与 `_with_retry` 对负数 max_retries 的处理一致：自然退化成「不重试」。"""
    model = _ScriptedModel([_submit_call(INCOMPLETE_PERSON)] * 3)

    with pytest.raises(StructuredOutputError):
        await extract_structured(
            model, [Message.user("抽取人物")], Person, max_fix_rounds=-1
        )

    assert len(model.seen) == 1


@pytest.mark.asyncio
async def test_error_text_omits_the_models_own_output():
    """回灌的是精简错误：把模型刚输出的一长串原文再喂回去纯属烧 token。"""
    long_name = "x" * 3000
    model = _ScriptedModel(
        [
            _submit_call(json.dumps({"name": long_name})),
            _submit_call(VALID_PERSON, call_id="call_2"),
        ]
    )

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    error = stats.attempts[0].error
    assert long_name not in error
    assert len(error) < 1400


@pytest.mark.asyncio
async def test_error_text_caps_the_number_of_listed_errors():
    class Wide(BaseModel):
        a1: int
        a2: int
        a3: int
        a4: int
        a5: int
        a6: int
        a7: int
        a8: int
        a9: int
        a10: int
        a11: int
        a12: int

    model = _ScriptedModel([_submit_call("{}"), _submit_call("{}", call_id="call_2")])

    with pytest.raises(StructuredOutputError) as excinfo:
        await extract_structured(model, [Message.user("x")], Wide, max_fix_rounds=0)

    assert "其余 2 条略" in str(excinfo.value)


@pytest.mark.asyncio
async def test_stats_sum_usage_across_rounds():
    first, second = (
        _submit_call(INCOMPLETE_PERSON),
        _submit_call(VALID_PERSON, call_id="call_2"),
    )
    first.usage = ChatUsage(input_tokens=10, output_tokens=2, time=0.5)
    second.usage = ChatUsage(input_tokens=20, output_tokens=5, time=0.7)
    model = _ScriptedModel([first, second])

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert stats.usage.input_tokens == 30
    assert stats.usage.output_tokens == 7
    assert stats.usage.time == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_stats_skip_rounds_without_usage():
    model = _ScriptedModel(
        [_submit_call(INCOMPLETE_PERSON), _submit_call(VALID_PERSON, call_id="call_2")]
    )

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert stats.usage.input_tokens == 0


@pytest.mark.asyncio
async def test_deeply_nested_tool_arguments_are_reported_instead_of_crashing():
    """同 M6 的 `_invoke`：json.loads 撞递归上限也是「一次失败」，不是崩掉整次调用。"""
    model = _ScriptedModel(
        [
            _submit_call("[" * 100000 + "]" * 100000),
            _submit_call(VALID_PERSON, call_id="c2"),
        ]
    )

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person
    )

    assert "嵌套层级过深" in stats.attempts[0].error


# --- json_object 模式 ---


def _text_response(text: str) -> ChatResponse:
    return ChatResponse(content=[TextBlock(text=text)])


def _capable_model(script, **kwargs) -> _ScriptedModel:
    """注册表里的 config 对象是**共享**的，就地改会污染其它测试——必须拷贝。"""
    config = get_model_config(Provider.DEEPSEEK).model_copy(
        update={"supports_native_json_schema": True}
    )
    return _ScriptedModel(script, config=config, **kwargs)


@pytest.mark.asyncio
async def test_json_object_mode_returns_validated_object():
    model = _ScriptedModel([_text_response(VALID_PERSON)])

    person = await extract_structured(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert person.age == 30


@pytest.mark.asyncio
async def test_json_object_mode_sends_response_format_and_inlines_schema():
    model = _ScriptedModel([_text_response(VALID_PERSON)])

    await extract_structured(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    seen = model.seen[0]
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["tools"] is None
    system_text = seen["messages"][0].get_text_blocks()[0].text
    assert "JSON Schema" in system_text
    assert '"name"' in system_text
    assert "additionalProperties" not in system_text, (
        "提示词用原样 schema，不做 strict 化"
    )


@pytest.mark.asyncio
async def test_json_object_mode_accepts_markdown_fenced_json():
    fenced = f"```json\n{VALID_PERSON}\n```"
    model = _ScriptedModel([_text_response(fenced)])

    person = await extract_structured(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert person.city == "西安"


@pytest.mark.asyncio
async def test_json_object_mode_repairs_broken_json():
    model = _ScriptedModel([_text_response("{不是 JSON"), _text_response(VALID_PERSON)])

    person, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert person.name == "老张"
    assert "合法 JSON" in stats.attempts[0].error
    repair = model.seen[1]["messages"]
    assert repair[-2].role is Role.ASSISTANT
    assert repair[-1].role is Role.USER


@pytest.mark.asyncio
async def test_empty_text_response_is_repaired_without_an_assistant_message():
    """空 content 的 assistant 消息在部分端点是非法请求，只能发 user。"""
    model = _ScriptedModel([ChatResponse(content=[]), _text_response(VALID_PERSON)])

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert "没有正文" in stats.attempts[0].error
    roles = [message.role for message in model.seen[1]["messages"]]
    assert Role.ASSISTANT not in roles, "空 content 的 assistant 消息不能发出去"
    assert roles[-1] is Role.USER


@pytest.mark.asyncio
async def test_deeply_nested_body_json_is_reported_instead_of_crashing():
    model = _ScriptedModel(
        [_text_response("[" * 100000 + "]" * 100000), _text_response(VALID_PERSON)]
    )

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert "嵌套层级过深" in stats.attempts[0].error


# --- json_schema 模式 ---


@pytest.mark.asyncio
async def test_json_schema_mode_requires_the_capability_bit():
    """决策 3：能力位为 False 直接抛错，连请求都不发（不静默降级）。"""
    model = _ScriptedModel([_text_response(VALID_PERSON)])

    with pytest.raises(NativeJsonSchemaUnsupportedError) as excinfo:
        await extract_structured(
            model, [Message.user("抽取人物")], Person, mode="json_schema"
        )

    assert "supports_native_json_schema" in str(excinfo.value)
    # M8 后能力位来自卡片 YAML，报错不能再让人去「回填注册表」（那个 Python dict 已删）。
    assert "回填注册表" not in str(excinfo.value)
    assert "providers/_models" in str(excinfo.value)
    assert model.seen == []


@pytest.mark.asyncio
async def test_json_schema_mode_sends_the_strict_schema():
    model = _capable_model([_text_response(VALID_PERSON)])

    await extract_structured(
        model, [Message.user("抽取人物")], Person, mode="json_schema"
    )

    seen = model.seen[0]
    assert seen["tools"] is None
    payload = seen["response_format"]["json_schema"]
    assert seen["response_format"]["type"] == "json_schema"
    assert payload["name"] == "Person"
    assert payload["strict"] is True
    assert payload["schema"]["additionalProperties"] is False
    assert payload["schema"]["required"] == [
        "name",
        "age",
        "city",
        "address",
        "tags",
        "kind",
    ]


@pytest.mark.asyncio
async def test_json_schema_mode_does_not_inject_a_system_prompt():
    """约束由服务端承担，消息原样发出。"""
    model = _capable_model([_text_response(VALID_PERSON)])
    messages = [Message.user("抽取人物")]

    await extract_structured(model, messages, Person, mode="json_schema")

    assert model.seen[0]["messages"] == messages


@pytest.mark.asyncio
async def test_json_schema_name_is_sanitized_and_truncated():
    weird = type(
        "Weird Name!" + "x" * 80, (BaseModel,), {"__annotations__": {"x": int}}
    )
    model = _capable_model([_text_response('{"x": 1}')])

    await extract_structured(model, [Message.user("x")], weird, mode="json_schema")

    name = model.seen[0]["response_format"]["json_schema"]["name"]
    assert name.startswith("Weird_Name_")
    assert len(name) == 64


@pytest.mark.asyncio
async def test_json_schema_name_falls_back_when_the_class_name_is_unusable():
    nameless = type("", (BaseModel,), {"__annotations__": {"x": int}})
    model = _capable_model([_text_response('{"x": 1}')])

    await extract_structured(model, [Message.user("x")], nameless, mode="json_schema")

    assert model.seen[0]["response_format"]["json_schema"]["name"] == "output"


@pytest.mark.asyncio
async def test_json_schema_mode_repairs_like_the_text_modes():
    model = _capable_model(
        [_text_response(VALID_PERSON[:8]), _text_response(VALID_PERSON)]
    )

    person, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person, mode="json_schema"
    )

    assert person.name == "老张"
    assert stats.rounds == 1


# --- 截断（承接 M6 §8 的遗留项）---


@pytest.mark.asyncio
async def test_truncated_response_is_reported_as_truncation_not_as_bad_json():
    """被砍断的 JSON 若不点破，模型会以为是自己格式写错了——错误提示必须归因正确。"""
    truncated = ChatResponse(
        content=[TextBlock(text=VALID_PERSON[:8])],
        finished_reason=FinishedReason.LENGTH,
    )
    model = _ScriptedModel([truncated, _text_response(VALID_PERSON)])

    _, stats = await extract_structured_with_stats(
        model, [Message.user("抽取人物")], Person, mode="json_object"
    )

    assert "截断" in stats.attempts[0].error
    assert "合法 JSON" in stats.attempts[0].error, "原始解析错误仍要保留"


# --- 公开导出 ---


def test_structured_api_is_exported_from_the_package_root():
    import hello_agents.model as model_package

    for name in (
        "SUBMIT_TOOL_NAME",
        "NativeJsonSchemaUnsupportedError",
        "StructuredAttempt",
        "StructuredMode",
        "StructuredOutputError",
        "StructuredStats",
        "extract_structured",
        "extract_structured_with_stats",
    ):
        assert name in model_package.__all__, f"{name} 应当在 __all__ 里"
        assert hasattr(model_package, name)
