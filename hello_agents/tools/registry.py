"""
工具注册机制 - 管理工具的注册和获取
"""

from collections.abc import Callable
from typing import Any

from .base import BaseTool


class ToolRegistry:
    """
    工具注册表，负责管理所有已注册的工具。

    提供工具的注册、获取、执行等功能。
    """

    def __init__(self):
        """初始化工具注册表"""
        self.tools: dict[str, BaseTool] = {}
        self._functions: dict[str, dict[str, Any]] = {}

    def register_tool(self, name: str, tool: BaseTool) -> None:
        """
        注册工具。

        Args:
            name: 工具名称
            tool: 工具实例
        """
        if name in self.tools:
            print(f"⚠️ 警告:工具 '{name}' 已存在，将被覆盖。")
        self.tools[name] = tool
        print(f"✅ 工具 '{name}' 已注册。")

    def register_function(
        self, name: str, description: str, func: Callable[[str], str]
    ):
        """
        直接注册函数作为工具（简便方式）

        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数，接受字符串参数，返回字符串结果
        """
        if name in self._functions:
            print(f"⚠️ 警告:工具 '{name}' 已存在，将被覆盖。")

        self._functions[name] = {"description": description, "func": func}
        print(f"✅ 工具 '{name}' 已注册。")

    def get_tool(self, name: str) -> BaseTool | None:
        """
        获取工具。

        Args:
            name: 工具名称

        Returns:
            工具实例，如果不存在则返回None
        """
        return self.tools.get(name)

    def execute_tool(self, name: str, input_data: Any, **kwargs) -> Any:
        """
        执行工具。

        Args:
            name: 工具名称
            input_data: 输入数据
            **kwargs: 额外参数

        Returns:
            工具执行结果

        Raises:
            ValueError: 如果工具不存在
        """
        tool = self.get_tool(name)
        if tool is not None:
            return tool.run(input_data, **kwargs)
        if name in self._functions:
            return self._functions[name]["func"](input_data)
        raise ValueError(f"工具 '{name}' 未注册")

    def get_tools_description(self) -> str:
        """
        获取所有已注册工具的描述。

        Returns:
            工具描述字符串
        """
        if not self.tools and not self._functions:
            return "暂无可用工具"

        descriptions = []
        # Tool对象描述
        for name, tool in self.tools.items():
            descriptions.append(f"- {name}: {tool.get_description()}")
        # 函数工具描述
        for name, info in self._functions.items():
            descriptions.append(f"- {name}: {info['description']}")
        return "\n".join(descriptions) if descriptions else "暂无可用工具"

    def list_tools(self) -> list:
        """列出所有已注册的工具名称"""
        return list(self.tools.keys()) + list(self._functions.keys())

    def to_openai_schema(self) -> list[dict[str, Any]]:
        """
        将所有已注册工具转换为 OpenAI function calling schema 列表

        用于 FunctionCallAgent，使工具能够被 OpenAI 原生 function calling 使用

        Returns:
            符合 OpenAI function calling 标准的 schema 列表
        """
        schemas: list[dict[str, Any]] = []
        for name, tool in self.tools.items():
            # 构建 properties
            properties = {}
            required = []
            for param in tool.get_parameters():
                prop = {"type": param.type, "description": param.description}
                # 如果有默认值，添加到描述中（OpenAI schema 不支持 default 字段）
                if param.default is not None:
                    prop["description"] = f"{param.description} (默认: {param.default})"
                # 如果是数组类型，添加 items 定义
                if param.type == "array":
                    prop["items"] = {"type": "string"}  # 默认字符串数组
                properties[param.name] = prop
                if param.required:
                    required.append(param.name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get_description(),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={list(self.tools.keys())})"
