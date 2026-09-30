# M6 工具调用闭环 —— 设计规格

> 对应需求：`docs/model/06-milestone-6-tools.md`
> 编写时间：2026-09-30
> 状态：**待审核**（审核通过后才开始实施）

---

## 1. 目标与边界

**目标**：让模型能声明要调工具 → 上层执行 → 结果以正确角色回灌 → 模型据此继续。
覆盖非流式与流式 `tool_calls` 拼接、`tool_choice`、并行调用。

**边界**（沿用规格）：

- **主循环属于上层**。model 层只提供到「执行工具调用 + 生成回灌消息」为止，
  「要不要再来一轮」的判断写在 demo 里。
- 不改动旧包 `hello_agents/tools/`（`BaseTool` / `ToolResponse` 那套同步 `run` + `arun`）。
  model 包内新建一套轻量协议，两者并存、互不依赖。
- 不引入真实网络请求作为验收手段——离线测试覆盖全部逻辑分支，真实网络只做最后
  一次端到端确认（且需用户同意）。

**已确认的四个决策**（本次澄清结论）：

| # | 决策 | 选择 |
|---|---|---|
| 1 | `tools` / `tool_choice` 怎么进请求 | **每次调用传参**：`await model(messages, tools=[...], tool_choice="auto")` |
| 2 | Tool 协议形态 | **ABC**，`function_spec()` 由基类给具体实现 |
| 3 | 流式 index 对齐 | **给 `ToolCallBlock` 加 `index: int \| None`**，累加器按 index 归位 |
| 4 | `execute_tool_calls` 执行方式 | **并发**（`asyncio.gather`），返回顺序与输入一致 |

---

## 2. 数据模型改动

### 2.1 `ToolCallBlock` 增加 `index`

```python
class ToolCallBlock(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: str
    id: str
    index: int | None = None   # 流式对齐用；非流式为 None
```

**为什么加在块上**：实测 `openai` 3.16.2，非流式 `message.tool_calls` 的元素只有
`id / function / type`（**没有** `index`），而流式 `delta.tool_calls` 的元素是
`index / id / function / type` 且 `index` **必填**。也就是说 `index` 是协议自己用来
标识「同一次响应里的第几个调用」的字段，模型层把它丢掉才需要另开旁路去传。

**为什么不走旁路**：规格 §3.4 的提示是「累加器里维护 `index -> id` 映射」。要建这个
映射，`index` 仍然得先到达累加器；把它挂在块上是最短的一条路，且块自带 index 后
累加器可以直接按 index 归位，连映射表都不需要。多一个字段换掉一张表 + 一条隐式通道。

### 2.2 `ToolResultBlock` 增加 `name`

```python
class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    output: str
    is_error: bool = False
    name: str | None = None    # 出站 role=tool dict 的 name 字段
```

**为什么**：规格 §3.2 要求出站 dict 带 `name`，但 `Message.name` 是消息级的，
而规格同时要求「同一条 Message 里多个 ToolResultBlock 展开成多条」——消息级 name
在多结果时是歧义的。name 属于「某个结果」，就该落在结果块上。

### 2.3 `ChatResponse.content` 联合类型

```python
content: list[TextBlock | ThinkingBlock | ToolCallBlock]
```

响应里不会出现 `ToolResultBlock`（那是出站方向的东西），不加。

### 2.4 `FinishedReason` 增加 `TOOL_CALLS`

```python
class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    TOOL_CALLS = "tool_calls"
```

---

## 3. Formatter 改动

`_formatter.py` 仍然是**唯一**知道 OpenAI 格式长什么样的地方。

### 3.1 出站：`to_openai_messages`（替换 M3 的 `NotImplementedError`）

转换规则：

