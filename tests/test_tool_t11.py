"""T11：主循环离线测试——脚本化假模型驱动，验证消息顺序/停止/并行/轮次上限"""

import sys
from pathlib import Path

import pytest

from hello_agents.bridge import model_tools_for
from hello_agents.model._response import ChatResponse, FinishedReason
from hello_agents.model.message import TextBlock, ToolCallBlock
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit

# examples/ 不在 pytest 收集范围、也无 __init__.py；沿用 test_model_examples.py 的约定
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
from agent_t11 import run_agent


def _toolkit():
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def echo(message: str) -> str:
        """回显。

        Args:
            message: 文本
        """
        return f"echo:{message}"

    toolkit.register_function(echo)
    return toolkit


class ScriptedModel:
    """按预设顺序返回 ChatResponse；记录每次收到的消息数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.observed = []

    async def __call__(self, messages, tools=None):
        self.observed.append((len(messages), tools))
        return self._responses.pop(0)


def _text_response(text):
    return ChatResponse(
        content=[TextBlock(text=text)],
        finished_reason=FinishedReason.COMPLETED,
    )


def _call_response(name, arguments, call_id, finish=FinishedReason.TOOL_CALLS):
    return ChatResponse(
        content=[ToolCallBlock(name=name, arguments=arguments, id=call_id)],
        finished_reason=finish,
    )


async def test_direct_answer_stops_in_one_round():
    model = ScriptedModel([_text_response("你好，这是答案")])
    final = await run_agent(model, _toolkit(), [], verbose=False)
    assert "答案" in final.content[0].text
    assert len(model.observed) == 1


async def test_tool_then_answer_has_correct_message_order():
    messages = []
    model = ScriptedModel(
        [
            _call_response("echo", '{"message":"hi"}', "c1"),
            _text_response("完成"),
        ]
    )
    await run_agent(model, _toolkit(), messages, verbose=False)

    # 历史顺序：assistant(tool_call) → tool(result) → assistant(final)
    roles = [m.role.value for m in messages]
    assert roles == ["assistant", "tool", "assistant"]
    assert messages[1].content[0].tool_call_id == "c1"
    assert messages[1].content[0].output == "echo:hi"


async def test_two_parallel_calls_both_execute():
    messages = []
    model = ScriptedModel(
        [
            ChatResponse(
                content=[
                    ToolCallBlock(
                        name="echo",
                        arguments='{"message":"a"}',
                        id="c1",
                    ),
                    ToolCallBlock(
                        name="echo",
                        arguments='{"message":"b"}',
                        id="c2",
                    ),
                ],
                finished_reason=FinishedReason.TOOL_CALLS,
            ),
            _text_response("done"),
        ]
    )
    await run_agent(model, _toolkit(), messages, verbose=False)

    tool_msgs = [m for m in messages if m.role.value == "tool"]
    assert len(tool_msgs) == 2
    outputs = {m.content[0].output for m in tool_msgs}
    assert outputs == {"echo:a", "echo:b"}


async def test_max_rounds_raises():
    looping = ScriptedModel(
        [_call_response("echo", '{"message":"x"}', f"c{i}") for i in range(10)]
    )
    with pytest.raises(RuntimeError, match="最大轮次"):
        await run_agent(looping, _toolkit(), [], max_rounds=3, verbose=False)


def test_model_tools_for_wraps_registered_tools():
    shells = list(model_tools_for(_toolkit()))
    assert len(shells) == 1
    spec = shells[0].function_spec()
    assert spec["function"]["name"] == "echo"
