"""
工具注册机制 - 管理工具的注册和获取
"""

from typing import Dict, Any, Optional
from .base import BaseTool


class ToolRegistry:
    """
    工具注册表，负责管理所有已注册的工具。

    提供工具的注册、获取、执行等功能。
    """

    def __init__(self):
        """初始化工具注册表"""
        self.tools: Dict[str, BaseTool] = {}

    def register_tool(self, name: str, tool: BaseTool) -> None:
        """
        注册工具。

        Args:
            name: 工具名称
            tool: 工具实例
        """
        self.tools[name] = tool

    def get_tool(self, name: str) -> Optional[BaseTool]:
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
        if not tool:
            raise ValueError(f"工具 '{name}' 未注册")
        return tool.run(input_data, **kwargs)

    def get_tools_description(self) -> str:
        """
        获取所有已注册工具的描述。

        Returns:
            工具描述字符串
        """
        if not self.tools:
            return "暂无可用工具"

        descriptions = []
        for name, tool in self.tools.items():
            descriptions.append(f"- {name}: {tool.get_description()}")
        return "\n".join(descriptions)

    def list_tools(self) -> list:
        """列出所有已注册的工具名称"""
        return list(self.tools.keys())

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={list(self.tools.keys())})"