| 输入 | 输出 |
|---|---|
| 普通消息（无工具块） | `{"role": ..., "content": <所有 TextBlock 拼接>}`（同 M3） |
| assistant 含 `ToolCallBlock` | `{"role":"assistant", "content": <文本 or None>, "tool_calls":[{"id","type":"function","function":{"name","arguments"}}]}` |
| role=tool 含 `ToolResultBlock` | 每个结果**展开一条** `{"role":"tool","tool_call_id":..., "content": output}`，`name` 非空时附上 |

**结构约束（违反即 `ValueError`，不静默丢弃）**：

- 含 `ToolCallBlock` 的消息必须是 `role=assistant`；
- 含 `ToolResultBlock` 的消息必须是 `role=tool`；
- `role=tool` 的消息必须至少含一个 `ToolResultBlock`，且不得含其它类型块。

这三条沿用 M3 那两处 `NotImplementedError` 的设计意图：工具消息一旦被悄悄吞掉，
模型会收到一段缺了上下文的对话，而错误要等很远才暴露。放开 `NotImplementedError`
不等于放开校验。

**`content` 取 `text or None` 而不是恒为 `None`**：规格 §3.2 的例子写 `content: null`，
是因为那条消息本来就没有正文。真实场景里模型可能「先说一句再调工具」，正文不能丢。

**`ThinkingBlock` 仍然忽略**（M3 决策不变）：思考链是否回传各家规则不同，不能混进
`content`。

**`name` 字段按需附带**：`name` 为 `None` 时不写这个 key，避免给严格校验的端点发
`"name": null`。

### 3.2 出站：新增 `to_openai_tools`

```python
def to_openai_tools(tools: Sequence[Tool]) -> list[dict]:
    return [t.function_spec() for t in tools]
```

放在 `_formatter` 而不是内联在 `_openai_compat`，是为了维持本模块「唯一 OpenAI 格式
出口」的定位。

### 3.3 入站（非流式）：`from_completion`

- `message.tool_calls` 非空 → 每个转一个 `ToolCallBlock(id, name, arguments)`，
  **原样搬运**（`arguments` 保持字符串，不在这里 `json.loads`——解析归
  `execute_tool_calls`，那里才需要把它变成 dict）；
- `index` 保持 `None`；
- `finish_reason` 映射后写入 `finished_reason`（见 §3.5）。

### 3.4 入站（流式）：`parse_chunk`

新增解析 `delta.tool_calls`，每个分片产出一个 `ToolCallBlock`：

```python
ToolCallBlock(
    id        = tc.id or "",              # 续片没有 id → 空串（匿名），靠 index 归位
    name      = tc.function.name or "",   # 续片没有 name → 空串
    arguments = tc.function.arguments or "",
    index     = tc.index,                 # 必填，原样带出
)
```

**片内顺序**：thinking → text → tool_calls。实测三家不会在同一帧里混发正文与
tool_calls，这个顺序只是给「万一混发」定个确定行为。

**`id` / `name` 用空串而不是随机 uuid**：与 M4 的匿名块同一个理由——随机 id 会让
每片都建新块。区别在于文本块靠「空 id 共享同一个身份」合并，而 tool_call 并行多路，
空 id 会互相串，所以**必须靠 index 归位**，这正是规格 §2 强调的那一点。

### 3.5 `finish_reason` 映射

```python
def _map_finish_reason(raw: str | None) -> FinishedReason:
    return FinishedReason.TOOL_CALLS if raw == "tool_calls" else FinishedReason.COMPLETED
```

- 非流式：`choices[0].finish_reason` 直接映射；
- 流式：末片带 `finish_reason`，映射后写进该增量的 `finished_reason`。

---

## 4. 累加器改动（`ChatResponse`）

### 4.1 新增 `append_tool_call`

```python
def append_tool_call(self, block: ToolCallBlock) -> None:
    existing = self._find_tool_call(block)
    if existing is None:
        self.content.append(block.model_copy())
    else:
        if block.name and not existing.name:
            existing.name = block.name      # 首片给了 name 之后就不再改
        if block.id and not existing.id:
            existing.id = block.id
        existing.arguments += block.arguments   # 分片字符串拼接
```

