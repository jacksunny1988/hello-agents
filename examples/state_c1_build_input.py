"""C1：把 AgentState 组装成模型输入 —— 三段式 messages + tools（离线、确定性）

构造一个真实形态的会话：user 提问 → assistant(正文 + 工具调用) → tool(结果)，
再看它被组装成什么、能不能直接喂给 formatter。
"""

from hello_agents.model import (
    Message,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.model._formatter import to_openai_messages
from hello_agents.state import AgentState, build_model_input

SYSTEM_PROMPT = "你是一个严谨的编码助手，回答前先读文件确认事实。"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件的内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
    }
]


def main() -> None:
    state = AgentState()
    # ① 会话已有过一段历史 → 被压缩成摘要（组装时会插到历史之前）
    state.summary = "此前你已读过 a.txt，内容是 hello。"
    # ② 未压缩历史：user → assistant(正文 + 工具调用) → tool(结果)
    state.context.append(Message.user("把 a.txt 的内容总结成一句话"))
    state.append_blocks(
        "coder",
        [
            TextBlock(text="我先读一下文件"),
            ToolCallBlock(name="read_file", arguments='{"path": "a.txt"}', id="call_1"),
        ],
    )
    state.append_tool_result(
        ToolResultBlock(tool_call_id="call_1", output="hello", name="read_file")
    )

    out = build_model_input(state, system_prompt=SYSTEM_PROMPT, tools=TOOLS)

    # 三件事：出站角色序列、前两段文本、tools 是否原样透传
    print("角色序列:", [m.role.value for m in out["messages"]])
    # ['system', 'user', 'user', 'assistant', 'tool']
    print("① system:", out["messages"][0].content[0].text)
    print("② summary:", out["messages"][1].content[0].text)
    print("tools 透传:", out["tools"] is TOOLS)  # True（引用透传，不拷贝）

    # 组装结果必须能直接进 formatter——这才是 C1 的真验收（工具结果独立成 role=tool）
    print(
        "formatter 出站角色:", [m["role"] for m in to_openai_messages(out["messages"])]
    )


if __name__ == "__main__":
    main()
