# 里程碑 C3：压缩入口与配置（`ContextConfig` · 阈值判定 · 守卫短路）

> **目标**：把「要不要压缩」这件事从 Agent 身上摘下来，做成一个**独立、可注入、可离线测**的入口。
> 交付 `ContextConfig`（两条比率）、`ContextManager`（首次引入）、`estimated_tokens()`、
> `needs_compression()`、`compress()` 的**判定与守卫**，以及一个专用异常 `ContextOverflowError`。
> **对标（只读）**：`src/agentscope/agent/_agent.py:327-373`（判定的完整分支）、
> `src/agentscope/agent/_config.py:51-64`（两个 ratio 的取值域与 docstring 理由）、
> `src/agentscope/model/_model_card.py:58`（`context_size` 的来源）。
> **分工特例**：`model/` 的改动仍由**我动手**（本轮加 `context_size`，见 §C3.5）；
> `state/`、`examples/`、`tests/` 由你写。
> **本次状态**：📐 **图纸**——参考实现已在本地跑通 18 项行为检查（§C3.8 全部可复现），待你落地。

---

## C3.0 计划修订（先读这一节）

### 修订 R1：`compress()` 在 C3 只交付「判定 + 守卫」，主体归 C4–C6

`00-overview.md` 原写「`compress` 骨架与短路」。这里把「骨架」的边界钉死，避免出现一个假装能压、
实则什么都没干的空壳：

| 阶段 | 职责 | 里程碑 |
|---|---|---|
| 判定 | `estimated_tokens` / `threshold_tokens` / `needs_compression` | **C3** |
| 守卫 | 超阈值但没有历史可压 → `ContextOverflowError` | **C3** |
| 短路 | 未达阈值 → 返回 `False`、**一个字节都不动** | **C3** |
| 切分 | 压谁、留谁（预算回扫 + block 级切 + 配对修正） | C4 |
| 组装 + 摘要 | 组压缩输入 → 5 字段结构化摘要 → 模板化 | C5 |
| 回填 + 异常 | 原子替换 `summary` / `context`；E1/E2/E3 | C6 |

所以 `compress()` 在 C3 的末尾是一行 `raise NotImplementedError`——
**它是故意的、响亮的、指路的**（消息里直接给出 C4 的文档名）。
静默返回成功才是真正的坑：主循环会以为压过了，继续发一个超长请求。
C4 的第一个动作就是把这行删掉。

### 修订 R2：`ContextConfig` 在 C3 只有两个 ratio 字段

`compression_prompt` / `summary_template` / `summary_schema` 三件**跟着 `SummarySchema` 一起**在 C5 落地——
它们的形状由「摘要要产出哪 5 个字段」决定，而那是 C5 的题目。在 C3 里先写死它们，
就等于在没想清 5 字段之前先冻结模板。

`tool_result_limit` **裁掉**（属于第 ③ 块「工具结果外置」，本轮整体不做）。

### 修订 R3（我动手）：`model/` 补一个 `context_size` 出口

AgentScope 的阈值算的是 `cfg.trigger_ratio * self.model.context_size`（`:351`）。
你的 `ChatModelBase` **没有**这个属性——它只有 `self.config`，而 `context_size` 挂在 `ModelConfig` 上。
直接写 `self.model.config.context_size` 能让代码跑，但 `state` 就开始依赖 `ModelConfig` 的字段表（registry 的内部形状）。

**已实现**（§C3.5）：给 `ChatModelBase` 加一个 `context_size` 属性，转发 `self.config.context_size`。
与 AgentScope 同名同义，调用点形状一致。

---

## C3.1 设计原理（先理解）

### 1) 为什么到 C3 才有理由引入 `ContextManager` 类

C1 时我特地**不建类**，理由是：那时的组装只是 `(state, system_prompt, tools) → 输入` 的纯函数，
为它建类就是空壳。C3 变了——判定要同时用上两样东西：

- `model`：C2 的 `count_tokens`（估算）；
- `config`：阈值/预算比率。