**查找规则**：

- `block.index is not None` → 按 `index` 匹配（流式路径，并行多路互不串）；
- `block.index is None` → 按非空 `id` 匹配（非流式 / 整体追加路径）。

**`model_copy()`**：新块是拷贝进去的，避免累加器与入站增量共享同一个对象后互相
污染。`append_text` 走的是「新建块」而不是「塞入传入对象」，这里对齐同一语义。

**`arguments` 拼接在累加器、`json.loads` 不在**：累加器只做字符串层面的搬运，
解析留给 `execute_tool_calls`——流式拼接期间每个中间态都是非法 JSON，提前解析必炸。

### 4.2 `append_chat_response` 去掉 `NotImplementedError`

```python
for block in delta.content:
    if isinstance(block, TextBlock):        self.append_text(...)
    elif isinstance(block, ThinkingBlock):  self.append_thinking(...)
    elif isinstance(block, ToolCallBlock):  self.append_tool_call(block)
    else:  # ToolResultBlock
        raise NotImplementedError(...)
```

`ToolResultBlock` 的 raise **保留**：它出现在响应方向是编程错误，不是「M6 待实现」。
文案要相应改写，不再指向 M6。

### 4.3 `finished_reason` 的吸收规则

```python
if delta.finished_reason is not FinishedReason.COMPLETED:
    self.finished_reason = delta.finished_reason
```

**只吸收非默认值**，理由是可验证的：`COMPLETED` 是默认值，等价于「这一帧没说」。
usage 载体帧（`choices=[]`）恒为默认值，而无条件吸收会把末片刚写进去的 `TOOL_CALLS`
又冲回 `COMPLETED`——dashscope 的载体帧正是在末片**之后**到达。

配套地，`_base._stream` 里 `acc.finished_reason = reason` 改成**只在取消时覆盖**：

```python
if reason is FinishedReason.INTERRUPTED:
    acc.finished_reason = reason
```

否则收尾时同样会把 `TOOL_CALLS` 冲掉。

### 4.4 新增 `get_tool_calls()` 与 `to_message()`

```python
def get_tool_calls(self) -> list[ToolCallBlock]:
    return [b for b in self.content if isinstance(b, ToolCallBlock)]

def to_message(self) -> Message:
    """响应 → 可回灌进历史的 assistant 消息（规格 §3.6 的 response_to_message）。"""
    return Message(role=Role.ASSISTANT, content=list(self.content))
```

`to_message` 放在 model 层而不是 demo 里：它无歧义、每轮循环都要写，且角色与块的
搬运规则属于模型层的知识。`ThinkingBlock` 一并带上，由 Formatter 出站时忽略。

---

## 5. Tool 协议与执行（新模块 `hello_agents/model/_tool.py`）

### 5.1 `Tool`（ABC）

```python
class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]     # JSON Schema

    def function_spec(self) -> dict:         # 具体实现，子类不用重复写
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> object: ...
```

`parameters` 是**现成的 JSON Schema dict**，不做「传 Pydantic 模型自动推导」那层
魔法——docstring 里写清配方即可：`parameters = MyParams.model_json_schema()`。
两套机制并存只会让人猜哪套生效。

`ToolChoice` 类型别名同处声明：

```python
ToolChoice = Literal["auto", "none", "required"] | dict[str, Any]
```

「强制调某个函数」直接写字面量 `{"type":"function","function":{"name":"..."}}`，
不额外封 helper——一行字典，封了反而要多记一个名字。

### 5.2 `execute_tool_calls`

```python
async def execute_tool_calls(
    tool_calls: Sequence[ToolCallBlock], tools: Mapping[str, Tool]
) -> list[Message]:
```

