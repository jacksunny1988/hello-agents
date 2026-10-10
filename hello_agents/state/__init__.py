"""会话上下文管理

把 Agent 的「记忆」建模成一个可序列化的状态对象，并提供在 token 预算内组装模型输入、
以及把超长历史压缩成结构化摘要的能力。

本包只提供"能力"，不负责"何时调用"——**触发压缩的时机由上层决定**
（examples 主循环，或未来的 Agent 类）。

典型用法：
    from hello_agents.state import AgentState

    state = AgentState()
    state.append_blocks("assistant", [TextBlock(text="你好")])
    print(state.summary, len(state.context))
"""

from ._context import ModelInput, build_model_input
from ._state import AgentState

__all__ = ["AgentState", "ModelInput", "build_model_input"]
