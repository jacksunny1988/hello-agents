import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, Field

from ._usage import ChatUsage
from .message import TextBlock, ThinkingBlock

# 目前能按 id 累加的两种块；M6 的 ToolCallBlock 要按 index/id 对齐，届时另走一条路
BlockT = TypeVar("BlockT", TextBlock, ThinkingBlock)


class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"  # M5 才会真正用到


class ChatResponse(BaseModel):
    """统一响应模型。

    纯数据模型，不认识任何 SDK 类型——入站的两个入口都在 `_formatter`：
    非流式走 `from_completion`，流式逐帧走 `parse_chunk`。

    流式时它同时充当**累加器**：每来一片增量就 `append_chat_response`，
    收尾的实例即完整响应（正文/思考分别拼好，usage 已吸收）。
    """

    content: list[TextBlock | ThinkingBlock] = Field(default_factory=list)
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage: ChatUsage | None = None
    finished_reason: FinishedReason = FinishedReason.COMPLETED
    is_last: bool = True  # 流式增量恒 False；收尾的完整响应为 True

    def _find_block(self, block_type: type[BlockT], block_id: str) -> BlockT | None:
        """找同类型同 id 的块。找不到返回 None。

        只按 (类型, id) 匹配，不看位置——真实 id 唯一时这是无歧义的。
        匿名块（id=""，见 `_formatter.parse_chunk`）共享同一个身份，所以
        「思考…思考…正文…正文…」这种正常流各自并成一个块。
        """
        for block in self.content:
            if isinstance(block, block_type) and block.id == block_id:
                return block
        return None

    def append_text(self, text: str, block_id: str | None = None) -> None:
        block_id = block_id or ""
        block = self._find_block(TextBlock, block_id)
        if block is None:
            self.content.append(TextBlock(text=text, id=block_id))
        else:
            block.text += text

    def append_thinking(self, thinking: str, block_id: str | None = None) -> None:
        """把一段思考增量并进累计结果。语义同 `append_text`。"""
        block_id = block_id or ""
        block = self._find_block(ThinkingBlock, block_id)
        if block is None:
            self.content.append(ThinkingBlock(thinking=thinking, id=block_id))
        else:
            block.thinking += thinking

    def append_chat_response(self, delta: "ChatResponse") -> "ChatResponse":

        for block in delta.content:
            if isinstance(block, TextBlock):
                self.append_text(block.text, block.id)
            elif isinstance(block, ThinkingBlock):
                self.append_thinking(block.thinking, block.id)
            else:
                raise NotImplementedError(
                    f"累加 {type(block).__name__} 属于 M6：tool_call 的分片要按 "
                    f"index/id 对齐拼接，不能当普通块追加"
                )

        if delta.usage is not None:
            self.usage = delta.usage
        if delta.id:
            self.id = delta.id
        return self

    @classmethod
    def from_completion(cls, completion: object, elapsed: float) -> "ChatResponse":
        from ._formatter import from_completion

        return from_completion(completion, elapsed)
