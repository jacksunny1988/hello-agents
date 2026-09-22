"""
Agent基类 - 所有智能体的基础抽象类
"""

from abc import ABC, abstractmethod
from typing import Any

from .config import Config
from .llm import HelloAgentsLLM
from .message import Message, MessageRole


class Agent(ABC):
    """
    智能体基类，定义了所有智能体必须实现的接口。

    所有具体的智能体实现（如ReActAgent、PlanAndSolveAgent等）
    都应该继承这个基类并实现其抽象方法。
    """

    def __init__(
        self,
        name: str,
        llm_client: HelloAgentsLLM,
        config: Config | None = None,
        description: str = "",
    ):
        """
        初始化智能体。

        Args:
            name: 智能体名称
            llm_client: LLM客户端实例
            config: 配置对象，可选
            description: 智能体描述
        """
        self.system_prompt: str | None = None
        self.name = name
        self.llm_client = llm_client
        self.config = config or Config()
        self.description = description
        self._history: list[Message] = []

    @abstractmethod
    def run(self, task: str, **kwargs) -> Any:
        """
        执行智能体的主任务。

        Args:
            task: 任务描述
            **kwargs: 额外参数

        Returns:
            任务执行结果
        """

    def add_message(self, role: MessageRole, content: str):
        """添加消息到历史记录"""
        message = Message(role=role, content=content)
        self._history.append(message)

    def get_history(self) -> list[Message]:
        """获取历史消息列表"""
        return self._history.copy()

    def clear_history(self):
        """清空历史记录"""
        self._history.clear()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
