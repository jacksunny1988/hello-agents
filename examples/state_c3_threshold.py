"""C3：压缩入口的阈值判定与守卫 —— 离线（桩模型，不发任何请求）

四件事：比率换算、估算与判定、未达阈值时的短路、超阈值时的两条异常路径。
"""

import asyncio
from typing import cast

from openai import AsyncOpenAI

from hello_agents.model import ChatModelBase, Message, ModelConfig, Provider, TextBlock
from hello_agents.state import (
    AgentState,
    ContextConfig,
    ContextManager,
    ContextOverflowError,
)

SYSTEM_PROMPT = "你是一个助手。"


class _StubModel(ChatModelBase):
    """离线桩：给出 `context_size`，估算走基类的真口径，绝不发请求。"""

    async def _call_api(
        self, messages, stream, tools=None, tool_choice=None, response_format=None
    ) -> None:
        raise AssertionError("阈值判定不该发请求")


def _stub(context_size: int = 100) -> _StubModel:
    return _StubModel(
        config=ModelConfig(
            provider=Provider.DEEPSEEK,
            base_url="http://stub",
            model="stub",
            context_size=context_size,
        ),
        client=cast(AsyncOpenAI, None),
    )


async def main() -> None:
    model = _stub(context_size=100)
    manager = ContextManager(model)  # 默认 trigger_ratio=0.8

    # ① 比率换算：阈值 = trigger_ratio × context_size
    print("context_size:", model.context_size)  # 100
    print("trigger_ratio:", manager.config.trigger_ratio)  # 0.8
    print("threshold_tokens:", manager.threshold_tokens)  # 80.0

    # ② 正常会话：估算远低于阈值 → 不需要压缩
    state = AgentState()
    state.context.append(Message.user("你好"))
    state.append_blocks("coder", [TextBlock(text="你好，有什么可以帮你？")])
    estimated = await manager.estimated_tokens(state, system_prompt=SYSTEM_PROMPT)
    print("estimated_tokens:", estimated)  # 20
    print(
        "needs_compression:",  # False
        await manager.needs_compression(state, system_prompt=SYSTEM_PROMPT),
    )

    # ③ 未达阈值 → compress() 短路返回 False，且 state 一个字节都没动
    before = state.model_dump()
    print("compress 返回:", await manager.compress(state, system_prompt=SYSTEM_PROMPT))
    print("state 前后一致:", state.model_dump() == before)  # True

    # ④ 阈值调到极小 → 两条异常路径：没有历史可压（报错）/ 有历史（交给 C4）
    tight = ContextManager(model, config=ContextConfig(trigger_ratio=0.001))
    print("极小阈值:", tight.threshold_tokens)  # 0.1

    try:
        await tight.compress(AgentState(), system_prompt=SYSTEM_PROMPT)
    except ContextOverflowError as exc:
        print("空 context:", type(exc).__name__, "|", exc)

    try:
        await tight.compress(state, system_prompt=SYSTEM_PROMPT)
    except NotImplementedError as exc:
        print("非空 context:", type(exc).__name__, "|", exc)


if __name__ == "__main__":
    asyncio.run(main())
