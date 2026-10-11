# 里程碑 C2：token 估算 `ChatModelBase.count_tokens`（粗估兜底 + 可覆写）

> **目标**：给 `model` 层加一个**离线、确定性、与出站同源**的输入规模估算——`count_tokens`。
> 它是 C3「要不要压缩」的判据，也是 C4「保留多少」的度量单位。没有它，整个压缩链路无从触发。
> **对标（只读）**：`src/agentscope/model/_base.py:350-434`（`count_tokens`）、
> `src/agentscope/agent/_config.py:57,62`（`trigger_ratio` / `reserve_ratio`）、
> `src/agentscope/agent/_agent.py:347-353`（阈值判定的调用点）。
> **分工特例**：按上一轮约定，`model/` 的改造由**我动手**，你负责 review。
> **本次状态**：✅ 代码已改（`hello_agents/model/_base.py`）+ 契约测试已绿（`tests/test_model_count_tokens.py`，
> 7 passed）——**待你 review**。

---

## C2.0 前置：`tools` 的类型必须与 `__call__` 逐字一致

C1 的 **R4 修订**（见 `04-milestone-c1-build-input.md`）已经指出：`model` 层的 `tools` 参数是
**`Sequence[Tool] | None`**（`hello_agents.model.Tool` 对象），不是 `list[dict]`。

C2 把这条约束**再收紧一层**——因为 `count_tokens` 存在的全部意义，就是让下面这两行成立：

```python
out = build_model_input(state, system_prompt=sp, tools=tools)   # C1 的产物
response = await model(**out)                                   # 真实请求
estimated = await model.count_tokens(**out)                     # 同一个 out，直接摊开
```

要能 `**out` 摊开到两个不同方法上，**两个方法的同名参数必须同类型、同默认值**：

| 参数 | `__call__` | `count_tokens` |
|---|---|---|
| `messages` | `list[Message]` | `list[Message]` |
| `tools` | `Sequence[Tool] \| None = None` | `Sequence[Tool] \| None = None` |
| `tool_choice` | `ToolChoice \| None = None` | **无**（估算不依赖） |
| `response_format` | `dict \| None = None` | **无**（估算不依赖） |

**契约**：`count_tokens` 的参数是 `__call__` 参数的**子集**，且同名参数类型逐字一致。

> 为什么 AgentScope 那边是 `list[dict]`？因为它的 `_prepare_model_input` 产出的 `tools` 就已经是 dict。
> 差异的根因是「谁负责调 `function_spec()`」：AgentScope 在 Agent 层转换，你的项目在 formatter 层转换。
> **契约的形式是「与 `__call__` 对齐」，内容随各自的架构走**，见 §C2.8。

---

## C2.1 设计原理（先理解）

### 1) 为什么这层必须有 token 估算

三条路都走不通，只剩本地估算：

- **靠上一轮的 `usage.input_tokens`** —— 只反映**上一次**请求。而压缩判定要回答的是
  「**下一次**请求会不会超」，下一次往往刚多了几条工具结果；首轮请求更是没有 usage。用过期数字做判断，
  等于用昨天的油表决定今天加不加油。
- **靠服务端报错** —— 超长报错是**事后**的，且各家的错误串不一致，解析它就是把厂商错误格式焊进业务逻辑。
- **靠 `len(text)` 字符数** —— 中文与英文的字符/token 比差 3 倍以上，用它做阈值必然两边都不准。

真正需要的是一种能力：**对「还没有发出去的输入」估价，且同一份输入每次估出同一个数**。
所以它是**纯计算 + 确定性**的（这也是它不该联网的原因）。

### 2) 为什么放在 `ChatModelBase` 上，而不是 `state/`

| 理由 | 说明 |
|---|---|
| **知识归属** | 「哪些块会出站、schema JSON 长什么样」只有 `model` 知道（`_formatter` 就在它隔壁）。放在 `state` 就是把 formatter 的知识抄第二遍，两处必然漂移 |
| **依赖方向** | `state → model` 是硬约束。估算放在 `model` 上，`state` 只是调用方；反过来则 `model → state`，方向就破了 |
| **扩展点靠多态** | 真 tokenizer 各厂商不同。要做「可覆写」，方法就得长在 provider 类上——多态是白送的；放在 `state` 里就只能写 `if provider == ...` |
| **测试** | 放在 `model` 上可用 `_FakeModel` 桩离线测（你的 `test_model_retry.py` 已有现成模式） |

