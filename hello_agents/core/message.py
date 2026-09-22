"""
消息系统 - 定义消息类型和消息角色
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class MessageRole(str, Enum):
    """消息角色枚举"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    """消息模型"""

    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    class Config:
        use_enum_values = True

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return self.model_dump(exclude_none=True)
