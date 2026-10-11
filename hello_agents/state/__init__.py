"""会话上下文管理

把 Agent 的「记忆」建模成一个可序列化的状态对象，并提供在 token 预算内组装模型输入、
以及把超长历史压缩成结构化摘要的能力。

本包只提供"能力"，不负责"何时调用"——**触发压缩的时机由上层决定**
（examples 主循环，或未来的 Agent 类）。

典型用法：
    from hello_agents.state import AgentState, ContextConfig, ContextManager

    cm = ContextManager(model, config=ContextConfig(trigger_ratio=0.8))
    if await cm.needs_compression(state, system_prompt=sp):
        await cm.compress(state, system_prompt=sp)
"""

from ._config import ContextConfig
from ._context import (
    ContextManager,
    ContextOverflowError,
    ModelInput,
    build_model_input,
)
from ._state import AgentState

__all__ = [
    "AgentState",
    "ContextConfig",
    "ContextManager",
    "ContextOverflowError",
    "ModelInput",
    "build_model_input",
]