### 3) 为什么口径是「字节 ÷ 4」而不是真 tokenizer

- **离线、零成本、确定性**：不联网、不下载词表、不产生费用，单测可以钉死数字；
- **精度够用**：英文约 4 字节 ≈ 1 token；中文 3 字节/字 ≈ 0.75 token/字，真实值多在 0.6~1.1。
  混合文本的整体误差在 1.3 倍以内；
- **压缩只需要「比值」**：判据是 `estimated < trigger_ratio * context_size`。**单调、一致**比绝对精确重要——
  只要阈值比 1 小，系统性偏差就能被吸收；
- **可回归**：「同输入必得同数」让 C4 的预算算术可被断言。换成真 tokenizer 后，词表版本一变测试就飘。

### 4) 但为什么仍然做成 `async` + 可覆写

你的 `pyproject.toml` 里已经有 `tiktoken>=0.14.0`，将来一定会有人想换精确版。
而**真 tokenizer 的第一件事就是去下载 BPE 词表文件**——那是 I/O，可能失败、可能要超时、可能要走代理。

**设计规则：接口的形状由最贵的那个实现决定。**
现在就写成 `async def`，将来在某个 provider 子类里覆写时，**一个调用点都不用改**：

```python
class DashScopeModel(ChatModelBase):
    async def count_tokens(self, messages, tools=None) -> int:
        enc = await _get_tiktoken_encoding()      # 可 await 的真实 tokenizer
        return sum(len(enc.encode(t)) for t in _outbound_strings(messages, tools))
```

当前默认实现**没有任何 I/O**，`await` 是零成本的。AgentScope 同样选了 `async`
并在 docstring 里明说「子类可覆写」（`_base.py:362`）。

### 5) 与出站同源：为什么复用 `to_openai_tools`

```python
chunks.append(json.dumps(to_openai_tools(tools), ensure_ascii=False))
```

`to_openai_tools` 就是**真实请求**用的那个函数。如果这里手写一份 JSON 拼接，
那么将来有人改了 `Tool.function_spec()`（比如加个字段），
**估算与请求就悄悄错开了**——而且是无声的。

这是本项目反复出现的同一条原则：**同一个知识只能有一个实现，其余地方复用它。**
（对比你自己在 C1 里坚持的「组装只有一个出口」。）

### 6) 计入 / 不计入：判据是「formatter 会不会把它发出去」

**唯一的判据**：`to_openai_messages` 出站时用到了吗？用到了就计入，没用到就不计入。
这样估算才真正是「出站规模的估计」，而不是「历史数据的大小」。

| 内容 | 计入？ | 依据 |
|---|---|---|
| `msg.role.value` | ✅ | `to_openai_messages` 每条都写 `"role": msg.role.value` |
| `TextBlock.text` | ✅ | 拼进 `content` |
| `ToolCallBlock.name` | ✅ | 进 `tool_calls[].function.name` |
| `ToolCallBlock.arguments` | ✅ | 进 `tool_calls[].function.arguments`（通常是全量 payload） |
| `ToolResultBlock.output` | ✅ | 进 `content` |
| tools schema JSON | ✅ | `json.dumps(to_openai_tools(tools))` |
| `ThinkingBlock.thinking` | ❌ | `to_openai_messages` **明确丢弃**（module docstring 第 4 条）。估一个发不出去的东西 = 虚高 |
| `ToolCallBlock.id` / `.index` | ❌ | `id` 确实出站（32 位 hex ≈ 8 token/次），但占比小；`index` 不出站。**已知小额偏小** |
| `ToolResultBlock.tool_call_id` / `.name` / `.is_error` | ❌ | 同上，`tool_call_id` 出站但短 |
| `Message.id` / `.timestamp` / `.name` | ❌ | 都不出站（`to_openai_messages` 只读 `role` + `content`） |

