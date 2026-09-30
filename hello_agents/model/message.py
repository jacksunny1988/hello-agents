import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex
    )  # .hex 就是去掉横线的 32 位 hex


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex
    )  # .hex 就是去掉横线的 32 位 hex


class ToolCallBlock(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: str
    id: str
    index: int | None = None  # 流式对齐用（同一次响应里的第几个调用）；非流式为 None


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    output: str
    is_error: bool = False
    name: str | None = None  # 出站 role=tool dict 的 name 字段


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    name: str | None = None
    content: list[
        Annotated[
            TextBlock | ThinkingBlock | ToolCallBlock | ToolResultBlock,
            Field(discriminator="type"),
        ]
    ]
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime | None = None

    def get_text_blocks(self) -> list[TextBlock]:
        return [b for b in self.content if isinstance(b, TextBlock)]

    def get_tool_calls(self) -> list[ToolCallBlock]:
        return [t for t in self.content if isinstance(t, ToolCallBlock)]

    @classmethod
    def user(cls, text: str, name: str | None = None) -> "Message":
        return cls(role=Role.USER, name=name, content=[TextBlock(text=text)])

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role=Role.SYSTEM, content=[TextBlock(text=text)])
