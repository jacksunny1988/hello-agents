import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

from docstring_parser import parse
from pydantic import ConfigDict, Field, create_model

from ._base import ToolBase
from ._response import ToolResponse


def _remove_title_field(schema: dict) -> dict:
    """递归删除 pydantic 自动生成的 title 噪音。"""
    if "title" in schema:
        schema.pop("title")
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _remove_title_field(prop)
    if "items" in schema and isinstance(schema["items"], dict):
        _remove_title_field(schema["items"])
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        _remove_title_field(schema["additionalProperties"])
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for sub_schema in schema["$defs"].values():
            if isinstance(sub_schema, dict):
                _remove_title_field(sub_schema)
    return schema


def _extract_func_description(docstring: str) -> str:
    """docstring → short + long 描述。"""
    parsed = parse(docstring or "")
    parts: list[str] = []
    if parsed.short_description is not None:
        parts.append(parsed.short_description)
    if parsed.long_description is not None:
        parts.append(parsed.long_description)
    return "\n".join(parts)


def _extract_input_schema(func: Callable) -> dict:
    """从函数签名/注解/docstring 生成入参 JSON Schema。"""
    parsed = parse(func.__doc__ or "")
    param_docs = {param.arg_name: param.description for param in parsed.params}

    fields: dict[str, Any] = {}
    for param_name, sig_param in inspect.signature(func).parameters.items():
        # 跳过 self/cls；*args/**kwargs 不纳入（见 3.5 边界）
        if param_name in ("self", "cls"):
            continue
        if sig_param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        annotation = (
            Any
            if sig_param.annotation is inspect.Parameter.empty
            else sig_param.annotation
        )
        has_default = sig_param.default is not inspect.Parameter.empty
        field = Field(
            description=param_docs.get(param_name),
            default=... if not has_default else sig_param.default,
        )
        fields[param_name] = (annotation, field)

    dynamic_model = create_model(
        "_DynamicToolParams",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        **fields,
    )
    schema = dynamic_model.model_json_schema()
    return _remove_title_field(schema)


def _normalize_result(result: Any) -> ToolResponse:
    """任意函数返回值 → ToolResponse。"""
    if isinstance(result, ToolResponse):
        return result
    if isinstance(result, str):
        return ToolResponse.succeed(result)
    try:
        return ToolResponse.succeed(json.dumps(result, ensure_ascii=False))
    except (TypeError, ValueError):
        return ToolResponse.succeed(str(result))


class FunctionTool(ToolBase):
    """把普通 Python 函数适配为 ToolBase。"""

    def __init__(
        self,
        func: Callable,
        *,
        name: str | None = None,
        description: str | None = None,
        is_read_only: bool = False,
        is_concurrency_safe: bool = True,
    ) -> None:
        # 先准备元数据与 schema，再调用 super().__init__（其中会校验 schema）
        self._func = func
        self.name = name or func.__name__
        self.description = description or _extract_func_description(func.__doc__ or "")
        self.input_schema = _extract_input_schema(func)
        self.is_read_only = is_read_only
        self.is_concurrency_safe = is_concurrency_safe
        super().__init__()

    async def call(self, **kwargs: Any) -> ToolResponse:
        """执行被包装函数：async 直接 await，sync 丢线程池（不阻塞事件循环）。"""
        if inspect.iscoroutinefunction(self._func):
            result = await self._func(**kwargs)
        else:
            result = await asyncio.to_thread(self._func, **kwargs)
        return _normalize_result(result)
