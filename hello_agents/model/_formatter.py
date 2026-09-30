"""统一消息模型 ↔ OpenAI 格式的**双向**转换。

本模块是**唯一**知道 OpenAI 格式长什么样的地方，两个方向都在这里：

- `to_openai_messages`：`Message` → OpenAI dict（出站）
- `from_completion`   ：OpenAI completion → `ChatResponse`（入站，非流式）
- `parse_chunk`       ：OpenAI 流的一帧 → 增量 `ChatResponse`（入站，流式）

模型类与上层只碰 `Message` / `ChatResponse`，将来换协议只改这里，
或新增一个 formatter。
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from ._response import ChatResponse, FinishedReason
from ._tool import Tool
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

    - 普通消息：`content` 为其中所有 `TextBlock.text` 按原顺序拼接的结果；
    - assistant 含 `ToolCallBlock`：转成 `content` + `tool_calls` 两个字段，
      `content` 取 `text or None`（模型可能「先说一句再调工具」，正文不能丢）；
    - role=tool 含 `ToolResultBlock`：**每个结果展开一条** dict；
    - `ThinkingBlock` 一律忽略——思考链是否回传各家规则不同，不能混进 `content`。

    结构约束违反时**显式报错而不是静默丢弃**：工具消息一旦被悄悄吞掉，模型会收到
    一段缺了上下文的对话，而错误要等到很远的地方才暴露。
    """
    out: list[dict] = []
    for msg in messages:
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        tool_calls = [b for b in msg.content if isinstance(b, ToolCallBlock)]
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]

        if tool_results:
            if msg.role is not Role.TOOL:
                raise ValueError(
                    f"ToolResultBlock 只能出现在 role=tool 的消息里"
                    f"（收到 role={msg.role.value}）"
                )
            if len(tool_results) != len(msg.content):
                stray = next(
                    b for b in msg.content if not isinstance(b, ToolResultBlock)
                )
                raise ValueError(
                    f"role=tool 的消息只能包含 ToolResultBlock"
                    f"（混入了 {type(stray).__name__}）"
                )
            out.extend(_tool_result_dict(b) for b in tool_results)
            continue

        if msg.role is Role.TOOL:
            raise ValueError(
                "role=tool 的消息至少要有一个 ToolResultBlock，"
                "否则它没有任何可回灌的内容"
            )

        if tool_calls:
            if msg.role is not Role.ASSISTANT:
                raise ValueError(
                    f"ToolCallBlock 只能出现在 role=assistant 的消息里"
                    f"（收到 role={msg.role.value}）"
                )
            out.append(
                {
                    "role": msg.role.value,
                    "content": text or None,
                    "tool_calls": [_tool_call_dict(tc) for tc in tool_calls],
                }
            )
            continue

        out.append({"role": msg.role.value, "content": text})
    return out


def to_openai_tools(tools: Sequence[Tool]) -> list[dict]:
    """产出 `chat.completions.create(tools=...)` 要的列表。"""
    return [tool.function_spec() for tool in tools]


def _tool_call_dict(tool_call: ToolCallBlock) -> dict:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {"name": tool_call.name, "arguments": tool_call.arguments},
    }


def _tool_result_dict(block: ToolResultBlock) -> dict:
    out = {
        "role": Role.TOOL.value,
        "tool_call_id": block.tool_call_id,
        "content": block.output,
    }
    if block.name:
        out["name"] = block.name
    return out


def _as_int(value: object) -> int:
    """用量字段在不同 SDK 版本可能是 None，统一兜底为 0。"""
    return value if isinstance(value, int) else 0


def _map_finish_reason(raw: object) -> FinishedReason:
    """`finish_reason` 字符串 → `FinishedReason`。

    只认 `tool_calls`；`stop` / `length` / `None` / 未知值都落到 COMPLETED。
    `length` 的截断语义留到 M7 处理（见设计规格 §8）。
    """
    return (
        FinishedReason.TOOL_CALLS if raw == "tool_calls" else FinishedReason.COMPLETED
    )


def from_completion(completion: object, elapsed: float) -> ChatResponse:
    """把非流式 ChatCompletion 解析成 `ChatResponse`。

    对「空 choices」「content 为 None」「缺 usage」「缺 id / created」都做兜底，
    不抛索引错误。
    """
    choices = getattr(completion, "choices", None) or []

    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = []
    finished = FinishedReason.COMPLETED
    if choices:
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if text:  # None 与 "" 都不产出空块
            content.append(TextBlock(text=text))
        # tool_calls 的 arguments 原样搬运（是字符串），json.loads 归
        # execute_tool_calls——那里才需要把它变成 dict。
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            content.append(
                ToolCallBlock(
                    id=getattr(tool_call, "id", None) or "",
                    name=getattr(function, "name", None) or "",
                    arguments=getattr(function, "arguments", None) or "",
                )
            )
        finished = _map_finish_reason(getattr(choices[0], "finish_reason", None))
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
        finished_reason=finished,
        is_last=True,
    )


def parse_chunk(chunk: object) -> ChatResponse:
    choices = getattr(chunk, "choices", None) or []

    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = []
    finished = FinishedReason.COMPLETED
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
        # tool_calls 排在正文之后：实测三家不会在同一帧里混发两者，
        # 这个顺序只是给「万一混发」定个确定行为。
        for tool_call in getattr(delta, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            content.append(
                ToolCallBlock(
                    # 续片没有 id/name，用空串（匿名）——与文本块同一个理由：
                    # 随机 id 会让每片都建新块。区别是并行多路不能靠空 id 合并，
                    # 必须靠 index 归位。
                    id=getattr(tool_call, "id", None) or "",
                    name=getattr(function, "name", None) or "",
                    arguments=getattr(function, "arguments", None) or "",
                    index=getattr(tool_call, "index", None),
                )
            )
        finished = _map_finish_reason(getattr(choices[0], "finish_reason", None))

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
        # 末片带 finish_reason，这里如实映射；没带帧的落到默认 COMPLETED。
        # 累加器只吸收「非默认值」，所以默认值不会把末片刚写进去的冲掉。
        finished_reason=finished,
        is_last=False,
    )
