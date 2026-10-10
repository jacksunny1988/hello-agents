"""上下文组装：把 AgentState 拼成模型可直接消费的输入。"""

from typing import TypedDict

from ..model import Message
from ._state import AgentState


class ModelInput(TypedDict):
    """`build_model_input` 的返回值：可直接作为关键字参数传给模型。"""

    messages: list[Message]
    tools: list[dict] | None


def build_model_input(
    state: AgentState,
    *,
    system_prompt: str,
    tools: list[dict] | None = None,
) -> ModelInput:
    """把会话状态组装成模型输入：三段式 messages + tools。

    顺序固定为：

    ① `SystemMsg(system_prompt)` —— 身份与指令；
    ② `UserMsg(state.summary)` —— **仅当 summary 非空**；
    ③ `state.context` —— 未压缩历史，原样展开、保时间序。

    Args:
        state: 会话状态；本函数**只读**，不修改它。
        system_prompt: 最终的系统提示文本；动态拼装（技能/工作区/中间件）由调用方负责。
        tools: 工具 JSON schema 列表；没有工具时传 `None`（不要传空列表）。

    Returns:
        关键字参数字典，可直接 `await model(**out)`；也可 `await model.count_tokens(**out)`，
        保证"估算"与"真实请求"用的是同一份输入。

    Note:
        返回的 `messages` 里的消息对象与 `state.context` 中**是同一批对象**（不深拷贝）。
        调用方与 formatter 不得就地修改它们，否则会污染会话状态。
    """
    messages: list[Message] = [Message.system(system_prompt)]
    if state.summary:
        messages.append(Message.user(state.summary))
    messages.extend(state.context)
    return ModelInput(messages=messages, tools=tools)
