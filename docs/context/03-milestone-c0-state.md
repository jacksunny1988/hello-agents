# 里程碑 C0：包骨架 + 会话状态对象 `AgentState`

> **目标**：在 `hello_agents/state/` 建包，实现会话状态对象 `AgentState`——它是"记忆"的唯一真相源，
> 承载 `context`（未压缩历史）+ `summary`（压缩摘要）+ 会话/循环元数据，并支持序列化与恢复。
> **对标（只读）**：`src/agentscope/state/_state.py`（`AgentState` / `append_context`）、
> `src/agentscope/agent/_agent.py:742` 附近（新回复时更新 `reply_id` / `cur_iter` 的内联逻辑）。
> **前置**：无。旧 `hello_agents/context/` 不动、并存；本节不引入任何外部依赖。

---

## C0.1 设计原理（先理解）

### 1) 为什么要有独立的「状态对象」，而不是上层自己拿两个变量

如果让上层自己维护 `history: list[Message]` 和 `summary: str` 两个局部变量，会立刻遇到四个问题：

- **无法持久化**：会话中断后，历史与摘要无从恢复（两个变量的生命周期 = 一个函数调用）；
- **无法测试**：想断言"压缩后摘要写了什么、历史剩了什么"，得先把整个主循环跑起来；
- **无法原子更新**：`summary` 与 `context` 必须同进同退（第二章 D13），两个裸变量做不到"要么都更新、要么都不动"；
- **职责漂移**：谁都能往里塞东西，最终变成"什么都能装的大杂烩"——你的旧
  `context/builder.py` 就是这么膨胀到 690 行、15 处缺陷堆叠的。

把它做成一个**显式对象**，上面四个问题一次解决：它是数据、可 dump、可注入、可断言，
而且**边界写在字段清单里**——加字段要过 review。

### 2) 为什么放在 `hello_agents/state/`，而不是塞进 `model/`

承接第一章的依赖方向：`state → model`，**没有反向依赖**。

- `model/` 管的是"一次请求/一次响应"的协议与数据形状，它不该知道"会话历史有多长、要不要压缩"；
- `state/` 管的是"跨轮次的记忆"，它**复用** `model` 的 `Message` / Block，但不被 `model` 依赖。

这与你自己在 `MY-AGENT-DESIGN.md` 里总结的第 9 条原则一致：**依赖单向、缝合点唯一**。

### 3) 为什么字段只有这 5 个（逐条对照裁剪表）

| 字段 | 保留理由 |
|---|---|
| `session_id` | 一份状态对应一个会话；持久化/日志/多会话并行的锚点 |
| `summary` | ② 压缩的唯一产物；与 `context` 构成模型的全部历史输入 |
| `context` | 未压缩历史，时间序，模型的另一半个历史输入 |
| `reply_id` | "一次 reply == 一条 assistant 消息"的锚点（第一章 D3）；事件流与消息 id 靠它对齐 |
| `cur_iter` | 当前回复内的循环轮次；上层的 `max_iters` 兜底需要它 |

**裁剪掉**（AgentScope 有、我们不要）：`permission_context`（权限）、`tool_context`（读文件缓存/激活组）、
`tasks_context`、`middle_context`（中间件跨轮数据）。理由：它们属于别的子系统，挂靠进来只会让"上下文管理"
这个职责边界糊掉。**注意**：AgentScope 压缩时必须联动清理 `tool_context` 里的读文件缓存
（`_clear_unreserved_read_cache`）——我们既然没有该字段，这条附带动作用也随之消失。

### 4) 为什么 `append_blocks` 要按 `reply_id` 聚合

AgentScope 的 `append_context`（`_state.py:194`）规则是：

> 若 `context` 末条消息是"本 agent、同 `reply_id` 的 assistant 消息"，就把新块 **append 进去**；否则新建一条。

于是：**一次 reply 从头到尾只对应一条 assistant 消息**，模型产出（思考/正文/工具调用）与工具结果
全都在这一条消息的 `content` 里按发生顺序排列。

