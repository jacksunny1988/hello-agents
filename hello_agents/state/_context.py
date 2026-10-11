"""上下文组装与压缩入口：把 AgentState 拼成模型输入，并决定何时压它。"""

from collections.abc import Sequence
from typing import TypedDict

from ..model import ChatModelBase, Message, Tool
from ._config import ContextConfig
from ._state import AgentState


class ModelInput(TypedDict):
    """`build_model_input` 的返回值：可直接作为关键字参数传给模型。"""

    messages: list[Message]
    tools: Sequence[Tool] | None


def build_model_input(
    state: AgentState,
    *,
    system_prompt: str,
    tools: Sequence[Tool] | None = None,
) -> ModelInput:
    """把会话状态组装成模型输入：三段式 messages + tools。

        顺序固定为：

        ① `SystemMsg(system_prompt)` —— 身份与指令；
        ② `UserMsg(state.summary)` —— **仅当 summary 非空**；
        ③ `state.context` —— 未压缩历史，原样展开、保时间序。

        Args:
            state: 会话状态；本函数**只读**，不修改它。
            system_prompt: 最终的系统提示文本；动态拼装（技能/工作区/中间件）由调用方负责。
             tools: `model.Tool` 对象序列（如 `bridge.model_tools_for(toolkit)` 的产物）；
    +            没有工具时传 `None`（不要传空列表）。
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


class ContextOverflowError(RuntimeError):
    """system prompt（+ 旧摘要）本身就超阈值 —— 没有任何历史可压。

    这是压缩**唯一解决不了**的问题：再压也压不到 system prompt 上。
    所以上层必须能单独捕获它（而不是去 `except RuntimeError` 顺带吞掉别人的 bug）。
    """


class ContextManager:
    """压缩入口：拿 `model` 与 `config`，回答「要不要压」，并（C4 起）执行压缩。

    它到 C3 才有存在理由——C1 的组装是无状态纯函数，不需要类；而判定要同时
    用上 `model`（C2 的估算）与 `config`（阈值比率），这才值得一个对象。

    组装仍然走 C1 的 `build_model_input`：本类**调用**它，不复制它。
    """

    def __init__(
        self,
        model: ChatModelBase,
        *,
        config: ContextConfig | None = None,
    ):
        self.model = model
        self.config = config or ContextConfig()

    @property
    def threshold_tokens(self) -> float:
        """触发线：`trigger_ratio × model.context_size`。"""
        return self.config.trigger_ratio * self.model.context_size

    async def estimated_tokens(
        self,
        state: AgentState,
        *,
        system_prompt: str,
        tools: Sequence[Tool] | None = None,
    ) -> int:
        """当前会话的输入规模估算。

        与真实请求**同一份输入**：`build_model_input` → `count_tokens(**out)`。
        这正是 C1 把返回值做成关键字参数字典、C2 让两个入口同签名的意义所在。
        """
        out = build_model_input(state, system_prompt=system_prompt, tools=tools)
        return await self.model.count_tokens(**out)

    async def needs_compression(
        self,
        state: AgentState,
        *,
        system_prompt: str,
        tools: Sequence[Tool] | None = None,
    ) -> bool:
        """估算是否已达触发线（`>=`，不是 `>`：到线就压）。"""
        estimated = await self.estimated_tokens(
            state, system_prompt=system_prompt, tools=tools
        )
        return estimated >= self.threshold_tokens

    async def compress(
        self,
        state: AgentState,
        *,
        system_prompt: str,
        tools: Sequence[Tool] | None = None,
    ) -> bool:
        """超阈值就压缩；返回**是否真的压了**。

        C3 只交付前两步——判定与守卫；「切分 → 摘要 → 回填」是 C4/C5/C6，
        方法末尾的 `NotImplementedError` 就是那三个里程碑的接入点。
        """
        if not await self.needs_compression(
            state, system_prompt=system_prompt, tools=tools
        ):
            return False

        self._raise_if_uncompressible(state)

        raise NotImplementedError(
            "C3 只交付判定与守卫；切分由 C4 接入：docs/context/07-milestone-c4-split.md"
        )

    def _raise_if_uncompressible(self, state: AgentState) -> None:
        """超阈值且没有可压的历史 —— 唯一一个压缩救不了的场面，必须报错。"""
        if state.context:
            return
        fixed = "system prompt" if not state.summary else "system prompt 与压缩摘要"
        raise ContextOverflowError(
            f"{fixed} 本身已达到压缩阈值（{self.threshold_tokens:.0f} tokens），"
            f"没有任何历史可压——请缩短 system prompt，或换上下文窗口更大的模型。"
        )
