from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from ._adapters import FunctionTool
from ._base import ToolBase
from ._governance import Approver, ToolHook, run_governed
from ._response import ToolResponse
from ._types import RegisteredTool


class Toolkit:
    def __init__(
        self,
        tools: list[ToolBase] | None = None,
        hooks: list[ToolHook] | None = None,
        approver: Approver | None = None,
    ):
        self._tools: OrderedDict[str, RegisteredTool] = OrderedDict()
        for tool in tools or []:
            self.register_tool(tool)
        self.hooks: list[ToolHook] = hooks or []
        self.approver: Approver = approver

    def add_hook(self, hook: ToolHook) -> None:
        self.hooks.append(hook)

    def set_approver(self, approver: Approver) -> None:
        self.approver = approver

    # --- 注册 / 注销 ------------------------------------------------------
    def register_tool(self, tool: ToolBase, *, group: str = "basic") -> None:
        """注册工具"""
        if not isinstance(tool, ToolBase):
            raise TypeError(f"tool must be an instance of ToolBase, got {type(tool)}")
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册，请勿重复注册.")
        self._tools[tool.name] = RegisteredTool(tool=tool, group=group)

    def register_function(
        self, func: Callable, *, group: str = "basic", **adapter_kwargs: Any
    ) -> FunctionTool:
        """注册函数为工具"""
        tool = FunctionTool(func=func, **adapter_kwargs)
        self.register_tool(tool, group=group)
        return tool

    def unregister(self, name: str) -> None:
        """按名注销；不存在报错。"""
        if name not in self._tools:
            raise KeyError(f"无法注销，工具 {name!r} 未注册")
        del self._tools[name]

    def get_tool(self, name: str) -> ToolBase:
        """按名取工具；不存在报错。"""
        record = self._tools.get(name)
        if record is None:
            raise KeyError(f"工具 {name!r} 未注册")
        return record.tool

    def _records(self, groups: list[str] | None) -> list[RegisteredTool]:
        """返回已注册工具的记录列表"""
        if groups is None:
            return list(self._tools.values())
        group_set = set(groups)
        return [record for record in self._tools.values() if record.group in group_set]

    def list_tools(self, groups: list[str] | None = None) -> list[ToolBase]:
        """返回已注册工具的列表"""
        return [record.tool for record in self._records(groups)]

    def get_tool_schemas(self, groups: list[str] | None = None) -> list[dict[str, Any]]:
        """获取工具的 schema"""
        return [tool.get_function_schema() for tool in self.list_tools(groups=groups)]

    def list_groups(self) -> list[str]:
        """返回已注册工具的分组列表"""
        groups: list[str] = []
        for record in self._tools.values():
            if record.group not in groups:
                groups.append(record.group)
        return groups

    async def call_tool(self, name: str, /, **kwargs: Any) -> ToolResponse:
        """按名调用工具；执行收口（线程池/超时/异常）由 T2 的 __call__ 负责。

        `name` 是 positional-only：`**kwargs` 是工具自身的入参命名空间，
        工具形参完全可以叫 `name`，若把工具名也放进该命名空间就会重复绑定。
        """
        tool = self.get_tool(name)
        return await run_governed(self, tool, kwargs)