好处：① 消息 id 与 `reply_id` 严格一对一，事件流（流式 chunk、工具结果事件）能直接挂到消息上；
② 追加是"就地 extend"，不需要反复重建消息对象。

代价（第二章已展开）：计数、序列化、切分都得**走进块内部**——这正是 C4 切分算法要分两层的原因。

### 5) 为什么用 pydantic + `extra="forbid"`

- **可校验**：`context: list[Message]` 会自动校验嵌套的消息与块，传错结构当场报错；
- **可序列化**：`model_dump_json()` / `model_validate_json()` 原生支持往返，持久化不用自己写编解码；
- **fail loud**：`extra="forbid"` 让"多传了一个字段"直接 `ValidationError`，而不是被静默吞掉
  ——这正是你自己提炼的第 7 条原则「静默失效比报错更贵」，也与 `tool/_response.py` 里
  `model_config = ConfigDict(extra="forbid")` 保持一致。

### 6) 我们比 AgentScope 多封装的一个方法：`new_reply()`

AgentScope 在主循环里**内联**做两件事（`_agent.py:742` 附近）：

```python
self.state.reply_id = _generate_id()
self.state.cur_iter = 0
```

我们把它们收成 `AgentState.new_reply()`。理由：这是**状态的内部不变量**（换 id 与轮次归零必须同进同退），
让调用方记住"先换 id 再归零"是隐性知识；收进方法后，上层只需 `state.new_reply()`。
这是**有意的偏离**，会记账在 §C0.8 对照表里。

---

## C0.2 目录与包骨架（操作步骤，项目根目录）

```powershell
cd D:\projects\hello-agents
New-Item -ItemType Directory -Force hello_agents\state
New-Item -ItemType File hello_agents\state\__init__.py
New-Item -ItemType File hello_agents\state\_state.py
```

最终形态（本节只建前两个文件，`_config.py` / `_context.py` / `_split.py` 在 C3/C1/C4 再加）：

```
hello_agents/state/
  __init__.py      # 模块 docstring + 典型用法 + __all__
  _state.py        # AgentState
```

---

## C0.3 接口契约：`hello_agents/state/_state.py`

### 字段说明

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `session_id` | `str` | `uuid4().hex` | 会话标识 |
| `summary` | `str` | `""` | 压缩摘要；空串 = 还没有历史被压过 |
| `context` | `list[Message]` | `[]` | 未压缩历史，时间序 |
| `reply_id` | `str` | `uuid4().hex` | 当前回复 id，同时是新 assistant 消息的 id |
| `cur_iter` | `int` | `0` | 当前回复内已进行的循环轮次 |

### 方法说明

| 方法 | 入参 | 返回 | 职责 |
|---|---|---|---|
| `new_reply` | - | `str` | 开一次新回复：换 `reply_id`、`cur_iter` 归零，返回新 id |
| `append_blocks` | `name: str`, `blocks: list[ContentBlock]` | `Message` | 按 `reply_id` 聚合写入；返回被写入的消息 |

### 完整代码