**一个对象存在的理由，是它持有别人没有的状态。** `ContextManager` 现在真的持有了。

同时它保持「不复制 C1」：`estimated_tokens()` 内部调 `build_model_input()`，
而不是自己再拼一遍 messages。所以组装口径仍然只有一处。

### 2) 为什么阈值是「比率 × 窗口」而不是一个绝对 token 数

```python
threshold = trigger_ratio * model.context_size     # 例：0.8 × 128000
```

绝对数（比如「10 万 token 就压」）在换模型时会立刻失效：窗口从 128K 变成 1M 之后，
10 万变成了「才用了 10% 就压」，白白丢掉历史；反过来换到 32K 就更糟——还没到阈值就已经超窗口了。
**比率把配置与模型解耦**，这也是 AgentScope 的选择。

### 3) 为什么 `trigger_ratio` 的上限是 0.9 而不是 1.0

AgentScope 的 docstring 把理由写得很直白（`_config.py:58-60`）：

> When the token exceeds this ratio … the context will be compressed.
> **To reserve the context for context compression**, the maximum ratio is 0.9.

**压缩本身要花 token**：你要把「system + 旧摘要 + 待压消息 + 压缩指令 + schema」一次性喂回模型。
如果阈值设成 1.0，那么触发的那一刻，输入恰好把窗口塞满——**你连"读"它的余量都没有**，压缩必然失败。
留出的那 10% 就是压缩这次调用的工作空间。

这条余量还有个副作用值得记住：C2 的估算**整体偏小**（中文 0.75 token/字、`id` 不计），
而偏小是危险方向。`trigger_ratio < 0.9` 留出的余量**顺带**把这个偏差也吸收了。
所以这两个约束是同一个设计意图的两面。

### 4) 为什么是 `>=` 触发，而不是 `>`

AgentScope 的写法是"提前返回"：

```python
if estimated_tokens < threshold:
    return
```

也就是「**没到线才跳过**」——等价于 `estimated >= threshold` 就压。
这个方向必须钉死，否则「恰好落在阈值上」这一档行为不确定，而它恰恰是测试最容易构造的一档
（用固定值的桩模型，`estimated == threshold` 一行就能摆出来）。§C3.7 里有一条专门测它。

### 5) 为什么「超阈值但没有历史可压」必须报错

这是**唯一一个压缩救不了的场面**：

```
threshold < len(system prompt) + len(旧摘要)
```

再怎么压也压不到 system prompt 上——它不会被压进摘要。此时有两条路：

- **静默返回**：主循环以为「这一轮不需要压」，接着发请求 → 必然超长报错。而且下一次、
  下下一次都一样，错误现场离真正的病因很远；
- **报错**（我们选的）：直接说清「谁超的、超了多少、怎么办」。

这是本项目一贯的 fail loud。AgentScope 同样选报错（`:363-373`），
并且它**特意区分了摘要存在与否**（`suffix = "and the compression summary "`）——
因为"是 system prompt 太长"和"是摘要本身太长"是两种不同的修法（前者改提示词，后者先精简摘要）。

我们保留这个区分，但把裸 `RuntimeError` 收成专用类型：

```python
class ContextOverflowError(RuntimeError): ...
```

理由与你的 `model/` 一致（`ModelCardError` / `StructuredOutputError` / `MissingAPIKeyError`）：
**上层要能只捕这一个**，而不是去 `except RuntimeError` 顺带吞掉别人的 bug。

### 6) 为什么 `needs_compression` 与 `compress` 是两个方法

`compress()` **内部自带判定**——调用方每轮无脑调一次就行，不需要「先问再压」：

```python
await cm.compress(state, system_prompt=sp, tools=tools)   # 不超阈值 → 直接 False 返回
```

那为什么还要单独暴露 `needs_compression()`？因为**「只问不压」是一种真实需求**：