**并发**（决策 4）：`asyncio.gather`，返回顺序与入参顺序一致。
工具之间无依赖是模型发起并行调用的前提，串行只会让总耗时累加。

**每个调用独立兜底**，任何失败都转成 `is_error=True` 的结果，绝不炸整批：

| 情况 | output | is_error |
|---|---|---|
| 未知工具名 | `未知工具 'xxx'` | True |
| `arguments` 不是合法 JSON | `参数不是合法 JSON: <解析错误>` | True |
| 解析出来不是 dict | `参数必须是 JSON 对象` | True |
| `run` 抛异常 | `f"{type(exc).__name__}: {exc}"`，另用 `logger.exception` 留完整栈 | True |
| 返回结果不可 JSON 序列化 | 序列化错误信息 | True |
| 正常 | `str` 原样；其它 `json.dumps(result, ensure_ascii=False)` | False |

**给模型看的是简短错误文本，完整 traceback 进日志**——把栈塞进回灌消息既浪费 token
又干扰模型，但排查时又必须能看到。

**返回形态**：每个 tool_call 一条 `Message(role=Role.TOOL, content=[ToolResultBlock(...)])`，
`ToolResultBlock.name` 填工具名，`Message.name` 留空。

---

## 6. 模型层接口改动

### 6.1 `ChatModelBase`

```python
async def __call__(
    self,
    messages: list[Message],
    tools: Sequence[Tool] | None = None,
    tool_choice: ToolChoice | None = None,
) -> ChatResponse | AsyncGenerator[ChatResponse]: ...

@abstractmethod
async def _call_api(
    self, messages: list[Message], stream: bool,
    tools: Sequence[Tool] | None = None,
    tool_choice: ToolChoice | None = None,
) -> ChatResponse | AsyncGenerator[ChatResponse]: ...
```

`tools` / `tool_choice` **透传**，基类不做解释——重试、取消、聚合的收口逻辑与工具
无关，保持模板方法不变。

### 6.2 `OpenAICompatModel._call_api`

```python
kwargs = {}
if tools:
    kwargs["tools"] = to_openai_tools(tools)
if tool_choice is not None:
    kwargs["tool_choice"] = tool_choice
```

非流式与流式两条路径都带上。**不传时不写这两个 key**，让端点用自己的默认值。

### 6.3 `build_model`

**签名不变**（决策 1 的直接推论：工具随调用走，不随构造走）。

### 6.4 `__init__.py` 导出

新增 `Tool`、`ToolChoice`、`execute_tool_calls`。

---

## 7. Demo 与测试

### 7.1 `examples/model_m6_demo.py`（需真实网络，实施时单独征求同意）

三段，都用 ASCII 标记（控制台 GBK，`✓`/`✗` 会 `UnicodeEncodeError`）：

1. **非流式闭环**：单工具，走完 `tool_calls → tool → stop` 全流程，打印每轮角色；
2. **并行两个工具**：一次请求触发两个调用，验证 `arguments` 不串、两条结果都回灌；
3. **流式 tool_calls**：打印分片到达过程，收尾 `finished_reason` 为 `tool_calls`，
   拼接结果与非流式一致。

工具用两个自造的即可（如 `get_weather` 假数据、`calculator`），不引外部 API——
demo 要演示的是闭环，不是天气服务。

### 7.2 `tests/test_model_tools.py`（全离线）