> **和 AgentScope 最重要的一处差异**：AgentScope **计入** `ThinkingBlock`（`:385`），我们**不计**。
> 这是**必须的偏离**——它的 formatter 会把思考链出站（各家规则不同），而你的 `_formatter` 明确丢弃。
> 照抄它的口径，就会让你在「思考很长但正文很短」的场景里误判超阈值。

### 7) 误差方向：偏小，而偏小是**危险方向**

把上面两条合起来看，默认口径的偏差是：

| 文本类型 | 字节 ÷ 4 | 真实值 | 方向 |
|---|---|---|---|
| 英文 | ≈ 4 字节/token | ≈ 4 字节/token | ≈ 准 |
| 中文 | 3 字节/字 → 0.75 token/字 | ≈ 0.6~1.1 token/字 | **偏小** |
| 未计的 `id`（32 hex） | 0 | ≈ 8 token/次调用 | **偏小** |

**偏小意味着「以为还有空间、其实已经超了」**——比偏大危险（偏大只会让你早压一点，代价是略多的模型调用）。

所以：**这个偏差必须由 `trigger_ratio < 1` 留出的余量吸收，而不能靠"估得准"来兜底。**

```python
threshold = trigger_ratio * model.context_size      # 例：0.8 * 128000
if estimated_tokens < threshold: return             # 足足留了 20% 余量
```

AgentScope 把 `trigger_ratio` 的取值域**硬性限制在 `(0, 0.9)`**，
docstring 写的理由正是「为压缩本身预留上下文」（`_config.py:57-60`）——同一个思路。
（这条在 **C3** 才会落成 `ContextConfig`，此处先建立直觉。）

### 8) 一个容易走神的点：估算必须**单调**，但不必须**精确**

C4 的切分算法会反复问「如果只留最后 N 条，还会超吗」。这类二分/回扫的前提是：

- **确定性**：同一输入永远同一个数（`uuid`、`时间戳` 都不能进公式）；
- **单调性**：追加消息不会让估算变小（否则「留得越少反而越大」，回扫会跑飞）。

精度只影响「压缩晚一点/早一点」，**不**影响正确性。测试里那条
`test_adding_messages_never_decreases_the_estimate` 用的断言是 `>=` 而不是 `>`——
因为四舍五入偶尔会让两次相等，但**绝不允许回退**。

---

## C2.2 接口契约

### 签名

```python
async def count_tokens(
    self,
    messages: list[Message],
    tools: Sequence[Tool] | None = None,
) -> int:
```

- **同步语义、异步形状**：默认实现无 I/O，但保持 `async` 以容纳「换成远端 tokenizer」；
- `tools=None` 而非 `[]`：与 `__call__` 一致（空数组在部分端点语义不同，你 M6 已确认）；
- 返回 `int`；空输入返回 `0`。

### 完整代码（`hello_agents/model/_base.py`，已写入）

```python
    async def count_tokens(
        self,
        messages: list[Message],
        tools: Sequence[Tool] | None = None,
    ) -> int:
        """粗估这次输入会消耗多少 token —— **只数真正会发出去的内容**。

        用途：`hello_agents/state` 的压缩阈值判定与切分预算都建立在本估算之上。

        口径（刻意粗，但**与出站同源**）：把会出站的字符串按顺序拼接，
        取 UTF-8 字节数 ÷ 4 后四舍五入。

        - 计入：`role`、`TextBlock.text`、`ToolCallBlock.name` /
          `arguments`、`ToolResultBlock.output`，以及 tools 的 JSON schema；
        - **不计**：`ThinkingBlock`（`to_openai_messages` 出站时丢弃它）、
          各种 `id` / `index` / `timestamp`（出站不用）；
        - 中文一字 3 字节、按 ÷4 折算约 0.75 token，而真实 tokenizer 多在
          1 token/字左右，所以本估算整体**略偏小**——由调用方
          `trigger_ratio < 1` 留出的余量吸收。

        要更准就在 provider 子类里覆写：先用 `to_openai_messages` /
        `to_openai_tools` 拿到出站形态，再用该厂商真实的 tokenizer 数。
        做成 `async` 正是为了让「换成远端 tokenizer」不必改动任何调用点；
        当前默认实现没有任何 I/O。
        """
        chunks: list[str] = []
        for msg in messages:
            chunks.append(msg.role.value)
            for block in msg.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
                elif isinstance(block, ToolCallBlock):
                    chunks.append(block.name)
                    chunks.append(block.arguments)
                elif isinstance(block, ToolResultBlock):
                    chunks.append(block.output)
                # ThinkingBlock 不计：formatter 出站时会忽略它。

        if tools:
            # 复用出站同一个函数产出 schema，避免「估算口径」与「请求口径」漂移。
            chunks.append(json.dumps(to_openai_tools(tools), ensure_ascii=False))

        return int(len("".join(chunks).encode("utf-8")) / 4 + 0.5)
```