```python
"""会话状态对象：Agent 的「记忆」唯一真相源，可序列化、可恢复、可注入测试。"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from ..model import (
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)

# 一条消息里允许出现的块类型（与 model/message.py 的联合类型保持一致）
ContentBlock = TextBlock | ThinkingBlock | ToolCallBlock | ToolResultBlock


class AgentState(BaseModel):
    """会话状态：上下文管理的唯一真相源。

    只承载「上下文管理」相关的数据：context（未压缩历史）+ summary（压缩摘要），
    以及"当前这一轮回复"需要的锚点（reply_id / cur_iter）。
    权限、工具缓存、任务等其它子系统的状态**不挂靠在此**（见 C0.6 裁剪）。
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """会话标识；一个会话一份状态。"""

    summary: str = ""
    """已压缩历史的摘要；空串表示"还没有历史被压缩过"。
    与 context 必须同进同退（原子更新），见 C6。"""

    context: list[Message] = Field(default_factory=list)
    """未压缩的对话历史，按时间顺序排列。"""

    reply_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """当前这次回复的 id；同时作为本次回复那条 assistant 消息的 id。"""

    cur_iter: int = 0
    """当前回复内 ReAct 循环已进行的轮次。"""

    # --- 回复生命周期 -----------------------------------------------------

    def new_reply(self) -> str:
        """开始一次新回复：换 reply_id、轮次归零，返回新的 reply_id。"""
        self.reply_id = uuid.uuid4().hex
        self.cur_iter = 0
        return self.reply_id

    # --- 写入上下文 -------------------------------------------------------

    def append_blocks(self, name: str, blocks: list[ContentBlock]) -> Message:
        """把内容块写入上下文，返回被写入的那条消息。

        规则：若末条消息是「本 agent、同 reply_id 的 assistant 消息」，就地 append；
        否则新建一条 assistant 消息（其 id 即当前的 reply_id）。
        这样"一次 reply == 一条 assistant 消息"，消息 id 与 reply_id 严格一对一。

        Args:
            name: 写入者名称；只有同名消息才会被合并。
            blocks: 至少一个内容块；空列表视为调用错误，直接报错。

        Returns:
            被写入的那条 Message（就是 context 的末条）。
        """
        if not blocks:
            raise ValueError("append_blocks 需要至少一个内容块")

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
            role=Role.ASSISTANT,
            name=name,
            content=list(blocks),
            id=self.reply_id,
        )
        self.context.append(msg)
        return msg
```

> **两个实现细节，写的时候留意**：
> ① `last.content.extend(blocks)` 是**就地修改**，pydantic 不会重新校验——因为传进来的
> `blocks` 本身就是已校验的模型实例，这是可接受的；但如果你传的是 `dict`，请先自己
> `TextBlock.model_validate(...)`，否则会把非法数据带进状态。
> ② `Message(role=Role.ASSISTANT, ...)` 走 pydantic 构造，会**校验** `content` 的判别联合类型。

---

## C0.4 接口契约：`hello_agents/state/__init__.py`

```python
"""会话上下文管理

把 Agent 的「记忆」建模成一个可序列化的状态对象，并提供在 token 预算内组装模型输入、
以及把超长历史压缩成结构化摘要的能力。

本包只提供"能力"，不负责"何时调用"——**触发压缩的时机由上层决定**
（examples 主循环，或未来的 Agent 类）。

典型用法：
    from hello_agents.state import AgentState

    state = AgentState()
    state.append_blocks("assistant", [TextBlock(text="你好")])
    print(state.summary, len(state.context))
"""

from ._state import AgentState

__all__ = ["AgentState"]
```

---

## C0.5 留给你的动手任务

### 1) 建包并填充两个文件

按 §C0.2 建目录，按 §C0.3 / §C0.4 填 `_state.py` 与 `__init__.py`。

### 2) 写演示 `examples/state_c0_state.py`

```python
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
    state.append_blocks("coder", [ToolResultBlock(tool_call_id="call_1", output="hello")])
    print("context 条数:", len(state.context))            # 1
    print("消息 id == reply_id:", state.context[0].id == state.reply_id)  # True
    print("块数:", len(state.context[0].content))          # 3

    # 新一轮 → 换 reply_id，新开一条消息
    rid = state.new_reply()
    state.append_blocks("coder", [TextBlock(text="继续")])
    print("context 条数:", len(state.context))            # 2
    print("新消息 id == 新 reply_id:", state.context[-1].id == rid)  # True

    # 可序列化 / 可恢复
    blob = state.model_dump_json()
    restored = AgentState.model_validate_json(blob)
    print("往返一致:", restored == state)                  # True


if __name__ == "__main__":
    main()
```

### 3) 写测试 `tests/test_state_c0.py`（离线、确定性）

- **默认构造**：`summary == ""`、`context == []`、`cur_iter == 0`；`session_id` / `reply_id` 是 32 位 hex；
- **唯一性**：两次 `AgentState()` 的 `session_id` / `reply_id` 互不相同；
- **首次 append**：新建一条 assistant 消息，`role == Role.ASSISTANT`、`name` 正确、`id == state.reply_id`；
  返回值就是 `state.context[-1]`；
