"""C3：ContextConfig / ContextManager —— 阈值判定、守卫与短路（离线、确定性）"""

from typing import Any, ClassVar, cast

import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from hello_agents.model import (
    ChatModelBase,
    Message,
    ModelConfig,
    Provider,
    TextBlock,
    Tool,
)
from hello_agents.state import (
    AgentState,
    ContextConfig,
    ContextManager,
    ContextOverflowError,
    build_model_input,
)

SYSTEM_PROMPT = "你是一个助手。"


class _StubModel(ChatModelBase):
    """`fixed` 给定时 count_tokens 返回定值，用来钉死阈值边界。"""

    def __init__(self, *, fixed: int | None = None, context_size: int = 100) -> None:
        super().__init__(
            config=ModelConfig(
                provider=Provider.DEEPSEEK,
                base_url="http://stub",
                model="stub",
                context_size=context_size,
            ),
            client=cast(AsyncOpenAI, None),
        )
        self._fixed = fixed

    async def _call_api(
        self, messages, stream, tools=None, tool_choice=None, response_format=None
    ) -> None:
        raise AssertionError("阈值判定不该发请求")

    async def count_tokens(self, messages, tools=None) -> int:
        if self._fixed is None:
            return await super().count_tokens(messages, tools)
        return self._fixed


class _EchoTool(Tool):
    name = "echo"
    description = "回显文本"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, arguments: dict) -> str:
        return str(arguments.get("text", ""))


def _state() -> AgentState:
    state = AgentState()
    state.context.append(Message.user("你好"))
    state.append_blocks("coder", [TextBlock(text="你好，有什么可以帮你？")])
    return state


# --------------------------------------------------------------------------- #
# 配置层：默认值与取值域
# --------------------------------------------------------------------------- #


def test_config_defaults():
    config = ContextConfig()

    assert config.trigger_ratio == 0.8
    assert config.reserve_ratio == 0.1


@pytest.mark.parametrize("ratio", [0.9, 0.95, 0.0, -0.1])
def test_trigger_ratio_out_of_range_is_rejected(ratio: float):
    # 0.9 与 0.0 都取不到：压缩本身要花 token，得给这次"读"留出余量
    with pytest.raises(ValidationError):
        ContextConfig(trigger_ratio=ratio)


@pytest.mark.parametrize("ratio", [0.9, 0.0])
def test_reserve_ratio_out_of_range_is_rejected(ratio: float):
    with pytest.raises(ValidationError):
        ContextConfig(reserve_ratio=ratio)


def test_extra_field_is_forbidden():
    with pytest.raises(ValidationError):
        ContextConfig(unknown=1)


# --------------------------------------------------------------------------- #
# 阈值层：比率 × 窗口
# --------------------------------------------------------------------------- #


def test_threshold_is_ratio_times_context_size():
    manager = ContextManager(_StubModel(context_size=100))

    assert manager.threshold_tokens == 80.0


def test_threshold_follows_injected_config():
    manager = ContextManager(
        _StubModel(context_size=100), config=ContextConfig(trigger_ratio=0.5)
    )

    assert manager.threshold_tokens == 50.0


# --------------------------------------------------------------------------- #
# 估算层：与真实请求同源（这组是 C1/C2 铺垫的意义所在）
# --------------------------------------------------------------------------- #


async def test_estimated_tokens_is_same_source_as_build_model_input():
    model = _StubModel()
    manager = ContextManager(model)
    state = _state()

    estimated = await manager.estimated_tokens(state, system_prompt=SYSTEM_PROMPT)
    manual = await model.count_tokens(
        **build_model_input(state, system_prompt=SYSTEM_PROMPT)
    )

    assert estimated == manual


async def test_tools_are_counted_in_the_estimate():
    manager = ContextManager(_StubModel())
    state = _state()

    without = await manager.estimated_tokens(state, system_prompt=SYSTEM_PROMPT)
    with_tools = await manager.estimated_tokens(
        state, system_prompt=SYSTEM_PROMPT, tools=[_EchoTool()]
    )

    # 走 C2 的 schema 计数：加上工具描述，估算只会变大
    assert with_tools > without


# --------------------------------------------------------------------------- #
# 判定层：边界方向必须钉死（>= 触发，不是 >）
# --------------------------------------------------------------------------- #


async def test_estimated_equal_to_threshold_triggers():
    # fixed=80 恰好等于 0.8 × 100：到线就压
    manager = ContextManager(_StubModel(fixed=80, context_size=100))

    assert (
        await manager.needs_compression(AgentState(), system_prompt=SYSTEM_PROMPT)
        is True
    )


async def test_just_below_threshold_does_not_trigger():
    manager = ContextManager(_StubModel(fixed=79, context_size=100))

    assert (
        await manager.needs_compression(AgentState(), system_prompt=SYSTEM_PROMPT)
        is False
    )


# --------------------------------------------------------------------------- #
# 短路与守卫层
# --------------------------------------------------------------------------- #


async def test_below_threshold_short_circuits_without_touching_state():
    manager = ContextManager(_StubModel(fixed=10, context_size=100))
    state = _state()
    before = state.model_dump()

    assert await manager.compress(state, system_prompt=SYSTEM_PROMPT) is False
    assert state.summary == before["summary"]
    assert len(state.context) == len(before["context"])
    assert state.cur_iter == before["cur_iter"]
    assert state.model_dump() == before


async def test_overflow_with_empty_context_blames_the_system_prompt():
    manager = ContextManager(_StubModel(fixed=80, context_size=100))
    state = AgentState()  # 空 context、空 summary

    with pytest.raises(ContextOverflowError) as excinfo:
        await manager.compress(state, system_prompt=SYSTEM_PROMPT)

    message = str(excinfo.value)
    assert "system prompt" in message
    assert "80" in message
    # 没有摘要时不该提"压缩摘要"——那是另一种修法
    assert "压缩摘要" not in message


async def test_overflow_with_summary_names_the_summary_too():
    manager = ContextManager(_StubModel(fixed=80, context_size=100))
    state = AgentState()
    state.summary = "一段很长的旧摘要"

    with pytest.raises(ContextOverflowError) as excinfo:
        await manager.compress(state, system_prompt=SYSTEM_PROMPT)

    assert "压缩摘要" in str(excinfo.value)


async def test_overflow_with_history_points_at_c4():
    manager = ContextManager(_StubModel(fixed=80, context_size=100))
    state = _state()

    with pytest.raises(NotImplementedError) as excinfo:
        await manager.compress(state, system_prompt=SYSTEM_PROMPT)

    assert "C4" in str(excinfo.value)
