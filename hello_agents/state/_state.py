"""会话状态对象：Agent 的「记忆」唯一真相源，可序列化、可恢复、可注入测试。"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from ..model import (
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)

# 一条消息里允许出现的块类型（与 model/message.py 的联合类型保持一致）
ContentBlock = TextBlock | ThinkingBlock | ToolCallBlock | ToolResultBlock


class AgentState(BaseModel):
    """会话状态对象：Agent 的「记忆」唯一真相源，可序列化、可恢复、可注入测试。"""

    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4().hex), description="会话 ID"
    )
    summary: str = Field(default="", description="会话摘要")
    context: list[Message] = Field(
        default_factory=list, description="会话上下文消息列表"
    )
    reply_id: str = Field(
        default_factory=lambda: str(uuid.uuid4().hex), description="当前回复 ID"
    )
    cur_iter: int = Field(default=0, description="当前回复内已进行的循环轮次")

    def new_reply(self) -> str:
        """开始新的回复，重置当前回复 ID 和 ReAct 循环轮次。"""
        self.reply_id = str(uuid.uuid4().hex)
        self.cur_iter = 0
        return self.reply_id

    def append_blocks(self, name: str, blocks: list[ContentBlock]) -> Message:
        """把内容块写入上下文，返回被写入的那条消息。

        只接受**模型产出**的块（思考 / 正文 / 工具调用）。工具结果请走
        :meth:`append_tool_result`——因为 model 层的 formatter 明确要求
        `ToolResultBlock` 只能出现在 `role=tool` 的消息里。

        Args:
            name: 写入者名称；只有同名消息才会被合并。
            blocks: 至少一个内容块，且不得包含 `ToolResultBlock`。

        Returns:
            被写入的那条 Message（就是 context 的末条）。
        """
        if not blocks:
            raise ValueError("append_blocks 需要至少一个内容块")

        if any(isinstance(b, ToolResultBlock) for b in blocks):
            raise ValueError(
                "工具结果不能写进 assistant 消息（formatter 只接受 role=tool），"
                "请改用 append_tool_result()"
            )

        last = self.context[-1] if self.context else None
        if (
            last is not None
            and last.role == Role.ASSISTANT
            and last.name == name
            and last.id == self.reply_id
        ):
            # 如果上一条消息是同一个助手的回复，则追加到上一条消息中
            last.content.extend(blocks)
            return last
        msg = Message(
            role=Role.ASSISTANT, name=name, id=self.reply_id, content=list(blocks)
        )
        self.context.append(msg)
        return msg

    def append_tool_result(self, block: ToolResultBlock) -> Message:
        """追加一条工具结果消息（`role=tool`），返回它。

        每个结果单独成一条消息：与该消息的 `tool_call_id` 一起，构成
        formatter 出站时的配对依据；也与你 T10 桥接层的既有约定一致。
        """
        msg = Message(role=Role.TOOL, content=[block])
        self.context.append(msg)
        return msg