- **同 reply 连续追加**：消息条数仍为 1，块按调用顺序累积（断言块类型序列）；
- **不同 name**：不合并，新建第二条消息；
- **`new_reply()`**：`reply_id` 变化、`cur_iter` 归零（先手动设成 3 再调）、后续 append 新建消息；
- **空 blocks**：`append_blocks("x", [])` → `ValueError`；
- **fail loud**：`AgentState(session_id="x", oops=1)` → pydantic `ValidationError`；
- **嵌套校验**：`AgentState(context=[{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}])`
  能被正确校验；`content` 里放 `{"type": "unknown"}` → `ValidationError`；
- **序列化往返**：`model_dump_json()` → `model_validate_json()` 后与原始相等（含嵌套 Message / Block）。

---

## C0.6 边界与裁剪

- **不做** `permission_context` / `tool_context` / `tasks_context` / `middle_context`（其它子系统的状态，不挂靠）；
- **不做** 消息级 `usage` / `created_at` / `finished_at`（你的 `Message` 目前没有这些字段；
  要加属于 **model 层**改造，且 C2 会用 `ChatUsage` 单独处理用量）；
- **不做** 多模态 `summary`（AgentScope 允许 `list[TextBlock | DataBlock]`，我们只留 `str`）；
- **不做** 持久化后端（AgentScope 有 storage 层；我们只保证 pydantic 原生 dump/load 可用）；
- **不做** 并发保护：状态假定"单会话、单线程/单任务"使用；
- **有意偏离**：空 `blocks` 直接 `ValueError`（AgentScope 不校验）；多封装 `new_reply()`。

---

## C0.7 验收清单

- [ ] `hello_agents/state/__init__.py`、`hello_agents/state/_state.py` 与契约一致；
- [ ] `examples/state_c0_state.py` 输出与注释中的期望值一致（1 / True / 3 / 2 / True / True）；
- [ ] `tests/test_state_c0.py` 全绿（默认值、唯一性、聚合、新建、`new_reply`、空输入、`extra=forbid`、嵌套校验、往返）；
- [ ] **零回归**：既有 model / tool 测试仍全绿；
- [ ] ruff 干净：

```powershell
uv run ruff check hello_agents/state examples/state_c0_state.py tests/test_state_c0.py
uv run ruff format --check hello_agents/state examples/state_c0_state.py tests/test_state_c0.py
uv run pytest tests/test_state_c0.py -q
uv run pytest tests/ -q
```

---

## C0.8 agentscope 源码对照

| 你的实现 | agentscope | 对照 / 裁剪 |
|---|---|---|
| `AgentState(session_id, summary, context, reply_id, cur_iter)` | `state/_state.py:149` | 保留 5 字段；裁剪 `permission_context` / `tool_context` / `tasks_context` / `middle_context` |
| `summary: str` | `summary: str \| list[TextBlock \| DataBlock]` | 裁剪多模态，只留字符串 |
| `append_blocks(name, blocks)` | `_state.py:194` `append_context` | 聚合规则一致；我们**返回 Message** 且**拒绝空输入**（fail loud） |
| `new_reply()` | `_agent.py:742` 附近内联（换 `reply_id` + `cur_iter=0`） | 提升为状态方法，避免上层记住更新顺序（**有意偏离**） |
| `model_config = ConfigDict(extra="forbid")` | pydantic 默认（AgentScope 未显式 forbid） | 更严格：多字段直接报错，贴合你 `tool/_response.py` 的既有风格 |
| `model_dump_json()` / `model_validate_json()` | docstring："should be saved and loaded from storage"（另有 storage 层） | 用 pydantic 原生能力；持久化后端裁剪 |

---

## 完成后

把 `hello_agents/state/__init__.py`、`hello_agents/state/_state.py`、`examples/state_c0_state.py` 的输出、
`tests/test_state_c0.py` 的 pytest 结果贴给我 review。

通过后进入 **C1：上下文组装 —— `ContextManager.build_model_input`（三段式 + tools）**，
即回答第一章那个问题："每一轮模型看到的 messages，到底是怎么按顺序拼出来的"。