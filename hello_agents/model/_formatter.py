"""统一消息模型 ↔ OpenAI 格式的**双向**转换。

本模块是**唯一**知道 OpenAI 格式长什么样的地方，两个方向都在这里：

- `to_openai_messages`：`Message` → OpenAI dict（出站）
- `from_completion`   ：OpenAI completion → `ChatResponse`（入站，非流式）
- `parse_chunk`       ：OpenAI 流的一帧 → 增量 `ChatResponse`（入站，流式）

模型类与上层只碰 `Message` / `ChatResponse`，将来换协议只改这里，
或新增一个 formatter。
"""

import uuid
from datetime import UTC, datetime

from ._response import ChatResponse, FinishedReason
from ._usage import ChatUsage
from .message import (
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def to_openai_messages(messages: list[Message]) -> list[dict]:
    """把统一消息模型转成 `chat.completions.create(messages=...)` 要的列表。

    M3 只处理文本：

    - 一条 `Message` 转成一个 dict，`content` 为其中所有 `TextBlock.text`
      按原顺序拼接的结果；
    - `ThinkingBlock` 直接忽略——思考链是否回传各家规则不同（M4/M6 再处理），
      不能让它混进 `content`；
    - system / user / assistant 一律按上述规则处理，没有例外分支。

    工具相关的转换（`ToolCallBlock` / `ToolResultBlock` / `role=tool`）属于 M6。
    这里**显式报错而不是静默丢弃**：工具调用一旦被悄悄吞掉，模型会收到一条
    缺了上下文的对话，而错误要等到很远的地方才暴露。
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role is Role.TOOL or any(
            isinstance(b, (ToolCallBlock, ToolResultBlock)) for b in msg.content
        ):
            raise NotImplementedError(
                f"工具消息的转换属于 M6，M3 的 Formatter 只处理文本"
                f"（role={msg.role.value}）"
            )

        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        out.append({"role": msg.role.value, "content": text})
    return out


def _as_int(value: object) -> int:
    """用量字段在不同 SDK 版本可能是 None，统一兜底为 0。"""
    return value if isinstance(value, int) else 0


def from_completion(completion: object, elapsed: float) -> ChatResponse:
    """把非流式 ChatCompletion 解析成 `ChatResponse`。

    对「空 choices」「content 为 None」「缺 usage」「缺 id / created」都做兜底，
    不抛索引错误。
    """
    choices = getattr(completion, "choices", None) or []

    content: list[TextBlock | ThinkingBlock] = []
    if choices:
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if text:  # None 与 "" 都不产出空块
            content.append(TextBlock(text=text))
    # M3 只取正文；reasoning_content 之类的思考字段留到 M4（见规格 §2.4）

    usage_obj = getattr(completion, "usage", None)
    prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
    usage = ChatUsage(
        input_tokens=_as_int(getattr(usage_obj, "prompt_tokens", None)),
        output_tokens=_as_int(getattr(usage_obj, "completion_tokens", None)),
        cache_read_tokens=_as_int(getattr(prompt_details, "cached_tokens", None)),
        time=elapsed,
    )

    created = getattr(completion, "created", None)
    created_at = (
        datetime.fromtimestamp(created, tz=UTC).isoformat()
        if isinstance(created, (int, float))
        else datetime.now(UTC).isoformat()
    )

    return ChatResponse(
        content=content,
        id=getattr(completion, "id", None) or uuid.uuid4().hex,
        created_at=created_at,
        usage=usage,
        # M3 恒为 COMPLETED；choices[0].finish_reason 的映射留到 M4/M6
        finished_reason=FinishedReason.COMPLETED,
        is_last=True,
    )


def parse_chunk(chunk: object) -> ChatResponse:
    choices = getattr(chunk, "choices", None) or []

    content: list[TextBlock | ThinkingBlock] = []
    if choices:
        delta = getattr(choices[0], "delta", None)
        thinking = getattr(delta, "reasoning_content", None)
        text = getattr(delta, "content", None)
        # 同一片里思考排在正文前：推理模型总是先想后答，按时间序放前面，
        # 累加时块的先后就自然还原了「先思考、后答案」。
        if thinking:
            content.append(ThinkingBlock(thinking=thinking, id=""))
        if text:
            content.append(TextBlock(text=text, id=""))

    usage_obj = getattr(chunk, "usage", None)
    usage = None
    if usage_obj is not None:
        prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
        usage = ChatUsage(
            input_tokens=_as_int(getattr(usage_obj, "prompt_tokens", None)),
            output_tokens=_as_int(getattr(usage_obj, "completion_tokens", None)),
            cache_read_tokens=_as_int(getattr(prompt_details, "cached_tokens", None)),
            # 单帧没有耗时概念；总耗时由驱动流的基类在收尾时写进最终响应
            time=0.0,
        )

    return ChatResponse(
        content=content,
        # 空 id 表示这片没带 id（少见）。累加器只认非空 id，否则每片都会
        # 用随机值把最终结果的 id 冲掉。
        id=getattr(chunk, "id", None) or "",
        usage=usage,
        # 增量的 finished_reason 无意义（真值只在末片），收尾由基类统一置位
        is_last=False,
    )
