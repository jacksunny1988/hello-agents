"""上下文工程

在调用 LLM 前组装有限的上下文窗口：收集候选信息包，按相关性与
近因性筛选排序，组织成结构化模板，超出 token 预算时压缩。

典型用法：
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    prompt = builder.build(
        user_query="帮我总结上周的会议纪要",
        conversation_history=history,
        system_instructions="你是一个严谨的助理。",
    )
"""

from .base import ContextConfig, ContextPacket

__all__ = [
    "ContextConfig",
    "ContextPacket",
]
