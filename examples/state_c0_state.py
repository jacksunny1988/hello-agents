from hello_agents.model import TextBlock, ToolCallBlock, ToolResultBlock
from hello_agents.state import AgentState


def main() -> None:
    state = AgentState()
    print("session:", state.session_id[:8], "reply:", state.reply_id[:8])
    # 同一次 reply 内多次追加 → 仍然只有一条 assistant 消息
    state.append_blocks("coder", [TextBlock(text="我看一下文件")])
    state.append_blocks(
        "coder",
        [ToolCallBlock(name="read_file", arguments='{"path": "a.txt"}', id="call_1")],
    )
    print("context 条数:", len(state.context))  # 1
    print("消息 id == reply_id:", state.context[0].id == state.reply_id)  # True
    print("块数:", len(state.context[0].content))  # 2

    # 工具结果必须独立成 role=tool 消息（formatter 只接受这种形态）
    state.append_tool_result(ToolResultBlock(tool_call_id="call_1", output="hello"))
    print("context 条数:", len(state.context))  # 2
    print("末条 role:", state.context[-1].role.value)  # tool

    # 新一轮 → 换 reply_id，新开一条消息
    rid = state.new_reply()
    state.append_blocks("coder", [TextBlock(text="继续")])
    print("context 条数:", len(state.context))  # 3
    print("新消息 id == 新 reply_id:", state.context[-1].id == rid)  # True

    # 可序列化 / 可恢复
    blob = state.model_dump_json()
    restored = AgentState.model_validate_json(blob)
    print("往返一致:", restored == state)  # True


if __name__ == "__main__":
    main()
