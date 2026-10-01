import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, Field

from ._usage import ChatUsage
from .message import Message, Role, TextBlock, ThinkingBlock, ToolCallBlock

# 目前能按 id 累加的两种块；M6 的 ToolCallBlock 要按 index/id 对齐，届时另走一条路
BlockT = TypeVar("BlockT", TextBlock, ThinkingBlock)


class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"  # M5 才会真正用到
    TOOL_CALLS = "tool_calls"  # 这轮模型在请求工具，还没给最终答案
    LENGTH = "length"  # 撞上长度上限被截断（M7 起有消费方：结构化输出的修复提示）


class ChatResponse(BaseModel):
    """统一响应模型。

    纯数据模型，不认识任何 SDK 类型——入站的两个入口都在 `_formatter`：
    非流式走 `from_completion`，流式逐帧走 `parse_chunk`。

    流式时它同时充当**累加器**：每来一片增量就 `append_chat_response`，
    收尾的实例即完整响应（正文/思考分别拼好，usage 已吸收）。
    """

    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = Field(
        default_factory=list
    )
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

    def _find_tool_call(self, block: ToolCallBlock) -> ToolCallBlock | None:
        """找同一个调用的累加块。

        流式分片带 `index`（同一次响应里的第几个调用），非流式没有 index——
        前者按 index 归位（并行多路靠它才不会串），后者退回按 id 匹配。
        """
        for existing in self.content:
            if not isinstance(existing, ToolCallBlock):
                continue
            if block.index is not None:
                if existing.index == block.index:
                    return existing
            elif block.id and existing.id == block.id:
                return existing
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

    def append_tool_call(self, block: ToolCallBlock) -> None:
        """把一片 tool_call 增量并进累计结果。

        arguments 在流式里是分片到达的，每一片单独看都是非法 JSON——所以这里只做
        字符串拼接，`json.loads` 留给 `execute_tool_calls`（那里每个调用的参数才完整）。
        """
        existing = self._find_tool_call(block)
        if existing is None:
            self.content.append(block.model_copy())
            return
        # 首片给了 name / id 之后就不再改：续片这两个字段是空串。
        if block.name and not existing.name:
            existing.name = block.name
        if block.id and not existing.id:
            existing.id = block.id
        existing.arguments += block.arguments

    def append_chat_response(self, delta: "ChatResponse") -> "ChatResponse":
        for block in delta.content:
            if isinstance(block, TextBlock):
                self.append_text(block.text, block.id)
            elif isinstance(block, ThinkingBlock):
                self.append_thinking(block.thinking, block.id)
            elif isinstance(block, ToolCallBlock):
                self.append_tool_call(block)
            else:
                raise NotImplementedError(
                    f"响应方向不会出现 {type(block).__name__}："
                    f"工具结果是出站回灌的内容，不该出现在模型的回复里"
                )

        if delta.usage is not None:
            self.usage = delta.usage
        if delta.id:
            self.id = delta.id
        if delta.finished_reason is not FinishedReason.COMPLETED:
            # COMPLETED 是默认值，等价于「这一帧没说」。载体帧（choices=[]）恒为
            # 默认值，而无条件吸收会把末片刚写进去的 TOOL_CALLS 又冲回去——
            # dashscope 的载体帧正是在末片**之后**到达。
            self.finished_reason = delta.finished_reason
        return self

    def get_tool_calls(self) -> list[ToolCallBlock]:
        return [b for b in self.content if isinstance(b, ToolCallBlock)]

    def to_message(self) -> Message:
        """响应 → 可回灌进消息历史的 assistant 消息。

        角色恒为 assistant（响应只可能来自模型）。`ThinkingBlock` 一并带上，
        由 Formatter 出站时忽略。
        """
        return Message(role=Role.ASSISTANT, content=list(self.content))

    @classmethod
    def from_completion(cls, completion: object, elapsed: float) -> "ChatResponse":
        from ._formatter import from_completion

        return from_completion(completion, elapsed)