- 测试：断言阈值边界时，不想碰压缩流程（C4 之后尤其如此）；
- 观测：主循环想打印「当前占用 / 阈值」而不想触发压缩；
- C4/C5 开发期：`needs_compression` 稳定了就可以先冻住，单独迭代切分。

**AgentScope 没有这个方法**（判定揉在 `compress_context` 里）。这是有意增补，不是照抄。

### 7) 为什么 `system_prompt` 每次调用都传，而不是构造时存

看起来存进 `ContextManager` 更省事，但那会让它变成"半个 Agent"。两个理由：

- **system prompt 是动态的**：AgentScope 每轮都在 `_get_system_prompt()` 里重算（技能指令、
  工作区指令、middleware 变换）。存下来就等于把某一轮的快照焊死；
- **C1 已经定了这条边界**：`build_model_input` 的 `system_prompt` 就是参数。`ContextManager`
  调用它，就必须把参数一路传下去，否则要么复制拼装逻辑，要么偷偷存状态——两条都违背 C1。

`tools` 同理：它属于调用方（`bridge.model_tools_for(toolkit)` 的产物），本层只透传。

### 8) 为什么配置要有取值域，以及为什么**不**校验 `reserve_ratio < trigger_ratio`

`Field(gt=0, lt=0.9)` 让**错误的配置在构造时就炸**，而不是等到某次对话跑了一半才表现成怪行为。
这条与你在 `model/` 里对 `ModelCard` 的处理是同一个原则。

但 `reserve_ratio < trigger_ratio` 这条**只在 docstring 里写、不做交叉校验**——AgentScope 也是这样
（它的 docstring 写 "which should be smaller than the trigger ratio"，但 `Field` 上没有任何跨字段约束）。
理由：跨字段校验会让「只想临时改一个」的用法变难，而这条路越界时的后果
（压完立刻又超阈值）**不是静默的**：它会在 C6 的收尾检查里被看出来。
**能靠下游显式暴露的问题，不必在上游设关卡。**

### 9) 为什么 `compress()` 返回 `bool`，而 AgentScope 返回 `None`

AgentScope 的 `compress_context()` 返回 `None`，只在内部 `logger.info`。我们返回 `bool`：

- **短路可断言**：测试不需要去数日志，`assert await cm.compress(...) is False` 就完事；
- **主循环要决策**：压没压过，直接影响下一步要不要重新组装输入、要不要提示用户。

代价是调用方可能忽略返回值——但忽略 `bool` 的代价远小于忽略 `None`。
这是一条**有意的小偏离**，会在 §C3.9 记账。

---

## C3.2 接口契约：`hello_agents/state/_config.py`（新建）

```python
"""压缩配置：两条比率阈值。"""

from pydantic import BaseModel, ConfigDict, Field


class ContextConfig(BaseModel):
    """上下文压缩的配置。

    两个 ratio 都乘 `model.context_size` 换算成绝对 token 数——**用比率而不是
    绝对数**，换模型（128K → 1M）时配置不用动。
    """

    model_config = ConfigDict(extra="forbid")

    trigger_ratio: float = Field(default=0.8, gt=0, lt=0.9)
    """估算 token 达到 `trigger_ratio × context_size` 就压缩。

    上限 0.9 不是随手取的：压缩**本身要花 token**——得把「system + 旧摘要 +
    待压消息 + 压缩指令」一次性喂回模型。留出 10% 才够这次"读"。
    """

    reserve_ratio: float = Field(default=0.1, gt=0, lt=0.9)
    """压缩后要保留的**预算**比例：`reserve_ratio × context_size`。

    是预算语义，不是"保留几条消息"——条数不是稳定单位（一条消息可能 3 个字，
    也可能 5 万字）。消费者是 C4 的切分算法。
    """
```

**为什么 `trigger_ratio` 的默认是 `0.8` 而不是顶格 `0.89`**：留给压缩的余量（0.1）是底线，
但运营上更希望「压缩发生得不要那么频繁」——每压一次就是一次额外的大模型调用（钱 + 延迟）。
0.8 是「差不多该压了」与「别太勤」之间的默认，与 AgentScope 一致。

