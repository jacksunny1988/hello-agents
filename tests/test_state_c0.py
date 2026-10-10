"""C0：会话状态对象 AgentState —— 默认值、聚合写入、new_reply、校验与序列化（离线、确定性）"""

import pytest
from pydantic import ValidationError

from hello_agents.model import (
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.state import AgentState


def _assert_hex32(value: str) -> None:
    assert len(value) == 32
    assert all(ch in "0123456789abcdef" for ch in value)


def _call_block() -> ToolCallBlock:
    return ToolCallBlock(name="read_file", arguments='{"path": "a.txt"}', id="call_1")


def _result_block() -> ToolResultBlock:
    return ToolResultBlock(tool_call_id="call_1", output="hi")


def test_default_construction():
    state = AgentState()
    assert state.summary == ""
    assert state.context == []
    assert state.cur_iter == 0
    _assert_hex32(state.session_id)
    _assert_hex32(state.reply_id)


def test_ids_are_unique_across_instances():
    first, second = AgentState(), AgentState()
    assert first.session_id != second.session_id
    assert first.reply_id != second.reply_id


def test_first_append_creates_assistant_message():
    state = AgentState()
    msg = state.append_blocks("coder", [TextBlock(text="你好")])
    assert len(state.context) == 1
    assert msg is state.context[-1]
    assert msg.role == Role.ASSISTANT
    assert msg.name == "coder"
    assert msg.id == state.reply_id


def test_same_reply_accumulates_into_one_message():
    state = AgentState()
    state.append_blocks("coder", [TextBlock(text="看一下")])
    state.append_blocks("coder", [_call_block()])
    assert len(state.context) == 1
    assert [block.type for block in state.context[0].content] == ["text", "tool_call"]

    # 工具结果独立成 role=tool 消息，不与 assistant 消息合并
    state.append_tool_result(_result_block())
    assert len(state.context) == 2
    assert state.context[-1].role == Role.TOOL
    assert [block.type for block in state.context[-1].content] == ["tool_result"]


def test_different_name_does_not_merge():
    state = AgentState()
    state.append_blocks("coder", [TextBlock(text="一")])
    state.append_blocks("planner", [TextBlock(text="二")])
    assert len(state.context) == 2
    assert [msg.name for msg in state.context] == ["coder", "planner"]


def test_new_reply_resets_id_and_iter():
    state = AgentState()
    state.append_blocks("coder", [TextBlock(text="一")])
    old_reply_id = state.reply_id
    state.cur_iter = 3

    new_reply_id = state.new_reply()

    assert new_reply_id != old_reply_id
    assert state.reply_id == new_reply_id
    assert state.cur_iter == 0
    state.append_blocks("coder", [TextBlock(text="二")])
    assert len(state.context) == 2
    assert state.context[-1].id == new_reply_id


def test_empty_blocks_raises():
    state = AgentState()
    with pytest.raises(ValueError, match="至少一个内容块"):
        state.append_blocks("coder", [])


def test_extra_field_is_forbidden():
    with pytest.raises(ValidationError):
        AgentState(session_id="x", oops=1)


def test_nested_message_is_validated():
    state = AgentState(
        context=[{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    )
    assert len(state.context) == 1
    assert state.context[0].content[0].text == "hi"


def test_unknown_block_type_is_rejected():
    with pytest.raises(ValidationError):
        AgentState(context=[{"role": "assistant", "content": [{"type": "unknown"}]}])


def test_json_round_trip_preserves_state():
    state = AgentState()
    state.summary = "已压缩的摘要"
    state.append_blocks("coder", [ThinkingBlock(thinking="先看文件")])
    state.append_blocks("coder", [TextBlock(text="想好了")])
    state.append_blocks("coder", [_call_block()])
    state.append_tool_result(_result_block())

    restored = AgentState.model_validate_json(state.model_dump_json())

    assert restored == state
    # 4 类块齐全，但分布在 2 条消息里：assistant(思考/正文/调用) + tool(结果)
    assert [block.type for block in restored.context[0].content] == [
        "thinking",
        "text",
        "tool_call",
    ]
    assert [block.type for block in restored.context[1].content] == ["tool_result"]
