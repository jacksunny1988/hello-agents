# 里程碑 C1：上下文组装 `build_model_input`（三段式 + tools）

> **目标**：把 `AgentState` 组装成模型可直接消费的输入——`messages` 三段式（系统提示 + 摘要 + 未压缩历史）
> 加 `tools`；并**修掉一个照抄 AgentScope 会炸的差异**：工具结果必须独立成 `role=tool` 消息。
> **对标（只读）**：`src/agentscope/agent/_agent.py:2280`（`_prepare_model_input`）、`:2255`（`_get_system_prompt`）。
> **前置**：C0 已通过复审（`state/_state.py`、`state/__init__.py`、demo 与 11 项测试）。

---

## C1.0 计划修订（先读这一节）

### 修订 R1：C1 交付模块级函数，不建 `ContextManager` 类

原计划写 `ContextManager.build_model_input`。但 C1 时点**组装不需要持有任何东西**：
它是 `(state, system_prompt, tools) → 输入` 的纯函数。为一个"还不需要状态"的东西提前建类，
就是我们上一轮已经批评过的"空壳文件"同一个坏味道。

**决定**：C1 交付**模块级纯函数** `build_model_input()`。等 **C3** 出现第二个需要 `model` + `config` 的
能力（估算与压缩）时，再引入 `ContextManager` 类；`build_model_input` 保持为纯函数、被它调用。
（`00-overview.md` 的路线表已同步。）

### 修订 R2：工具结果的存放规则必须改（**这是必改项，不是风格偏好**）

AgentScope 的做法是「**存储聚合、出网展开**」：一次 reply 的思考/正文/工具调用/工具结果**全都塞进同一条
assistant 消息**，协议要求的 `assistant(tool_calls)` + `role=tool` 由 formatter 在出站时拆开。

**但你的 `model` 层选了相反的分工**——`_formatter.py:30` 的 `to_openai_messages` 是**校验者**而非展开者：

```python
# hello_agents/model/_formatter.py:48-53（你的现状）
if tool_results:
    if msg.role is not Role.TOOL:
        raise ValueError("ToolResultBlock 只能出现在 role=tool 的消息里 ...")
```

后果：若沿用 C0 的 `append_blocks` 把 `ToolResultBlock` 写进 assistant 消息，
**C1 组装出的 messages 一进 formatter 就 `ValueError`**，而到 C7 端到端才会暴露——那时排查成本高得多。

**决定**（三处，C0 的 `_state.py` 需要打补丁）：

1. `append_blocks` 收紧：`blocks` 里出现 `ToolResultBlock` 时**直接报错**，并指向 `append_tool_result`；
2. 新增 `append_tool_result(block)`：追加一条 `role=tool` 的消息（每个结果一条，与你 T10 桥接层
   `tool_result_message` 的既有约定一致）；
3. 理由要说清：**「谁保证 role 与 block 匹配」这个职责，你的项目放在了存储侧，AgentScope 放在了 formatter 侧**。
   这不是谁对谁错，而是"失败要早"与"存储与协议解耦"的取舍——你的 formatter 已经选择了 fail loud，
   复刻时就必须尊重它。

### 修订 R3：C0 的 demo 与 2 个测试需要跟着改

| 位置 | 现在 | 改成 |
|---|---|---|
| `examples/state_c0_state.py` | 三次 `append_blocks`（含工具结果）→ `块数: 3`、`context 条数: 1` | 工具结果走 `append_tool_result` → `块数: 2`、`context 条数: 2`（第二条是 `role=tool`） |
| `tests/test_state_c0.py::test_same_reply_accumulates_into_one_message` | 断言块类型 `["text","tool_call","tool_result"]` | 断言 assistant 消息为 `["text","tool_call"]`，另有一条 `role=tool` 消息 |
| `tests/test_state_c0.py::test_json_round_trip_preserves_state` | 用 `append_blocks` 追加 `_result_block()` | 改用 `append_tool_result`；往返后仍断言 4 类块齐全（分布在 2 条消息里） |

> 这三处改完，C0 的其余 9 个测试应保持全绿。

---

## C1.1 设计原理（先理解）

### 1) 为什么"组装"必须单独抽出来

组装是**上下文管理唯一的出口**：所有"历史如何变成模型输入"的知识都只活在这一个函数里。收益有三条：

