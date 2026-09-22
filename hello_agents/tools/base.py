"""
工具基类 - 定义所有工具的基础接口
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from hello_agents.tools.response import ToolResponse


class ToolParameter(BaseModel):
    """工具参数定义"""

    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


class BaseTool(ABC):
    """
    工具基类，定义了所有工具必须实现的接口。

    所有具体的工具实现（如SearchTool、CalculatorTool等）
    都应该继承这个基类并实现其抽象方法。
    """

    def __init__(self, name: str, description: str = ""):
        """
        初始化工具。

        Args:
            name: 工具名称
            description: 工具描述
        """
        self.name = name
        self.description = description

    @abstractmethod
    def run(self, input_data: Any, **kwargs) -> Any:
        """
        执行工具的主逻辑。

        Args:
            input_data: 输入数据
            **kwargs: 额外参数

        Returns:
            工具执行结果
        """

    async def arun(self, parameters: dict[str, Any]) -> ToolResponse:
        """异步执行工具

        默认实现：在线程池中运行同步 run() 方法
        子类可以重写此方法实现真正的异步执行

        Args:
            parameters: 工具参数字典

        Returns:
            ToolResponse: 标准化的工具响应对象
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.run(parameters))

    @abstractmethod
    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""

    def get_description(self) -> str:
        """获取工具描述"""
        return self.description

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