---

## C3.3 接口契约：`hello_agents/state/_context.py`（追加）

**先改文件头的 docstring 与 import**：

```diff
-"""上下文组装：把 AgentState 拼成模型可直接消费的输入。"""
+"""上下文组装与压缩入口：把 AgentState 拼成模型输入，并决定何时压它。"""
 
 from collections.abc import Sequence
 from typing import TypedDict
 
-from ..model import Message, Tool
+from ..model import ChatModelBase, Message, Tool
+from ._config import ContextConfig
 from ._state import AgentState
```

> `ModelInput` 与 `build_model_input` **一个字都不动**。

**在文件末尾追加**（`NotImplementedError` 那行是 C4 的接入点，不是遗留垃圾）：

```python
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
    ) -> None:
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
            "C3 只交付判定与守卫；切分由 C4 接入："
            "docs/context/07-milestone-c4-split.md"
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
```

三个设计细节值得记住：

- **`threshold_tokens` 是属性不是方法**：它只依赖 `config` 与 `model`，不依赖调用参数——
  每次调用都传同样的东西的"方法"，本来就该是属性；
- **`_raise_if_uncompressible` 是私有**：它只在「已确认超阈值」时才有意义，
  暴露成公开 API 会让人以为可以单独调用；
- **`compress()` 里判定在前、守卫在后**：顺序不能反——未达阈值又没有历史，
  根本不是错误（空会话刚开始就是这状态），不该报 `ContextOverflowError`。

---

## C3.4 接口契约：`hello_agents/state/__init__.py`（追加导出）

```diff
 from ._context import ModelInput, build_model_input
+from ._config import ContextConfig
+from ._context import (
+    ContextManager,
+    ContextOverflowError,
+    ModelInput,
+    build_model_input,
+)
 from ._state import AgentState
 
-__all__ = ["AgentState", "ModelInput", "build_model_input"]
+__all__ = [
+    "AgentState",
+    "ContextConfig",
+    "ContextManager",
+    "ContextOverflowError",
+    "ModelInput",
+    "build_model_input",
+]
```

（上面是**合并后的结果**，不是让你留两行 `from ._context import`。`__all__` 按字母序。）

顺带把模块 docstring 的「典型用法」补一句压缩的形状：

```python
    from hello_agents.state import AgentState, ContextConfig, ContextManager

    cm = ContextManager(model, config=ContextConfig(trigger_ratio=0.8))
    if await cm.needs_compression(state, system_prompt=sp):
        await cm.compress(state, system_prompt=sp)
```

---

## C3.5 我做的改动：`model/_base.py` 的 `context_size`（已写入）

```diff
         self.retry_delay = retry_delay
 
+    @property
+    def context_size(self) -> int:
+        """该模型的上下文窗口大小（token）。
+
+        `state` 的压缩阈值 = `trigger_ratio × context_size`；与 AgentScope
+        同名同义（那边是 `ModelCard.context_size`）。暴露成属性而非让上层翻
+        `self.config`，是因为「窗口多大」是模型层的公开事实，而 `ModelConfig`
+        的字段表属于 registry 的内部形状。
+        """
+        return self.config.context_size
+
     async def __call__(
```

实测：`ruff check` / `format --check` 干净；`context_size == 1000000`（DeepSeek 卡）。
回归：`tests/test_model_count_tokens.py test_model_retry.py test_state_c0.py test_state_c1.py` → **48 passed**。

**这处不需要单独写测试**：§C3.7 的阈值用例会断言 `threshold_tokens == 0.8 * model.config.context_size`——
它把 `config → model.context_size → threshold` 整条链一起钉住，比一句转发断言有价值。

---

## C3.6 留给你的动手任务

### 1) 建 `_config.py`、追加 `_context.py`、补导出（§C3.2 / §C3.3 / §C3.4）

### 2) 写演示 `examples/state_c3_threshold.py`