- **一处收口**：改顺序、加一段、裁一段，只动这一个函数；上层（主循环）永远 `await model(**out)`；
- **请求与估算同源**：C3 判断"是否超阈值"时要先估算输入大小。若估算走另一条拼装代码，
  两条路径必然会漂移（你旧 `context/builder.py` 的 B10 就是"两套 token 口径"导致的预算算术不可靠）。
  把组装结果做成 `{"messages": ..., "tools": ...}` 的**关键字参数字典**，就能 `model(**out)` 与
  `count_tokens(**out)` 共用同一份输入；
- **可测**：纯函数，不需要模型、不需要网络就能断言顺序与内容。

### 2) 三段式的顺序与理由

```
① SystemMsg(system_prompt)      —— 身份与指令（每轮现算，由调用方拼好传入）
② UserMsg(state.summary)        —— 仅当 summary 非空
③ state.context                 —— 未压缩历史，原样展开（保时间序）
```

- ① 在最前：几乎所有厂商都对 system 的"开头位置/唯一性"有约定；
- ② 紧跟 system：摘要承担"我继承了一段历史"的语义，必须出现在历史**之前**，否则模型会先读到细节、
  再被告知"这之前还有摘要"，顺序上自相矛盾；
- ③ 最后：保持真实时间序，不变形。

### 3) 为什么 `summary` 用 `user` 角色而不是 `system`

AgentScope 的做法是 `UserMsg(name="user", content=summary)`，理由（按我们的分析）：

1. **system 是"身份与规则"，不是"事实"**。把随会话不断变长的历史摘要塞进 system，
   会让"我是谁、我该怎么做"被数据淹没，也让 prompt 缓存策略更难做；
2. **厂商约束**：有的 API 要求 system 只能出现在最前/只能有一条（如 Anthropic 把 system 单列成参数），
   摘要塞 system 会撞约束；
3. **语义归属**：摘要描述的是"user 与 assistant 之间发生过什么"，用 user 侧承载更接近事实。

### 4) 为什么返回 kwargs 字典（而不是直接调用模型）

两个用途共用一份组装结果（见 §1 第 2 点）。**代价**要说清：字典没有静态类型约束。
所以：① 用 `TypedDict` 给键值上类型；② 用一条测试**固定键集合**——否则某天有人往里加了第三个键，
`count_tokens(**out)` 会当场 TypeError，而报错点离改动点很远。

### 5) 为什么是**同步**函数（async 传染的根源在哪）

AgentScope 的 `_prepare_model_input` 是 `async`，唯一原因是它内部要 `await self._get_system_prompt()`
（skill 指令、offloader 指令、middleware 都可能异步）。

我们把"动态 system prompt 的拼装"**裁剪**掉——最终字符串由调用方（examples 主循环 / 未来的 Agent 类）
拼好传入。于是这个函数没有任何 I/O，**天然同步**：更简单、更好测，也不会把 `async` 无谓地传染给调用方。

### 6) 为什么 `system_prompt` / `tools` 用参数传入，而不是让本模块自己去取

本模块依赖 `model`（用它的 `Message`）。若它再去 import `tool`（拿 schema）或持有系统提示模板：

- `state` 会被迫同时依赖 `tool`，而你的架构里 `bridge` 才是"同时认识 model 与 tool 的唯一缝合点"；
- 提示词会从"调用方的策略"变成"库的硬编码"，换 Agent 人设要改库。

**参数传入 = state 只认识 model，其余一概不认识。**

### 7) 引用语义：传出去的消息就是 `state.context` 里的那批对象

`messages.extend(state.context)` 只复制**引用**，不深拷贝（AgentScope 亦然）。所以：

- 组装**不得**修改任何消息（本函数保证只读）；
- 调用方与 formatter 同样**不得就地改**这些消息，否则会污染会话状态——这是一个必须写进 docstring 的约束。

---

## C1.2 接口契约：`hello_agents/state/_state.py`（补丁）

### 新增/收紧的方法

| 方法 | 变化 | 说明 |
|---|---|---|
| `append_blocks(name, blocks)` | **收紧** | `blocks` 中出现 `ToolResultBlock` → `ValueError`，错误信息指向 `append_tool_result` |
| `append_tool_result(block)` | **新增** | 追加一条 `role=tool`、只含该结果块的消息；返回该消息 |

### 完整代码（改动后的两个方法）