| 分组 | 用例要点 |
|---|---|
| 出站 Formatter | assistant+tool_calls 形状；role=tool 单结果；**一条消息多结果展开成多条**；`name` 缺省时不出现该 key；三类结构约束各报错一次 |
| 入站 Formatter | `from_completion` 的 tool_calls 与 `finish_reason="tool_calls"`；`parse_chunk` 的分片解析（首片带 id/name、续片为空串） |
| 累加器 | **并行两路 index 不串**（规格 §2 那个三片例子）；`arguments` 拼接正确；index 为空时按 id 合并；`ToolResultBlock` 仍 raise |
| `finished_reason` | 末片 `tool_calls` 经载体帧后仍为 `TOOL_CALLS`（回归 §4.3 那个坑） |
| `execute_tool_calls` | 成功 / 未知工具 / 坏 JSON / 非 dict / `run` 抛错 / 不可序列化，各产出正确 `is_error`；返回顺序与入参一致 |
| 离线闭环 | 手写假模型按脚本返回 `tool_calls` → 再返回 `stop`，断言两轮消息历史配对正确（`id ↔ tool_call_id`） |
| `to_message` / `get_tool_calls` | 响应转消息后块完整、角色为 assistant |

手写 fake/stub，不用 `unittest.mock`；中文 docstring、英文用例名。

### 7.3 必须同步修改的既有测试

`tests/test_model_retry.py` 里 `_FakeModel` / `_BlockingModel` / `_StreamingFake` 的
`_call_api(self, messages, stream)` 签名要加上 `tools=None, tool_choice=None`，
否则基类透传 kwargs 时会 `TypeError`。**这是签名变更的必要连带改动，不是顺手重构。**

---

## 8. 有意不做（不是遗漏）

| 项 | 理由 |
|---|---|
| `parallel_tool_calls` 参数 | 协议里有，但三家兼容端点支持不一，且它只是「让模型别并行」的开关；M6 不暴露，需要时加一个可选参数即可 |
| `supports_tool_calls=False` 时报错 | 三家都是 `True`，加一条永不触发的错误路径是仪式；等真有 provider 不支持时再加 |
| `_get_retryable_exceptions` 补 408/409 | 沿用 M5 结论，仍不在规格 §2 表格内 |
| `FinishedReason.LENGTH` | 本次不扩；`finish_reason="length"` 会落到 `COMPLETED`。M7（结构化输出）关心截断，届时一并处理 |
| 工具结果回灌时的 `ThinkingBlock` 出站 | 沿用 M3 决策，继续忽略 |
| 主循环 / 多轮编排 | 规格明确划归上层 |

---

## 9. 影响面清单

**改动**
- `hello_agents/model/message.py` —— `ToolCallBlock.index`、`ToolResultBlock.name`
- `hello_agents/model/_response.py` —— 联合类型、`FinishedReason.TOOL_CALLS`、
  `append_tool_call`、`append_chat_response` 分派与 `finished_reason` 吸收、
  `get_tool_calls`、`to_message`
- `hello_agents/model/_formatter.py` —— 出站三处规则、`to_openai_tools`、
  `from_completion` tool_calls、`parse_chunk` tool_calls、`_map_finish_reason`
- `hello_agents/model/_base.py` —— `__call__` / `_call_api` 签名、`_stream` 的
  `finished_reason` 覆盖条件
- `hello_agents/model/providers/_openai_compat.py` —— 透传 `tools` / `tool_choice`
- `hello_agents/model/__init__.py` —— 导出

**新增**
- `hello_agents/model/_tool.py` —— `Tool` / `ToolChoice` / `execute_tool_calls`
- `tests/test_model_tools.py`
- `examples/model_m6_demo.py`

**不动**
- `hello_agents/tools/`（旧工具包）、`hello_agents/core/message.py`、
  以及与 M6 无关的未提交改动（`.gitignore` / `uv.lock` / `.claude/` /
  `docs/plans/context-plan-progress.md`）

---

## 10. 验收标准

1. `tests/test_model_tools.py` 全绿，`tests/test_model_retry.py` 改签名后仍全绿；
2. 全量 `pytest tests/ -q` 不新增失败（既有 `test_embedding.py` 那条失败与本工作线无关）；
3. `ruff check` / `ruff format --check` 在本工作线涉及文件上干净；
4. 真实网络：三家各跑通一次非流式闭环；至少一家跑通流式 tool_calls 与并行调用；
5. 能回答规格 §6 自检清单的每一条。
