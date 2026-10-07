"""T1：ToolResponse 出参模型与 ToolBase 核心抽象（全部离线，不发网络请求）"""

from typing import ClassVar

import pytest
from pydantic import ValidationError

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse, ToolStatus, ToolTextBlock
from hello_agents.tool._types import ToolStatus as ToolStatusInTypes


class EchoTool(ToolBase):
    name = "echo"
    description = "原样返回传入的文本"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


class NoArgTool(ToolBase):
    """无参工具：properties 为空 dict 也合法。"""

    name = "noop"
    description = "什么都不做"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("ok")


class MissingTypeTool(ToolBase):
    name = "bad-type"
    description = "缺少 type"
    input_schema: ClassVar[dict] = {"properties": {"text": {"type": "string"}}}

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("unreachable")


class BadPropertiesTool(ToolBase):
    name = "bad-props"
    description = "properties 不是 dict"
    input_schema: ClassVar[dict] = {"type": "object", "properties": ["text"]}

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("unreachable")


class NotADictTool(ToolBase):
    name = "bad-dict"
    description = "schema 根本不是 dict"
    input_schema: ClassVar = "not-a-dict"

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("unreachable")


# --- ToolStatus -------------------------------------------------------------


def test_status_has_four_members():
    assert [s.value for s in ToolStatus] == [
        "success",
        "error",
        "denied",
        "interrupted",
    ]


def test_status_is_str_enum():
    assert ToolStatus.SUCCESS == "success"
    assert f"{ToolStatus.DENIED}" == "denied"


def test_response_reexports_status_from_types():
    """T1.5 示例从 _response 导入 ToolStatus，这条守住该路径。"""
    assert ToolStatus is ToolStatusInTypes


# --- ToolResponse：三种构造 ------------------------------------------------


def test_succeed_sets_status_and_text():
    resp = ToolResponse.succeed("done")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "done"
    assert len(resp.content) == 1
    assert resp.content[0].type == "text"
    assert resp.metadata == {}


def test_fail_sets_error_status():
    resp = ToolResponse.fail("boom")
    assert resp.status is ToolStatus.ERROR
    assert resp.get_text() == "boom"


def test_denied_sets_denied_status():
    resp = ToolResponse.denied("用户拒绝了")
    assert resp.status is ToolStatus.DENIED
    assert resp.get_text() == "用户拒绝了"


def test_denied_without_reason_uses_default_text():
    resp = ToolResponse.denied()
    assert resp.status is ToolStatus.DENIED
    assert resp.get_text() == "工具调用被用户拒绝"


def test_response_defaults():
    resp = ToolResponse()
    assert resp.status is ToolStatus.SUCCESS
    assert resp.content == []
    assert resp.metadata == {}
    assert resp.get_text() == ""


# --- ToolResponse：content / metadata / id ---------------------------------


def test_metadata_roundtrip():
    resp = ToolResponse.succeed("ok", tokens=12, cached=True, source=None)
    assert resp.metadata == {"tokens": 12, "cached": True, "source": None}


def test_metadata_default_is_not_shared_between_instances():
    first = ToolResponse.succeed("a")
    second = ToolResponse.succeed("b")
    first.metadata["k"] = 1
    assert second.metadata == {}


def test_get_text_concatenates_blocks_in_order():
    resp = ToolResponse(
        content=[
            ToolTextBlock(text="a"),
            ToolTextBlock(text="b"),
            ToolTextBlock(text="c"),
        ]
    )
    assert resp.get_text() == "abc"


def test_ids_are_unique_uuid_hex():
    first = ToolResponse.succeed("a")
    second = ToolResponse.succeed("b")
    assert first.id != second.id
    assert len(first.id) == 32
    assert first.content[0].id != second.content[0].id
    assert len(first.content[0].id) == 32


def test_text_block_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ToolTextBlock(text="x", unexpected=1)


# --- ToolBase：schema 校验 --------------------------------------------------


def test_valid_schema_instantiates():
    tool = EchoTool()
    assert tool.name == "echo"
    assert tool.description == "原样返回传入的文本"


def test_no_arg_tool_is_valid():
    assert NoArgTool().input_schema == {"type": "object", "properties": {}}


def test_schema_missing_type_raises_on_instantiation():
    with pytest.raises(ValueError, match="input_schema"):
        MissingTypeTool()


def test_schema_with_non_dict_properties_raises():
    with pytest.raises(ValueError, match="input_schema"):
        BadPropertiesTool()


def test_schema_that_is_not_a_dict_raises():
    with pytest.raises(ValueError, match="input_schema"):
        NotADictTool()


def test_schema_error_message_names_the_tool():
    with pytest.raises(ValueError, match="bad-type"):
        MissingTypeTool()


def test_tool_base_is_abstract():
    with pytest.raises(TypeError):
        ToolBase()


def test_default_flags():
    assert ToolBase.is_read_only is False
    assert ToolBase.is_concurrency_safe is True
    assert EchoTool.is_read_only is True
    assert EchoTool.is_concurrency_safe is True


# --- ToolBase：调用与 function schema --------------------------------------


async def test_echo_call_returns_success_and_same_text():
    resp = await EchoTool()(text="hello tool")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hello tool"


async def test_dunder_call_delegates_to_call():
    class SpyTool(ToolBase):
        name = "spy"
        description = "记录 call 是否被走到"
        input_schema: ClassVar[dict] = {"type": "object", "properties": {}}
        called = False

        async def call(self) -> ToolResponse:
            type(self).called = True
            return ToolResponse.succeed("spied")

    resp = await SpyTool()()
    assert SpyTool.called is True
    assert resp.get_text() == "spied"


def test_get_function_schema_shape():
    schema = EchoTool().get_function_schema()
    assert set(schema) == {"type", "function"}
    assert schema["type"] == "function"
    function = schema["function"]
    assert set(function) == {"name", "description", "parameters"}
    assert function["name"] == "echo"
    assert function["description"] == "原样返回传入的文本"
    assert function["parameters"] == EchoTool.input_schema
    assert function["parameters"]["required"] == ["text"]