```python
    def append_blocks(self, name: str, blocks: list[ContentBlock]) -> Message:
        """把内容块写入上下文，返回被写入的那条消息。

        只接受**模型产出**的块（思考 / 正文 / 工具调用）。工具结果请走
        :meth:`append_tool_result`——因为 model 层的 formatter 明确要求
        `ToolResultBlock` 只能出现在 `role=tool` 的消息里。

        Args:
            name: 写入者名称；只有同名消息才会被合并。
            blocks: 至少一个内容块，且不得包含 `ToolResultBlock`。

        Returns:
            被写入的那条 Message（就是 context 的末条）。
        """
        if not blocks:
            raise ValueError("append_blocks 需要至少一个内容块")
        if any(isinstance(b, ToolResultBlock) for b in blocks):
            raise ValueError(
                "工具结果不能写进 assistant 消息（formatter 只接受 role=tool），"
                "请改用 append_tool_result()"
            )

        last = self.context[-1] if self.context else None
        if (
            last is not None
            and last.role == Role.ASSISTANT
            and last.name == name
            and last.id == self.reply_id
        ):
            last.content.extend(blocks)
            return last

        msg = Message(
            role=Role.ASSISTANT, name=name, id=self.reply_id, content=list(blocks)
        )
        self.context.append(msg)
        return msg

    def append_tool_result(self, block: ToolResultBlock) -> Message:
        """追加一条工具结果消息（`role=tool`），返回它。

        每个结果单独成一条消息：与该消息的 `tool_call_id` 一起，构成
        formatter 出站时的配对依据；也与你 T10 桥接层的既有约定一致。
        """
        msg = Message(role=Role.TOOL, content=[block])
        self.context.append(msg)
        return msg
```

> **约定**：`role=tool` 的消息只放 `ToolResultBlock`，并且**不复用 `reply_id` 作 id**——
> `reply_id` 是"回复"的锚点，工具结果消息是协议配对的一环；它靠 `tool_call_id` 与调用配对，
> 自己的 id 用默认新生成的即可。

---

## C1.3 接口契约：`hello_agents/state/_context.py`（新建）

### 参数与返回

| 项 | 类型 | 说明 |
|---|---|---|
| `state` | `AgentState` | **只读**；函数不改它 |
| `system_prompt` | `str` | 最终系统提示文本（动态拼装由调用方负责） |
| `tools` | `list[dict] \| None` | 工具 JSON schema；**无工具传 `None`，不要传 `[]`**（空数组在部分端点语义不同，见你 M6 结论） |
| 返回 | `ModelInput` | `{"messages": list[Message], "tools": list[dict] \| None}`，可直接 `**` 展开 |

### 完整代码

```python
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

    return {"messages": messages, "tools": tools}
```

---

## C1.4 接口契约：`hello_agents/state/__init__.py`（追加导出）

```python
from ._context import ModelInput, build_model_input
from ._state import AgentState

__all__ = ["AgentState", "ModelInput", "build_model_input"]
```

> `__all__` 按字母序（沿用你既有风格）。

---

## C1.5 留给你的动手任务

### 1) 打 C0 补丁（§C1.0 R2/R3 + §C1.2）

改 `_state.py` 两个方法；同步改 C0 的 demo 与 2 个测试。

### 2) 新建 `_context.py`，补 `__init__.py` 导出（§C1.3 / §C1.4）

### 3) 写演示 `examples/state_c1_build_input.py`

要求：构造一个**真实形态**的会话（user → assistant(正文+工具调用) → tool(结果)），打印三件事：

- `[m.role.value for m in out["messages"]]`（期望 `['system', 'user'... 'assistant', 'tool']`）；
- 第一条 system 的文本、第二条 summary 的文本；
- `out["tools"] is tools`（期望 `True`，证明透传而非拷贝）。

### 4) 写测试 `tests/test_state_c1.py`

**组装部分**

- 三段式顺序：`summary` 非空 → `[system, user(summary), *context]`；角色与文本正确；
- `summary` 为空 → 只有 `[system, *context]`；
- **原样透传**：`out["messages"][2] is state.context[0]`（identity，证明不拷贝）；
- `tools` 透传：`None` → `None`（**不**变成 `[]`）；传 list → `is` 同一对象；
- **键集合固定**：`set(out) == {"messages", "tools"}`；
- **只读**：调用后 `state.summary` / `len(state.context)` / `state.cur_iter` 不变；
- 空 `context` + 空 `summary` → 只有 system 一条。

**协议兼容部分（这一组才是真验收）**