要求**离线**（用桩模型，不发请求），打印四件事：

- `threshold_tokens` 与实际 `context_size`（证明比率换算）；
- 正常会话的 `estimated_tokens` 与 `needs_compression()`（期望 `False`）；
- `compress()` 在未达阈值时的返回值（期望 `False`），并打印 `state` 前后一致；
- 把 `trigger_ratio` 调到极小后（如 `0.001`）再调 `compress()`：
  分别用「空 context」与「非空 context」两种 state，打印两个异常的类型与消息。

### 3) 写测试 `tests/test_state_c3.py`

**桩模型（完全离线）**——一个就够，`fixed=None` 时走基类的真口径：

```python
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
```

**配置层**

- `ContextConfig()` → `trigger_ratio == 0.8`、`reserve_ratio == 0.1`；
- `trigger_ratio` 取 `0.9` / `0.95` / `0.0` / `-0.1` → `pytest.raises(ValidationError)`；
- `reserve_ratio` 取 `0.9` / `0.0` → 同理；
- `ContextConfig(unknown=1)` → `ValidationError`（`extra="forbid"` 真的生效）。

**阈值层**

- `_StubModel(context_size=100)` → `cm.threshold_tokens == 80.0`；
- 注入 `ContextConfig(trigger_ratio=0.5)` → 阈值随之变成 `50.0`。

**估算层（同源复用的证据，这组最重要）**

- `await cm.estimated_tokens(state, system_prompt=sp)` **逐字等于**
  `await model.count_tokens(**build_model_input(state, system_prompt=sp))`；
- 传入 `tools=[_EchoTool()]` 后估算**变大**（走 C2 的 schema 计数）。

**判定层（边界）**

- `fixed=80, context_size=100` → `needs_compression() is True`（**钉死 `>=`**）；
- `fixed=79` → `False`。**这两条就是 §C3.1 第 4 条那个方向的可执行版本。**

**短路与守卫层**

- 未达阈值 → `await cm.compress(...) is False`，且 `state.summary` / `len(state.context)` /
  `state.cur_iter` **三者都没变**；
- 超阈值 + `context` 为空 → `pytest.raises(ContextOverflowError)`，消息含 `"system prompt"`
  与阈值数字 `80`，且**不含** `"压缩摘要"`；
- 同上但 `state.summary` 非空 → 消息**含** `"压缩摘要"`；
- 超阈值 + `context` 非空 → `pytest.raises(NotImplementedError)`，消息含 `"C4"`。

---

## C3.7 边界与裁剪

- **不做** `tool_result_limit`：属于第 ③ 块「工具结果外置」，本轮整体裁剪；
- **不做** `compression_prompt` / `summary_template` / `summary_schema`：归 C5，跟着
  `SummarySchema` 的 5 字段一起定（§C3.0 R2）；
- **不做** `reserve_tokens` 属性：它的消费者是 C4 的切分，C4 加；
- **不做** middleware 链 / `HintBlock` instructions：新栈没有这些抽象；
- **不做**「压缩后再验一次是否降到阈值以下」：那是 C6 的收尾检查；
- **不做** 估算结果的缓存/记忆化：判定每轮只跑一次，纯字符串拼接极便宜；
- **不做** `ContextManager` 持有 `system_prompt` / `tools`：理由见 §C3.1 第 7 条。

---

## C3.8 验收清单

**参考实现已在本地跑通（18 项行为检查全绿），清单里的每一条都可复现。**

- [ ] `_config.py`：`ContextConfig` 两个 ratio 的默认值与取值域与契约一致；
- [ ] `_context.py`：`ContextOverflowError` + `ContextManager` 与契约一致；
- [ ] `ModelInput` / `build_model_input` **未被改动**（diff 里不该出现它们）；
- [ ] `__init__.py` 导出 `ContextConfig` / `ContextManager` / `ContextOverflowError`，`__all__` 按字母序；
- [ ] `examples/state_c3_threshold.py` 输出符合注释期望；
- [ ] `tests/test_state_c3.py` 全绿（上表全部用例）；
- [ ] 零回归；
- [ ] ruff 干净（含 format）：

