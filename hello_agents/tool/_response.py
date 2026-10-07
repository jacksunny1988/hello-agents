import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._types import ToolStatus


class ToolTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ToolResponse(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: ToolStatus = ToolStatus.SUCCESS
    content: list[ToolTextBlock] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def get_text(self) -> str:
        """把所有文本块按顺序拼成一个字符串。"""
        return "".join(block.text for block in self.content)

    @classmethod
    def succeed(cls, text: str, **metadata: object) -> "ToolResponse":
        """成功结果：单段文本，status=SUCCESS。"""
        return cls(
            status=ToolStatus.SUCCESS,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )

    @classmethod
    def fail(cls, text: str, **metadata: object) -> "ToolResponse":
        """失败结果：错误说明文本，status=ERROR。"""
        return cls(
            status=ToolStatus.ERROR,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )

    @classmethod
    def denied(cls, reason: str | None = None, **metadata: object) -> "ToolResponse":
        """人工确认被拒：status=DENIED。"""
        text = reason or "工具调用被用户拒绝"
        return cls(
            status=ToolStatus.DENIED,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )
