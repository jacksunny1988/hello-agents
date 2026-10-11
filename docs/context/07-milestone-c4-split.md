# 里程碑 C4：切分算法（压谁 · 留谁 · 绝不切散一次工具调用）

> **目标**：把「保留多少、压掉哪些」做成一个**纯函数**——只读 `context`、不造新消息、
> 返回两个切片；并让「保留区对模型自洽」这件事变成**一行可证明的规则**。
> **对标（只读）**：`src/agentscope/agent/_agent.py:1975-2076`（`_split_context_for_compression`，
> 全函数 101 行，三层结构）。
> **前置**：C1 的 `build_model_input`（组装同源）、C2 的 `count_tokens`（估算）、C3 的 `ContextManager`（入口）。
> **分工**：`state/`、`examples/`、`tests/` 全部归你；本轮 `model/` 不动。
> **本次状态**：📐 **图纸**——参考实现已在本地**穷举 46 个预算 × 5 条不变量**跑通（§C4.5 可复现）。

---

## C4.0 计划修订（先读这一节）

### 修订 R1：**不做** AgentScope 的第二层（block 级切分）—— 我们的形态不需要它

AgentScope 的切分是三层：① 消息级回扫 → ② **边界消息按 block 切** → ③ 配对修正。
**我们只做 ① 和 ③**，并让 ③ 退化成**一行**。三个理由：

1. **它为什么需要 ②**：AgentScope 的存储是「一次 reply 聚合进**一条**消息」——
   工具调用块与工具结果块**住在同一条消息里**。切在消息中间，就等于把一次调用的
   调用块和结果块劈开，所以要 `deepcopy` 出「半个消息」再修配对（`:2026-2074`）。
   而**我们的 C1 修订 R2 已经偏离了这个形态**：工具结果是**独立的 `role=tool` 消息**。
   于是「配对」在我们这里天生就是**跨消息**的事，② 解决的那个问题根本不存在。
2. **收益几乎为零**：我们能想到的大块是**工具结果**，而它们是**单块消息**——
   单块是不可分的原子，② 对它无能为力（AgentScope 也一样）。
   真正需要「部分保留一个超大块」的场景，正是第 ③ 块 `Offloader` 的问题域，本轮已整体裁剪。
3. **代价是真的**：② 要 `deepcopy` 边界消息并把 `content` 劈成两半，
   于是**两条消息共享同一个 `id`**（AgentScope `:2026-2027` 就是这么写的），
   而 `id` 在你的 `append_blocks` 里是「同一次 reply 合并」的依据。引入脏 `id`
   换一个几乎不发生的收益，不划算。

**决定**：C4 = **消息级回扫 + 跨消息配对规则**。配对规则在我们形态下能压成一行，
且**可证明正确**（§C4.2）。
**附录 §C4.9** 完整给出 AgentScope 的第二层（含它自己的配对修正）——它是理解 AgentScope
的关键零件，但**不是我们需要的零件**，读它、不用它。

### 修订 R2：`compress()` 本轮只把「切分」接上，摘要与回填仍在 C5/C6

C3 那行 `NotImplementedError` 会**继续存在**，只是消息换成指向 C5，并把切分结果写进去：

```
NotImplementedError: 切分完成：压缩 8 条 / 保留 1 条；摘要由 C5 接入：...
```

为什么不在 C4 就把 `state.context` 换成保留区？因为**那会不可逆地丢数据**——摘要还没生成，
把历史缩短就是纯粹的销毁。C6 的回填必须是原子的（`summary` 与 `context` 一起换），
在那之前 `compress()` 只能"算完就报错"。

### 修订 R3：切分写成 `_split.py` 的模块级函数，`reserve_tokens` 是**参数**

三个理由，每条都对应一个具体约束：

- **要能被任意预算调用**：C6 的 E1（`reserve_ratio` 太大 → 一条都压不掉）在 AgentScope 里
  是「降级成 `reserve_ratio = 0` **再调一次**」（`:395-401`）。把预算从 config 里读的函数
  做不到这件事；参数化就自然支持。**已验证 `reserve_tokens=0` 会退化成「全量压缩」**。
- **要能单独测**：这正是你旧 `context/builder.py` 的教训——切分混在 `build()` 里就无法单独测。
- **不能 import `_context`**：它要用 `build_model_input`，而 `_context` 要用它 → 循环导入。
  解法是**把「固定前缀」当参数传进来**（由 `ContextManager` 用 C1 的组装算好）。
  顺带的好处：`_split.py` 连 `AgentState` 都不需要知道，只认 `list[Message]`。

---

## C4.1 设计原理（先理解）

### 1) 第一层：为什么「从尾部往前凑够预算」，而不是「从头部切掉超出的部分」

两种写法在**总量**上等价，但在**保留什么**上完全不同：

- **从头部切**：算出「总共超了多少」，从最旧的开始丢到够为止。问题是"够"的标准是**全局的**：
  一旦前缀（system + 摘要）本身变大，所有历史都得重算一遍位置。
- **从尾部往前凑**（AgentScope 的做法）：从最近的一条开始往回加，直到**再加一条就超预算**为止。

后者的价值在于它直接对应真正的目标——**保留最近的对话**。而且它有个天然的好处：
**判定只看「保留区自己够不够」，不看「压缩区有多大」**，所以前缀变长只会让保留区更短，
不会改变算法的形状。§C4.5 的第 11 条测试就是把这点钉住（给旧摘要会让保留区变短）。

### 2) 为什么预算判定是「严格小于」而边界是「大于等于」

```python
while split_at > 0:
    if await model.count_tokens(prefix + context[split_at - 1:], tools) >= reserve_tokens:
        break
    split_at -= 1
```

- 循环**继续**的条件是「含边界那条**仍然小于**预算」——即保留区必须**真的装得下**；
- 循环**停止**（`>=`）时的那条就是**边界**：它进不去，所以它归压缩区。

对照 AgentScope 的写法是「不满足就提前返回」：

```python
if reserved_tokens >= to_reserved_tokens:
    break          #  _agent.py:2013
```

两者等价（`>=` 是同一个方向），但**必须一致地选一边**：混用 `<` 和 `>` 会让「恰好等于预算」
的那一档行为不确定，而它恰好是测试最容易构造的一档（用固定值的桩模型一行就能摆出来）。

⚠️ **一个真实的坑**：AgentScope 的第二层用的是**严格 `>`**（`:2041`），第一层用的是 `>=`
（`:2013`）。同一个函数里两层的边界条件不一致——不是笔误就是历史遗留，但在它那儿无伤大雅
（两层差一条 block 的量级）。**我们只有一层，所以只需要一条规则**，这也是砍掉第二层的一个附带好处。

### 3) 第二层（我们的配对规则）：为什么它是一行

先看**不修**它会怎样。设保留区从 `context[i]` 开始，而 `context[i]` 是一条 `role=tool` 消息：

```
context[i-1] = assistant，里面是 ToolCallBlock(id="c1")     ← 进压缩区（会被摘要掉）
context[i]   = tool，ToolResultBlock(tool_call_id="c1")    ← 留在保留区
```

出站时这条 tool 消息**引用了一个模型再也看不到的 `tool_call_id`**。你的 `_formatter`
不会拦它（它只检查「`ToolResultBlock` 必须在 `role=tool` 消息里」），
但 **OpenAI 协议会拦**：`role=tool` 消息必须能对应到前面某条 assistant 消息的 `tool_calls`。
于是整次请求 400——而且是**压缩之后才开始报错**，现场离病因很远。

修法就是一行：

```python
while split_at < len(context) and context[split_at].role is Role.TOOL:
    split_at += 1
```

**为什么一行就够**：因为协议还要求「一次工具调用的结果消息**紧跟在**它的调用消息之后、且连续」。
在这条前提下，可以证明「只要保留区首条不是 tool 消息，保留区里就不会有任何孤儿结果」（见 §C4.2）。
而"首条是 tool 消息"这件事，恰好把两类问题一次覆盖：

- **边界切在结果消息上**：`context[i]` 本身就是 tool → 推后；
- **整个边界 assistant 消息进了压缩区**：它的结果消息紧随其后，成了保留区的头 → 也被推后。

也就是说：**含 `ToolCallBlock` 的消息一旦被压掉，它的结果消息会被这条规则自动带走**，
不需要额外写「把结果也一起压掉」的逻辑。

### 4) 为什么修正是「把结果一起压掉」，而不是「把调用一起留下」

反方向也说得通：既然结果必须配得上调用，那就把 `context[i-1]` 的 assistant 消息也留在保留区。
但那会**推翻第一层的决策**——第一层刚刚才算出「它装不下」。
让保留区反向膨胀，等于让预算失效，还会把触发压缩的那 20% 余量一点点吃掉。

AgentScope 选的是同一个方向（它的修正是把 `block_index` **往后推**，即把更多内容丢进压缩区，
`:2060`），代价也一样：**修正之后，保留区可能明显短于预算**。
这是刻意的取舍：**宁可少留，不可留出对话模型看不懂的历史。**

### 5) 切分的四条不变量（这才是本模块真正的验收标准）

| # | 不变量 | 为什么重要 |
|---|---|---|
| ① | `to_compress + to_reserve == context`（顺序、元素 identity 都不变） | 不丢消息、不造新消息。C6 回填时 `state.context = to_reserve`，若有消息凭空消失，摘要里也不会提到它 |
| ② | `to_reserve` 里每个 `tool_call_id`，其 `ToolCallBlock` 也在 `to_reserve` 里 | 保留区对模型**自洽**（§C4.1-3） |
| ③ | `to_reserve` 非空时 `count(prefix + to_reserve) < reserve_tokens` | 保留区**真的**压进了预算 |
| ④ | 配对修正**之前**的切分点是紧致的：`count(prefix + context[i-1:]) >= reserve_tokens` | 第一层没有多留（否则等于白白丢历史） |
| ⑤ | 修正只会让 `to_reserve` 变短 | 代价方向正确（§C4.1-4） |

③ 与 ④ 是一对：**③ 管上界、④ 管紧致**。少一个，"从尾部往前凑"就退化成"随便留几条"。

### 6) 前缀与工具也必须计入预算

`reserve_tokens` 是保留区的预算，但**保留区不是全部输入**：真实请求是
`prefix（system + 旧摘要）+ 保留区`。如果只数保留区，那么前缀越大，越容易"压完之后还是超"。
所以 `split_for_compression` 的第一次估算就是 `count(prefix + ...)`，
`tools` 也一样透传给 `count_tokens`（C2 会把 schema 算进去）。

**这条也解释了一个设计细节**：`prefix` 由 `ContextManager` 用 C1 的 `build_model_input` 算出来，
而不是 `_split.py` 自己拼 `[Message.system(sp)]`。否则"估算口径"与"请求口径"又会分成两套——
正是 C1 建立 `ModelInput` 时要避免的那件事。

### 7) 为什么 `split_at == 0` 要单独返回，而不是继续往下走

`split_at == 0` 的含义是：**把整段历史都保留下来，仍然在预算内**。
这不是错误，而是 AgentScope 那条异常路径 E1 的入口——「`reserve_ratio` 太大，一条都压不掉」。
AgentScope 在**调用方**检测 `len(msgs_to_compress) == 0` 然后降级重试（`:386-401`）。

我们保持同样的分工：**C4 只如实地返回「无可压」，不做降级**。降级属于 C6
（它才知道"重试一次"这个动作是否安全）。

---

## C4.2 配对规则的正确性证明

先把前提写清楚（记作 **P**）——它是**协议**要求，不是我们的假设：

> **P**：对每一次工具调用，若其调用消息在 `context` 的下标为 `j`，它的结果消息占据
> `j+1, j+2, …, j+m`（`m ≥ 1`），即**紧随其后且连续**。

（你的 `append_tool_result` 每次追加一条、主循环「决策 → 执行 → 回灌」的顺序，
都在维持 P；`_formatter` 出站时的展开也依赖它。）

**定理**：若 `context[i]` 不是 `role=tool` 消息，则 `context[i:]` 里每条 `role=tool`
消息的调用消息也在 `context[i:]` 里。

**证明**：设某条结果消息在下标 `p ≥ i`，其调用消息在 `j`。由 P，`p ∈ [j+1, j+m]`，故 `j+1 ≤ p`。

1. 假设 `j+1 < i`。由 `j+1 ≤ p` 与 `i ≤ p` 得 `j+1 < i ≤ p ≤ j+m`，即
   `i ∈ (j+1, j+m]`，也就是 `i ∈ [j+1, j+m]`。按 P，下标落在 `[j+1, j+m]` 的
   **每一条**都是该死工具调用的结果消息，于是 `context[i]` 是 `role=tool`——与题设矛盾。
   故 `j+1 ≥ i`，即 `j ≥ i-1`。
2. 再排除 `j = i-1`：此时 `j+1 = i ≤ p`，说明 `i ∈ [j+1, j+m]`，同样得到
   `context[i]` 是 `role=tool`，矛盾。故 `j ≥ i`。

所以调用消息也在 `context[i:]` 内。∎

**推论**：`while split_at < len(context) and context[split_at].role is Role.TOOL: split_at += 1`
之后，保留区必然满足不变量 ②。∎

> 顺带说明：这个证明**只在 P 成立时有效**。如果主循环允许在两次工具结果之间插入别的消息，
> P 就破了，规则也可能失效。所以 P 必须写进 `_state.py` / 主循环的约定里，
> 而不是留在"大家都知道"的默契层面。

---

## C4.3 接口契约：`hello_agents/state/_split.py`（新建）

```python
"""压缩切分：决定哪些消息进摘要、哪些原样保留。

只做一件事——**从尾部往前凑够保留预算**。本模块不修改 `state`、不造新消息：
返回的两个列表是 `context` 的两个切片，元素就是原对象。

由此得到三条可以被断言的性质：

1. `to_compress + to_reserve == context`（不丢消息，也不造"半个消息"）；
2. `to_reserve` 里出现过的每个 `tool_call_id`，其 `ToolCallBlock` 也在 `to_reserve` 里
   （保留的对话对模型是自洽的）；
3. 它 **不 import `_context`**：固定前缀由调用方算好传进来——否则就是循环导入。
"""

from collections.abc import Sequence
from typing import NamedTuple

from ..model import ChatModelBase, Message, Role, Tool


class SplitResult(NamedTuple):
    """切分结果：`to_compress` 交给摘要，`to_reserve` 原样留在上下文里。"""

    to_compress: list[Message]
    to_reserve: list[Message]


async def split_for_compression(
    context: Sequence[Message],
    *,
    model: ChatModelBase,
    prefix: Sequence[Message],
    reserve_tokens: float,
    tools: Sequence[Tool] | None = None,
) -> SplitResult:
    """把 `context` 切成「进摘要」与「原样保留」两段。

    Args:
        context: 未压缩历史（即 `state.context`）。
        model: 用来估算——**必须是真实请求用的那个模型**，否则预算口径不一致。
        prefix: 固定前缀（system + 可选旧摘要）。它不参与切分，但**必须计入预算**：
            否则「前缀 + 保留区」合起来仍会把窗口撑爆。
        reserve_tokens: 保留区的 token 预算，通常是 `reserve_ratio × context_size`。
            做成参数而不是内部读 config，是为了让 C6 的 E1 兜底能用 `0` 再调一次。
        tools: 与真实请求同一份工具描述；计入预算才能对齐。

    Returns:
        `SplitResult`：两个切片，合起来等于 `context`（顺序、元素都一致）。
    """
    if not context:
        return SplitResult([], [])

    # 第一层：从尾部往前，凑到「再加一条就超预算」为止
    split_at = len(context)
    while split_at > 0:
        candidate = [*prefix, *context[split_at - 1 :]]
        if await model.count_tokens(candidate, tools) >= reserve_tokens:
            break
        split_at -= 1

    # 整段历史都在预算内：没有可压的，如实返回（E1 的降级由 C6 决定）
    if split_at == 0:
        return SplitResult([], list(context))

    # 第二层：配对修正 —— 保留区首条不能是 `role=tool`
    #   一旦含 ToolCallBlock 的消息被压掉，它的结果消息会被这一条自动带走。
    while split_at < len(context) and context[split_at].role is Role.TOOL:
        split_at += 1

    return SplitResult(list(context[:split_at]), list(context[split_at:]))
```

**不做 `__init__.py` 导出**：它是内部协作者，不是上层要用的东西。
测试直接 `from hello_agents.state._split import SplitResult, split_for_compression`——
与既有测试用 `hello_agents.model._formatter` 是同一个习惯。

---

## C4.4 接口契约：`hello_agents/state/_context.py`（两处改动）

**① 加 `reserve_tokens` 属性**（C3 §C3.7 说好"消费者是 C4，C4 加"）：

```diff
     @property
     def threshold_tokens(self) -> float:
         """触发线：`trigger_ratio × model.context_size`。"""
         return self.config.trigger_ratio * self.model.context_size
 
+    @property
+    def reserve_tokens(self) -> float:
+        """保留区预算：`reserve_ratio × model.context_size`。"""
+        return self.config.reserve_ratio * self.model.context_size
+
```

**② import 加一行、`compress()` 把切分接上**：

```diff
 from ..model import ChatModelBase, Message, Tool
 from ._config import ContextConfig
+from ._split import split_for_compression
 from ._state import AgentState
```

```diff
         self._raise_if_uncompressible(state)
 
-        raise NotImplementedError(
-            "C3 只交付判定与守卫；切分由 C4 接入："
-            "docs/context/07-milestone-c4-split.md"
-        )
+        out = build_model_input(state, system_prompt=system_prompt, tools=tools)
+        # 固定前缀 = 组装结果去掉 context 的那一段（system + 可选旧摘要）。
+        # 复用 build_model_input 而不是自己拼，保留预算的计数才与真实请求同源。
+        prefix = out["messages"][: len(out["messages"]) - len(state.context)]
+
+        split = await split_for_compression(
+            state.context,
+            model=self.model,
+            prefix=prefix,
+            reserve_tokens=self.reserve_tokens,
+            tools=tools,
+        )
+
+        raise NotImplementedError(
+            f"切分完成：压缩 {len(split.to_compress)} 条 / 保留 {len(split.to_reserve)} 条；"
+            f"摘要由 C5 接入：docs/context/08-milestone-c5-summary.md"
+        )
```

> **为什么把切分结果拼进异常消息**：它让"接线顺序对不对"在运行期一眼可见
> （`压缩 8 条 / 保留 1 条` 只可能来自真实切分），同时也让 C6 接手时能直接看到数字。
> 顺带避免"算了却不用"的死变量。

**🔴 必改：C3 那条测试的断言**

`tests/test_state_c3.py::test_overflow_with_history_points_at_c4` 断言了 `"C4" in str(...)`——
现在消息指向 C5。改成断言更稳的东西：

```python
async def test_overflow_with_history_points_at_c5():
    manager = ContextManager(_StubModel(fixed=80, context_size=100))

    with pytest.raises(NotImplementedError) as excinfo:
        await manager.compress(_state(), system_prompt=SYSTEM_PROMPT)

    assert "切分完成" in str(excinfo.value)   # 切分真的接上了
    assert "C5" in str(excinfo.value)         # 下一棒在哪
```

---

## C4.5 留给你的动手任务

### 1) 新建 `_split.py`，改 `_context.py` 两处（§C4.3 / §C4.4）

### 2) 写演示 `examples/state_c4_split.py`

要求离线（桩模型），构造一段**含两次工具调用**的历史，然后：

- 遍历若干 `reserve_tokens`（含 0、极大、以及会切在工具消息上的那一档），打印
  `压 N / 留 M`、保留区 tokens、以及"未修正的切点"；
- 对每一档都断言并打印**配对完备性**（保留区里没有孤儿 `tool_result`）；
- 打印一个**对照**：把配对规则去掉后，某个预算下会切在哪条 tool 消息上，
  以及那会让出站 payload 引用一个看不见的 `tool_call_id`。

### 3) 写测试 `tests/test_state_c4.py`

`_split.py` 是纯函数，所以**这组测试完全离线、不需要任何网络**。

**桩与素材**

```python
class _StubModel(ChatModelBase):
    """context_size 固定，count_tokens 走基类真口径。"""

    def __init__(self, context_size: int = 100_000) -> None: ...
    async def _call_api(self, messages, stream, tools=None, tool_choice=None, response_format=None):
        raise AssertionError("切分不该发请求")


def _history() -> list[Message]:
    """9 条：user → assistant(call c1) → tool(c1) → user
              → assistant(text + call c2 + call c3) → tool(c2) → tool(c3)
              → user → assistant(text)"""
```

**核心策略：把预算穷举掉**（这是本任务的验收主线，比挑几个用例强得多）

```python
@pytest.mark.parametrize("reserve_tokens", list(range(0, 130, 3)) + [10**9])
async def test_split_invariants_hold_for_every_budget(reserve_tokens: float) -> None:
    """不变量 ①②③⑤ 对**每一个**预算都成立。"""
```

一条测试覆盖四条不变量（`①` 拼接相等 + `is` 同一批对象；`②` 无孤儿 + 首条非 tool；
`③` 保留区 `< reserve_tokens`（空则豁免）；`⑤` 修正只变短）。

**再单独钉三条**

- **④ 紧致**：独立测「配对修正**之前**的切点 `i` 满足 `count(prefix + context[i-1:]) >= reserve_tokens`」
  （用一个 helper 复算朴素切点，不要在实现的返回值上断言——修正之后紧致性**不再成立**，这正是它的代价）；
- **配对规则真的会触发**：固定 `reserve_tokens` 到一个会切在 `tool` 消息上的值，
  断言「朴素切点落在 `role=tool` 上」且「实现的切点 > 朴素切点」；
- **`reserve_tokens=0`** → `to_compress == list(context)`、`to_reserve == []`
  （C6 的 E1 兜底路径，必须现在就钉死）。

**三条边界**

- 空 `context` → `([], [])`；
- `reserve_tokens` 极大 → `to_compress == []`、`to_reserve == list(context)`；
- 前缀变大（给个非空 `summary`）→ 同样预算下保留区**不长于**无摘要时（证明 prefix 真的计入）。

**`ContextManager` 侧两条**

- `manager.reserve_tokens == 0.1 * 100_000`；注入 `ContextConfig(reserve_ratio=0.2)` 后跟着变；
- `compress()` 在有历史时抛的 `NotImplementedError` 消息含 `"切分完成"`（切分确实接上了）。

---

## C4.6 边界与裁剪

- **不做** block 级切分（§C4.0 R1）：形态不匹配、收益近零、代价是脏 `id`；
- **不做** `deepcopy`：返回切片，元素与原对象同一批。上层（与 formatter）不得就地修改（与 C1 同一条约定）；
- **不做** E1 降级重试（`to_compress` 为空 → `reserve_tokens=0` 再来一次）：**C6 的事**，
  C4 只如实返回"无可压"；
- **不做** E2（压缩输入本身超长）的逐条丢弃：也是 C6；
- **不做** 单条超大工具结果的截断/外置：第 ③ 块 `Offloader`，本轮整体裁剪。
  C4 对它的处理是「整条进压缩区」——**单块不可分**；
- **不做**「压缩后预留回复空间」之类的额外预算：`reserve_ratio` 已经承担这个语义，
  再叠一层就是双重记账。

---

## C4.7 验收清单

**参考实现已在本地穷举验证：46 个预算 × 5 条不变量全过；配对规则触发 16 次。**

- [ ] `_split.py`：`SplitResult` + `split_for_compression` 与契约一致；
- [ ] `_split.py` **没有** import `_context` 或 `_state`（无循环导入）；
- [ ] `_context.py`：加 `reserve_tokens` 属性；`compress()` 接上切分；
- [ ] `tests/test_state_c3.py` 那条 `"C4"` 断言已按 §C4.4 改掉；
- [ ] `examples/state_c4_split.py` 输出符合注释期望（含配对完备性检查）；
- [ ] `tests/test_state_c4.py` 全绿（穷举不变量 + 三条边界 + 两条 manager 侧）；
- [ ] 零回归；
- [ ] ruff 干净（含 format）：

```powershell
uv run ruff format hello_agents/state examples/state_c4_split.py tests/test_state_c4.py
uv run ruff check hello_agents/state examples/state_c4_split.py tests/test_state_c4.py
uv run pytest tests/test_state_c4.py -q
uv run pytest tests/test_state_c3.py tests/test_state_c4.py -q
uv run pytest tests/test_state_c0.py tests/test_state_c1.py tests/test_model_count_tokens.py -q
```

---

## C4.8 agentscope 源码对照

| 你的实现 | agentscope（只读） | 对照 / 裁剪 |
|---|---|---|
| `split_for_compression(context, *, model, prefix, reserve_tokens, tools)`（模块级纯函数） | `Agent._split_context_for_compression(to_reserved_tokens, tools)`（`:1975`） | 同职责；从 Agent 上摘下来做成**无状态函数**，预算参数化（§C4.0 R3） |
| `prefix` 由调用方用 `build_model_input` 算好传入 | 函数内部 `await self._get_system_prompt()` 拼 `system_msg`（`:1994-2003`） | **有意偏离**：避免 `_split → _context` 循环导入，也让前缀口径只有一处（C1） |
| 第一层消息级回扫（`:2005-2015`） | 同 | **逐字同构**；绕着 `>=` 的方向也一致 |
| `split_at == 0` → 返回「无可压」 | `if msg_index < 0: return [], deepcopy(self.state.context)`（`:2017-2018`） | 同语义；我们不 `deepcopy`（不改内容就不需要） |
| **第二层：一行 `role is not TOOL` 的配对规则** | 第二层 block 级切分（`:2031-2068`）+ 块内配对修正（`:2045-2060`） | **必要偏离**：它的配对是同一条消息内部的事，我们的是跨消息。见 §C4.0 R1 与 §C4.2 的证明 |
| 切分点是**消息边界**，永远不造新消息 | `deepcopy(boundary_msg)` 造两条半消息（`:2026-2027`），并靠 `len(...) > 0` 决定是否入列（`:2070-2074`） | 我们形态下不需要；`to_compress + to_reserve == context` 这条不变量其间也更强 |
| 配对修正方向：推后切分点（丢更多） | 同方向：`block_index = max(remain_result_ids.values())`（`:2060`） | 一致——**宁可少留，不可留出自洽不了的历史** |
| 配对键：`ToolResultBlock.tool_call_id` ↔ `ToolCallBlock.id` | 用同一个 `block.id` 两边配对（`:2052-2055`） | **形态差异**：它的实现细节是"两边同 `id`"，我们显式用 `tool_call_id` |
| 只有两层 | 三层（含 block 级） | 裁剪，见 §C4.0 R1 |
| `reserve_tokens` 是参数；`reserve_tokens=0` 退化成全量压缩 | 调用方在 `to_compress` 为空时用 `0 * self.model.context_size` 重调（`:398-401`） | **同一个行为**，只是参数化后不需要特判分支；降级动作归 C6 |
| 只读、不修改 state | 同样不改（回填在调用方 `:536-537`） | 一致 |
| 不 import `_context` | 是 Agent 的方法，无所谓 | 我们的分层副产品 |