### 需要新增/修改的 import

```diff
 import asyncio
 import itertools
+import json
 import logging
 import time

 from openai import AsyncOpenAI

+from ._formatter import to_openai_tools
 from ._registry import ModelConfig
 from ._response import ChatResponse, FinishedReason
 from ._tool import Tool, ToolChoice
-from .message import Message
+from .message import Message, TextBlock, ToolCallBlock, ToolResultBlock
```

**无循环导入风险**：`_formatter` 只依赖 `_response` / `_tool` / `_usage` / `message`，
不依赖 `_base`；`_response` / `_usage` 也不 import `_tool`。已实测导入通过。

### 取整口径：`int(x / 4 + 0.5)`

- 这是 **四舍五入**，不是 `ceil`：`ceil` 会给每次估算白送 1 token；
- 也**不能**写 `round(x / 4)`：Python 的 `round` 是**银行家舍入**，`round(2.5) == 2`
  而 `int(2.5 + 0.5) == 3`，与 AgentScope 的数会对不上；
- **与 AgentScope `:434` 逐字一致**，所以两边的估算值可以直接横向比较。

### 手算校验（测试里已钉死）

| 输入 | 拼接串 | 字节 | `int(b/4 + 0.5)` |
|---|---|---|---|
| `[Message.user("abcd")]` | `"user" + "abcd"` | 8 | **2** |
| `[Message.user("中文")]` | `"user" + "中文"` | 4 + 6 = 10 | **3** |
| `[]` | `""` | 0 | **0** |
| `[Message.user("hi")]` + `[_EchoTool()]` | `"user" + "hi"` + schema JSON | 6 + 183 | **47** |

> 最后一行是本次调试的真实教训：`json.dumps(..., ensure_ascii=False)` 产生的是
> **字符**串，中文在 UTF-8 里是 3 字节——算期望值时用 `len(str)` 会得到 175 而不是 183。
> **凡是要喂给 `.encode("utf-8")` 的，长度都必须按字节算**（这个坑测试里已经用 `_estimate()` 钉住）。

---

## C2.3 我做的改动（逐行 diff）

```diff
--- a/hello_agents/model/_base.py
+++ b/hello_agents/model/_base.py
@@ -1,5 +1,6 @@
 import asyncio
 import itertools
+import json
 import logging
 import time
 from abc import ABC, abstractmethod
@@ -8,10 +9,11 @@
 from openai import AsyncOpenAI
 
+from ._formatter import to_openai_tools
 from ._registry import ModelConfig
 from ._response import ChatResponse, FinishedReason
 from ._tool import Tool, ToolChoice
-from .message import Message
+from .message import Message, TextBlock, ToolCallBlock, ToolResultBlock
 
 logger = logging.getLogger(__name__)
@@ -105,6 +107,49 @@ class ChatModelBase(ABC):
         return self._stream(messages, tools, tool_choice, response_format)
 
+    async def count_tokens(
+        self,
+        messages: list[Message],
+        tools: Sequence[Tool] | None = None,
+    ) -> int:
+        ...（见 §C2.2 完整代码）
+
     async def _stream(
```

改动面：`git diff --numstat` = **+47 / -1**（1 处新增含 docstring、3 行 import 调整）。

---

## C2.4 契约测试（`tests/test_model_count_tokens.py`）

**这个文件的作用是把口径钉死：改口径 = 改契约，必须同步改文档。** 7 个用例：

