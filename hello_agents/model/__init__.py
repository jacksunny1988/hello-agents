"""统一模型层

在「厂商 SDK」与「上层 Agent」之间插一层：上层只依赖 `Message` / `ChatResponse`
这套统一模型，换厂商不动业务代码。

消息之所以建模成 `Message + 若干 Block`，而不是 `role + content 字符串`，
是因为一次 assistant 回复可能**同时**包含思考、正文与多个工具调用；而工具结果
又必须以独立角色回灌给模型。字符串塞不下这种结构。

典型用法：
    from hello_agents.model import Message, Role, TextBlock

    msg = Message(role=Role.USER, content=[TextBlock(text="你好")])
"""

from ._base import ChatModelBase
from ._registry import (
    MissingAPIKeyError,
    ModelConfig,
    Provider,
    build_client,
    get_api_key,
    get_client,
    get_model_config,
    parse_spec,
)
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

__all__ = [
    "ChatModelBase",
    "ChatResponse",
    "ChatUsage",
    "FinishedReason",
    "Message",
    "MissingAPIKeyError",
    "ModelConfig",
    "Provider",
    "Role",
    "TextBlock",
    "ThinkingBlock",
    "ToolCallBlock",
    "ToolResultBlock",
    "build_client",
    "get_api_key",
    "get_client",
    "get_model_config",
    "parse_spec",
]