---

## C4.9 附录：AgentScope 的第二层（block 级切分）—— 读它，但不要它

放在附录是因为**它是理解 AgentScope 的关键**，而我们的形态让它成了多余的零件。
下面是它那 40 行的骨架（`_agent.py:2025-2068`），注释是我加的：

```python
# 边界消息一分为二：一份进压缩区，一份进保留区
boundary_msg_to_compress = deepcopy(boundary_msg)
boundary_msg_to_reserve = deepcopy(boundary_msg)

# 从最后一块往前试：保留区能容下末尾 k 块吗？容不下就停
block_index = len(boundary_msg_content) - 1
while block_index >= 0:
    attempt_msg.content = boundary_msg_content[block_index:]
    if await self.model.count_tokens(system_msg + [attempt_msg] + msgs_to_reserve, tools) > to_reserved_tokens:
        break                      # 注意：这里用的是严格 >（与第一层的 >= 不一致）
    block_index -= 1

# 块内配对修正：保留区里有没有「配不上调用」的结果块？
remain_result_ids = {}
for i in range(len(boundary_msg_content) - 1, block_index, -1):
    block = boundary_msg_content[i]
    if isinstance(block, ToolResultBlock):
        remain_result_ids[block.id] = i
    elif isinstance(block, ToolCallBlock):
        remain_result_ids.pop(block.id, None)
if remain_result_ids:
    block_index = max(remain_result_ids.values())   # 把孤儿结果也推进压缩区

boundary_msg_to_compress.content = boundary_msg_content[: block_index + 1]
boundary_msg_to_reserve.content = boundary_msg_content[block_index + 1 :]
```

读它的三个要点：

1. **它为什么必须存在**：一条 assistant 消息里既可能有很多正文块，又可能有很多工具调用块，
   而"一次 reply 只有一条消息"意味着这条消息可能比整个保留预算还大。不切块就只能整条丢。
2. **它的配对修正为什么在块内**：调用块与结果块同住一条消息，所以"配对"就是**同一条消息内部**
   两个下标的关系。
3. **它的两处不一致值得记下来**：第一层 `>=`、第二层 `>`；并且它切出来的两条消息**共享 `id`**。
   我们的实现里这两个问题都不存在——只有一层、也不造半条消息。

**如果你将来想让 C4 支持 block 级**（比如接了多模态、一条消息里塞几个大块），
最小改动是加一条**受限**的切块规则：**只切「不含 `ToolCallBlock` 的 assistant 消息」**。
这样切点不会丢弃任何调用，也就不会孤立任何结果——§C4.2 的证明继续有效，
第二层的复杂度也就停在"取末尾 k 块"这一层。**含调用的消息整条走**，交给配对规则处理。

---

## 完成后

把 `_split.py`、`_context.py` 的 diff、`examples/state_c4_split.py` 的输出、
`pytest tests/test_state_c3.py tests/test_state_c4.py -q` 结果贴给我 review。

通过后进 **C5：结构化摘要**（`docs/context/08-milestone-c5-summary.md`）——
`SummarySchema` 的 5 个字段、`ContextConfig` 补上 `compression_prompt` / `summary_template` /
`summary_schema`、复用你 M7 的 `extract_structured`（含失败自修），
再把 `compress()` 里那行 `NotImplementedError` 换成真正的一次摘要调用。