| 用例 | 钉住什么 |
|---|---|
| `test_empty_input_is_zero` | `[]` → `0` |
| `test_formula_is_pinned_by_hand_computation` | 手算两个数（`2` / `3`）与实现一致 |
| `test_thinking_block_is_not_counted` | 加一整块思考文，估算**一字不加** |
| `test_tool_call_and_result_are_counted` | 正文 / 调用 / 结果三段**各自精确值**都对（不只是 `>`） |
| `test_tools_schema_is_counted` | tools schema 按 **UTF-8 字节**计入 |
| `test_adding_messages_never_decreases_the_estimate` | 单调性 `>=`（四舍五入允许相等） |
| `test_count_tokens_is_overridable` | 子类覆写生效（`12345`） |

关键复算函数——**测试不复制实现，而是复算同一条口径**（否则实现写错、测试跟着错，两边一起对）：

```python
def _estimate(*chunks: str) -> int:
    """按实现同一条口径独立复算期望值：UTF-8 字节数 ÷ 4 四舍五入。"""
    return int(len("".join(chunks).encode("utf-8")) / 4 + 0.5)
```

`_StubModel` 只实现 `_call_api` 并**主动抛 `AssertionError`**——
一旦 `count_tokens` 意外走了网络路径，测试会立刻炸而不是偷偷发请求。

---

## C2.5 留给你的动手任务

### 1) 验收我的改动（**主要任务**）

- 读 `hello_agents/model/_base.py` 的 `count_tokens`：口径判断（§C2.1 第 6 条）你是否认同？
- 读 `tests/test_model_count_tokens.py`：7 个用例有没有「测了等于没测」的？
- 有异议直接说——**这是评审，不是通知**。

### 2) 修 `state/_context.py` 的 `tools` 类型（C1-R4 遗留）

```diff
-from typing import TypedDict
+from collections.abc import Sequence
+from typing import TypedDict
 
-from ..model import Message
+from ..model import Message, Tool
@@ class ModelInput(TypedDict):
     messages: list[Message]
-    tools: list[dict] | None
+    tools: Sequence[Tool] | None
@@ def build_model_input(
     *,
     system_prompt: str,
-    tools: list[dict] | None = None,
+    tools: Sequence[Tool] | None = None,
 ) -> ModelInput:
```

运行时行为不变（`tools` 本来原样透传），改完 `pytest tests/test_state_c1.py -q` 应仍全绿。

### 3) 补一个**签名一致性**测试（把 §C2.0 的契约变成可执行断言）

```python
import inspect

def test_count_tokens_signature_matches_call() -> None:
    """C1 的 out 要能同时摊给 __call__ 与 count_tokens，签名必须对齐。"""
    call = inspect.signature(ChatModelBase.__call__)
    count = inspect.signature(ChatModelBase.count_tokens)
    for name in ("messages", "tools"):
        assert count.parameters[name].annotation == call.parameters[name].annotation
        assert count.parameters[name].default == call.parameters[name].default
```

> 这条测试的价值：将来谁把 `tools` 改回 `list[dict]`，**测试先红**，不等到 C3 才炸。

### 4)（可选）加一个**偏差观测**测试，但**不要**把 tiktoken 变成硬依赖

用 `pytest.importorskip("tiktoken")` 写一个「粗估 / 真实」对比，只打印比值、只断言
「粗估 ≤ 真实 × 1.5」这类**宽松上界**。目的是让你对偏差量级有实感，不是让 CI 依赖词表下载。

---

## C2.6 边界与裁剪

- **不做**真 tokenizer 接入——只留扩展点（§C2.4 第 4 项是可观测的替代）；
- **不做**多模态块估算（AgentScope 的 `DataBlock` 一律按 `2000` token 估，`:32`）——你 model 层没有 `DataBlock`；
- **不做** `HintBlock`（AgentScope 的「临时提示」块）——你 model 层没有这个类型；
- **不做**「已知未知块类型」告警（AgentScope `:419` 会 `logger.warning`）——你的 block 是
  `Literal` 判别联合，**不可能**出现未知类型，加告警是死代码；
