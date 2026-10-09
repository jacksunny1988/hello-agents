from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import ToolBase


class ToolStatus(StrEnum):
    SUCCESS = "success"  # 正常完成
    ERROR = "error"  # 工具自身报错（已被捕获、转成结构化结果）
    DENIED = "denied"  # 人工确认被拒绝
    INTERRUPTED = "interrupted"  # 执行中被取消


@dataclass
class RegisteredTool:
    """工具 + 它在注册中心里的管理信息。"""

    tool: "ToolBase"
    group: str = "basic"
