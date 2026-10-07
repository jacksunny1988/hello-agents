from enum import StrEnum


class ToolStatus(StrEnum):
    SUCCESS = "success"  # 正常完成
    ERROR = "error"  # 工具自身报错（已被捕获、转成结构化结果）
    DENIED = "denied"  # 人工确认被拒绝
    INTERRUPTED = "interrupted"  # 执行中被取消