```powershell
uv run ruff format hello_agents/state examples/state_c3_threshold.py tests/test_state_c3.py
uv run ruff check hello_agents/state examples/state_c3_threshold.py tests/test_state_c3.py
uv run pytest tests/test_state_c3.py -q
uv run pytest tests/test_model_count_tokens.py tests/test_model_retry.py tests/test_state_c0.py tests/test_state_c1.py -q
```

---

## C3.9 agentscope 源码对照

| 你的实现 | agentscope（只读） | 对照 / 裁剪 |
|---|---|---|
| `ContextConfig(trigger_ratio, reserve_ratio)` | `ContextConfig` 同名字段（`_config.py:57,62`） | 只搬两个 ratio；其余字段按 §C3.0 R2 分期 |
| `Field(gt=0, lt=0.9)` | 同（`:57,62`） | **逐字一致**；`0` 与 `0.9` 都取不到，docstring 的理由也照搬 |
| 不校验 `reserve_ratio < trigger_ratio` | 同（只在 docstring 里写 "should be smaller"） | 一致，理由见 §C3.1 第 8 条 |
| `ContextManager(model, *, config=None)` | `Agent.__init__(context_config=...)` | 把挂在 Agent 上的 `context_config` 摘出来，独立成可注入的管理器 |
| `threshold_tokens` 属性 | `threshold = cfg.trigger_ratio * self.model.context_size`（`:351`） | 同式；提成属性，因为判定与日志都要用 |
| `model.context_size` | `self.model.context_size`（`:351`） | 同名同义；**本轮补的出口**（原先只有 `model.config.context_size`） |
| `estimated_tokens` = `count_tokens(**build_model_input(...))` | `kwargs = await self._prepare_model_input()` 然后 `await self.model.count_tokens(**kwargs)`（`:347-348`） | **完全同构**——C1/C2 两轮的铺垫就是为这一行 |
| `estimated >= threshold` 触发 | `if estimated_tokens < threshold: return`（`:352`） | 等价（`>=` ⟺ `not <`）；都写成"不满足就提前返回" |
| 超阈值且 `context` 为空 → `ContextOverflowError` | `raise RuntimeError(...)`（`:363-373`） | 同语义同分支；把它收成专用异常类型（对齐你 `model/` 的 `ModelCardError` 风格）；它用 `suffix` 拼英文串，我们选主语 |
| `compress()` 内自带判定 | `compress_context()` 内含判定（`:344-353`） | 一致——**调用方不需要先问再压** |
| `needs_compression()` 单独暴露 | **无** | **有意增补**：测试与观测要「只问不压」 |
| `compress()` 返回 `bool` | `-> None` | 有意小偏离：短路可断言、主循环可记日志（§C3.1 第 9 条） |
| `raise NotImplementedError` 占位 | 无（它一次做完） | C3 分期交付的产物；C4 的第一个动作就是删掉它 |
| 无 middleware 链 | `compress_context` 被 `_compress_context_middlewares` 包裹（`:288-325`） | 裁剪：新栈无 middleware |
| 无 `HintBlock` / `instructions` | `instructions: HintBlock \| None`（`:274`） | 裁剪：无该块类型 |
| 无 `Offloader` 联动 | 回填时 offload 被压消息并把路径写进摘要（`:521-530`） | 裁剪：第 ③ 块整体不做 |

---

## 完成后

把 `_config.py`、`_context.py` 的 diff、`__init__.py`、demo 输出、
`pytest tests/test_state_c3.py -q` 结果贴给我 review。

通过后进 **C4：切分算法**（`docs/context/07-milestone-c4-split.md`）——
把 `compress()` 里那行 `NotImplementedError` 换成真正的「保留预算回扫 + 边界消息按 block 切 +
不切断 tool_call/tool_result 配对」，也就是第二章 §2.3 讲的那三层算法。

