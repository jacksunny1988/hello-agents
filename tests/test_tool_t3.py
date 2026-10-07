"""T3：普通函数自动适配（注解/docstring → JSON Schema，返回值归一），全部离线确定性"""

import json
import threading

from hello_agents.tool._adapters import FunctionTool
from hello_agents.tool._response import ToolResponse, ToolStatus


def add(a: int, b: int = 2) -> int:
    """两数相加。

    这是更长的说明，会拼在短描述之后。

    Args:
        a: 第一个加数。
        b: 第二个加数，有默认值、非必填。
    """
    return a + b


async def greet(name: str) -> str:
    """向某人打招呼。

    Args:
        name: 要问候的名字。
    """
    return f"hello {name}"


def list_user() -> dict:
    """返回一个字典。"""
    return {"id": 1, "tags": ["x", "y"]}


def list_numbers() -> list:
    """返回一个列表。"""
    return [1, 2, 3]


def returns_response() -> ToolResponse:
    """返回一个现成的 ToolResponse。"""
    return ToolResponse.succeed("已包装", source="custom")


def unannotated(x) -> str:
    """参数没有类型注解。"""
    return str(x)


def undocumented(a: int) -> int:
    return a


def no_params() -> str:
    """无参函数。"""
    return "ok"


def var_args(*args, **kwargs) -> str:
    """只有可变参数。"""
    return "ok"


def thread_probe(a: int) -> dict:
    """把执行线程 id 当结果返回，用来验证同步函数走了线程池。"""
    return {"thread": threading.get_ident()}


async def async_thread_probe(a: int) -> dict:
    """异步版本：不应换线程。"""
    return {"thread": threading.get_ident()}


# --- schema：类型映射 / required / 描述 -------------------------------------


def test_schema_type_mapping():
    schema = FunctionTool(add).input_schema
    assert schema["type"] == "object"
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "integer"


def test_required_lists_only_params_without_default():
    schema = FunctionTool(add).input_schema
    assert schema["required"] == ["a"]


def test_param_description_comes_from_docstring():
    props = FunctionTool(add).input_schema["properties"]
    assert props["a"]["description"] == "第一个加数。"
    assert props["b"]["description"] == "第二个加数，有默认值、非必填。"


def test_schema_has_no_title_noise():
    """pydantic 会自动加 title，必须被递归删干净。"""
    schema = FunctionTool(add).input_schema
    assert "title" not in schema
    assert all("title" not in prop for prop in schema["properties"].values())


# --- 函数级元数据 -----------------------------------------------------------


def test_name_defaults_to_function_name():
    assert FunctionTool(add).name == "add"


def test_description_joins_short_and_long():
    assert FunctionTool(add).description == (
        "两数相加。\n这是更长的说明，会拼在短描述之后。"
    )


def test_explicit_name_and_description_win():
    tool = FunctionTool(add, name="my_add", description="自定义描述")
    assert tool.name == "my_add"
    assert tool.description == "自定义描述"


def test_function_without_docstring_yields_empty_description():
    tool = FunctionTool(undocumented)
    assert tool.description == ""
    assert tool.name == "undocumented"


# --- 调用：同步 / 异步 ------------------------------------------------------


async def test_sync_function_call_and_int_normalization():
    resp = await FunctionTool(add)(a=1)
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "3"


async def test_default_param_may_be_omitted():
    resp = await FunctionTool(add)(a=1, b=5)
    assert resp.get_text() == "6"


async def test_async_function_call():
    resp = await FunctionTool(greet)(name="world")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hello world"


# --- 返回值归一 -------------------------------------------------------------


async def test_dict_result_is_json_serialized():
    resp = await FunctionTool(list_user)()
    assert json.loads(resp.get_text()) == {"id": 1, "tags": ["x", "y"]}


async def test_list_result_is_json_serialized():
    resp = await FunctionTool(list_numbers)()
    assert json.loads(resp.get_text()) == [1, 2, 3]


async def test_json_serialization_keeps_non_ascii():
    def echo_cn() -> dict:
        """返回中文。"""
        return {"msg": "你好"}

    resp = await FunctionTool(echo_cn)()
    assert "你好" in resp.get_text()


async def test_tool_response_result_is_returned_as_is():
    """已是 ToolResponse 就不再二次包装——metadata 原样保留即是证据。"""
    resp = await FunctionTool(returns_response)()
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "已包装"
    assert resp.metadata == {"source": "custom"}


# --- 边界：无注解 / 无 docstring / 无参 / 可变参数 --------------------------


def test_unannotated_param_has_no_type_constraint():
    schema = FunctionTool(unannotated).input_schema
    assert "type" not in schema["properties"]["x"]
    assert schema["required"] == ["x"]


def test_no_param_function_has_empty_properties():
    schema = FunctionTool(no_params).input_schema
    assert schema["properties"] == {}


async def test_no_param_function_can_be_called():
    resp = await FunctionTool(no_params)()
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "ok"


def test_var_positional_and_keyword_are_skipped():
    schema = FunctionTool(var_args).input_schema
    assert schema["properties"] == {}
    assert schema.get("required", []) == []


# --- 与 T2 执行收口的衔接 ---------------------------------------------------


async def test_sync_function_still_runs_off_the_event_loop_thread():
    """T2 不回归：适配后的同步函数依然在别的线程执行。"""
    main_thread = threading.get_ident()
    resp = await FunctionTool(thread_probe)(a=1)
    assert json.loads(resp.get_text())["thread"] != main_thread


async def test_async_function_stays_on_the_event_loop_thread():
    main_thread = threading.get_ident()
    resp = await FunctionTool(async_thread_probe)(a=1)
    assert json.loads(resp.get_text())["thread"] == main_thread


def test_function_tool_is_a_tool_base():
    """适配器拿到的应是完整工具能力，比如继承来的 function schema。"""
    schema = FunctionTool(add).get_function_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "add"
    assert schema["function"]["parameters"]["required"] == ["a"]
