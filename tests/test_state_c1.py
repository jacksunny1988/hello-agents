"""C1：上下文组装 build_model_input —— 三段式、引用透传、只读，以及与 formatter 的协议兼容（离线、确定性）"""

import pytest

from hello_agents.model import (
    Message,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.model._formatter import to_openai_messages
from hello_agents.state import AgentState, build_model_input

SYSTEM_PROMPT = "你是一个严谨的编码助手，回答前先读文件确认事实。"


def _call_block() -> ToolCallBlock:
    return ToolCallBlock(name="read_file", arguments='{"path": "a.txt"}', id="call_1")


def _realistic_state() -> AgentState:
    """user → assistant(正文 + 工具调用) → tool(结果)，并带一段非空摘要。"""
    state = AgentState()
    state.summary = "此前你已读过 a.txt，内容是 hello。"
    state.context.append(Message.user("把 a.txt 的内容总结成一句话"))
    state.append_blocks("coder", [TextBlock(text="我先读一下文件"), _call_block()])
    state.append_tool_result(
        ToolResultBlock(tool_call_id="call_1", output="hello", name="read_file")
    )
    return state


# --------------------------------------------------------------------------- #
# 组装部分：三段式、透传、键集合、只读
# --------------------------------------------------------------------------- #


def test_three_segment_order_with_summary():
    state = _realistic_state()

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT)

    assert [m.role for m in out["messages"]] == [
        Role.SYSTEM,
        Role.USER,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
    ]
    assert out["messages"][0].content[0].text == SYSTEM_PROMPT
    assert out["messages"][1].content[0].text == state.summary
    # ③ 未压缩历史原样展开、保时间序
    assert out["messages"][2:] == state.context


def test_empty_summary_omits_user_segment():
    state = AgentState()
    state.append_blocks("coder", [TextBlock(text="你好")])

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT)

    assert [m.role for m in out["messages"]] == [Role.SYSTEM, Role.ASSISTANT]
    assert out["messages"][0].content[0].text == SYSTEM_PROMPT


def test_context_messages_are_passed_by_reference():
    state = _realistic_state()

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT)

    # identity：不是拷贝，是同一批对象（summary 占位 1，故 context 从下标 2 起）
    assert out["messages"][2] is state.context[0]
    assert out["messages"][-1] is state.context[-1]


def test_tools_none_stays_none():
    out = build_model_input(AgentState(), system_prompt=SYSTEM_PROMPT)

    # 没有工具时必须是 None，不能被兜底成 []（空数组在部分端点语义不同）
    assert out["tools"] is None


def test_tools_list_is_passed_by_reference():
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    out = build_model_input(AgentState(), system_prompt=SYSTEM_PROMPT, tools=tools)

    assert out["tools"] is tools


def test_keys_are_fixed():
    out = build_model_input(AgentState(), system_prompt=SYSTEM_PROMPT)

    # 多一个键，count_tokens(**out) 会在远处 TypeError——这里钉死键集合
    assert set(out) == {"messages", "tools"}


def test_build_model_input_is_read_only():
    state = _realistic_state()
    before = state.model_dump()

    build_model_input(state, system_prompt=SYSTEM_PROMPT, tools=[{"x": 1}])

    assert state.summary == before["summary"]
    assert len(state.context) == len(before["context"])
    assert state.cur_iter == before["cur_iter"]
    # 更严的兜底：整份状态一个字节都没变
    assert state.model_dump() == before


def test_empty_state_yields_only_system():
    out = build_model_input(AgentState(), system_prompt=SYSTEM_PROMPT)

    assert len(out["messages"]) == 1
    assert out["messages"][0].role == Role.SYSTEM
    assert out["messages"][0].content[0].text == SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 协议兼容部分：真验收 —— 组装结果能进 formatter，且配对正确
# --------------------------------------------------------------------------- #


def test_formatter_accepts_assembled_input():
    state = _realistic_state()

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT)
    openai_messages = to_openai_messages(out["messages"])  # 不抛错

    assert [m["role"] for m in openai_messages] == [
        "system",
        "user",
        "user",
        "assistant",
        "tool",
    ]
    # assistant 同时带正文与工具调用：正文不能丢
    assistant = openai_messages[3]
    assert assistant["content"] == "我先读一下文件"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    # 工具结果独立成一条，靠 tool_call_id 与调用配对
    tool = openai_messages[4]
    assert tool["tool_call_id"] == "call_1"
    assert tool["content"] == "hello"


def test_append_blocks_rejects_tool_result():
    state = AgentState()

    with pytest.raises(ValueError, match="append_tool_result"):
        state.append_blocks(
            "coder",
            [_call_block(), ToolResultBlock(tool_call_id="call_1", output="hi")],
        )

    # 报错发生在写入之前，上下文未被污染
    assert state.context == []


def test_append_tool_result_forms_tool_message_and_formatter_pairs():
    state = AgentState()
    state.append_blocks("coder", [TextBlock(text="读文件"), _call_block()])

    msg = state.append_tool_result(
        ToolResultBlock(tool_call_id="call_1", output="hello", name="read_file")
    )

    assert msg is state.context[-1]
    assert msg.role == Role.TOOL
    # 约定：工具结果消息不复用 reply_id，它靠 tool_call_id 配对
    assert msg.id != state.reply_id

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT)
    openai_messages = to_openai_messages(out["messages"])
    assert openai_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "hello",
        "name": "read_file",
    }