- 构造 `user → assistant(text + tool_call) → tool(result)` 的 context，组装后用
  `hello_agents.model._formatter.to_openai_messages(out["messages"])` 转换**不抛错**，
  且出站角色序列正确（`['system', 'user', 'user', 'assistant', 'tool']` 之类，按你的构造断言）；
- `append_blocks` 传 `ToolResultBlock` → `ValueError`，错误信息含 `append_tool_result`；
- `append_tool_result` → 生成 `role=tool` 消息，且 formatter 能正确展开出 `tool_call_id`。

---

## C1.6 边界与裁剪

- **不做**动态 system prompt（技能指令 / 工作区指令 / middleware 变换）——由调用方拼好传入；
- **不做** tools schema 的获取（`toolkit.get_tool_schemas()`）——由调用方或 `bridge` 提供；
- **不做**协议校验的重复实现：`role` 与 `block` 是否匹配，**由 formatter 当唯一裁判**；组装层不复制这套规则；
- **不做**消息深拷贝：显式选择"传引用 + 文档约束"，避免每次组装都复制整个历史；
- **不做**多模态块的组装（`DataBlock` 你 model 层还没有）。

---

## C1.7 验收清单

- [ ] `_state.py`：`append_blocks` 拒绝 `ToolResultBlock`；`append_tool_result` 可用；C0 的 11 项测试仍全绿（3 处按 §C1.0 R3 调整后）；
- [ ] `_context.py`：`build_model_input` 与 `ModelInput` 与契约一致；
- [ ] `state/__init__.py` 导出 `AgentState` / `ModelInput` / `build_model_input`；
- [ ] `examples/state_c1_build_input.py` 输出符合注释期望；
- [ ] `tests/test_state_c1.py` 全绿（含两条**协议兼容**用例）；
- [ ] 零回归：既有 model / tool 测试仍全绿；
- [ ] ruff 干净（含 format）：

```powershell
uv run ruff format hello_agents/state examples/state_c0_state.py examples/state_c1_build_input.py tests/test_state_c0.py tests/test_state_c1.py
uv run ruff check hello_agents/state examples/state_c0_state.py examples/state_c1_build_input.py tests/test_state_c0.py tests/test_state_c1.py
uv run pytest tests/test_state_c0.py tests/test_state_c1.py -q
uv run pytest tests/ -q
```

---

## C1.8 agentscope 源码对照

| 你的实现 | agentscope | 对照 / 裁剪 |
|---|---|---|
| `build_model_input(state, *, system_prompt, tools)`（模块级纯函数） | `Agent` 的方法 `_prepare_model_input()`（`:2280`） | 逻辑一致（三段式 + tools）；我们从 Agent 上摘下，做成**无状态纯函数** |
| 同步函数 | `async def _prepare_model_input` | AgentScope 的 async 只为 `await _get_system_prompt()`；我们裁剪动态提示后**去掉 async** |
| `system_prompt` 由参数传入 | `_get_system_prompt()`（`:2255`）内部拼装 skill / workspace 指令并过 middleware | 裁剪：技能 / 工作区 / 中间件一律不做，调用方给最终字符串 |
| `tools` 由参数传入 | `self.toolkit.get_tool_schemas(...)` | 裁剪：不 import `tool`，保持 `state → model` 单向依赖（bridge 才是缝合点） |
| 返回 `TypedDict ModelInput` | 返回 `dict[str, Any]` | 我们用 TypedDict 补上类型，保留 `**kwargs` 的同源复用 |
| `summary` 用 `Message.user(...)` | `UserMsg(name="user", content=summary)` | 一致；`name` 你的 formatter 不消费，故不设 |
| 工具结果独立成 `role=tool` 消息 | 聚合进 assistant 消息，由 formatter 展开 | **必须偏离**：你的 formatter 是校验者（`_formatter.py:48`），存储就必须是协议形态 |
| 不重复实现协议校验 | Agent 也不校验（交给 formatter） | 一致：formatter 是唯一裁判 |

---

## 完成后

把改动后的 `_state.py`、新建的 `_context.py`、`__init__.py`、demo 输出、`pytest tests/test_state_c0.py tests/test_state_c1.py -q`
结果贴给我 review（另附一次全量 `pytest tests/ -q` 基线）。

通过后进入 **C2：token 估算**——由**我**动手改造 `model/`（`ChatModelBase.count_tokens` 粗估 + 可覆写），
你只需 review 我的改动并补 2~3 个测试。