- **不做**缓存/记忆化——估算本身极便宜（纯字符串拼接），提前优化只会引入「缓存失效」这一类新 bug；
- **不做**「一次估算同时返回 messages/tools 的明细」——C3 只需要一个总数；需要拆明细时再加，
  不要为了可能的将来把返回值做成结构体。

---

## C2.7 验收清单

- [ ] `count_tokens` 的 `messages` / `tools` 与 `__call__` **逐字一致**（§C2.0）；
- [ ] `tests/test_model_count_tokens.py` 全绿（7 passed）；
- [ ] 你补的**签名一致性**测试全绿；
- [ ] `state/_context.py` 的 `tools` 类型已改（C1-R4）；
- [ ] ruff 干净：

```powershell
uv run ruff format --check hello_agents/model/_base.py tests/test_model_count_tokens.py
uv run ruff check hello_agents/model/_base.py tests/test_model_count_tokens.py
```

- [ ] 零回归（本次实测基线 **134 passed**）：

```powershell
uv run pytest tests/test_model_tools.py tests/test_model_structured.py tests/test_model_retry.py tests/test_state_c0.py tests/test_state_c1.py -q
```

---

## C2.8 agentscope 源码对照

| 你的实现 | agentscope（只读） | 对照 / 裁剪 |
|---|---|---|
| `count_tokens(messages, tools: Sequence[Tool] \| None)` | `count_tokens(messages: list[Msg], tools: list[dict] \| None)`（`:350`） | **同位置、同职责**；参数类型随各家 `__call__` 走（它传 dict，我们传 `Tool` 对象）。见 §C2.0 |
| 计入 `msg.role.value` | **不计** role | **有意偏离**：role 确实会出站。每条消息 +1~2 token，方向偏保守（更早触发压缩），可忽略 |
| 不计 `ThinkingBlock` | **计入** `block.thinking`（`:385`） | **必须有意的偏离**：你的 `to_openai_messages` 明确丢弃思考块。估一个发不出去的东西 = 虚高 |
| 逐块 `isinstance` 分发 + 未知类型静默跳过 | `isinstance` 分发 + `logger.warning("Unknown block type ...")`（`:419`） | 逻辑同构；告警裁剪（你的判别联合不可能有未知类型） |
| `json.dumps(to_openai_tools(tools), ensure_ascii=False)`（`:425` 对应） | `json.dumps(tools, ensure_ascii=False)`（`:425`） | 一致；我们多一步 `to_openai_tools`（复用出站函数），因为它拿到的已是 dict |
| `int(len(text.encode("utf-8")) / 4 + 0.5)` | `int(len(acc_text.encode("utf-8")) / 4 + 0.5)`（`:434`） | **逐字一致**，两边数值可直接比 |
| 无多模态 | `len(data_blocks) * 2000`（`:32,430`） | 裁剪：无 `DataBlock` |
| 无 `HintBlock` | 计入 `block.hint`（字符串或 `list[TextBlock｜DataBlock]`） | 裁剪：无该块类型 |
| 无缓存 | `cnt` 累加，无缓存 | 一致 |
| `async def` + 「子类可覆写」 | `async def` + docstring 「Subclasses may override ...」（`:362`） | 一致，保留同一扩展点 |
| 无 `self` 状态依赖（纯函数语义） | 同上（不读 `self`） | 一致；差别在于它没把 `role` 计进去 |
| 调用点：`await model.count_tokens(**out)`（C3 才写） | `estimated = await self.model.count_tokens(**kwargs)`，`kwargs` 来自 `_prepare_model_input()`（`_agent.py:347-348`） | **完全同构**——这正是我们坚持「返回值是 kwargs 字典」的原因（C1 §C1.1 第 4 条） |

---

## 完成后

请把 review 结论（认同 / 异议）+ 你补的签名一致性测试贴给我。

通过后进入 **C3：压缩入口与配置**——引入 `ContextConfig`（`trigger_ratio` / `reserve_ratio` /
`compression_prompt` / `summary_template` / `summary_schema`）、`needs_compression()`、`compress()` 骨架，
以及**首次引入 `ContextManager` 类**（它要同时持有 `model` 与 `config`，此时才有理由存在）。