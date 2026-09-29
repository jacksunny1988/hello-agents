# 里程碑 3：最小调用闭环（非流式一问一答，三家通）

> 目标：把 M1 的消息模型、M2 的注册表/客户端串起来，实现
> `ChatUsage → ChatResponse → Formatter → ChatModelBase → OpenAICompatModel`，
> 用**非流式**完成一次「发消息 → 收完整回复」，并在三家（先 DeepSeek）跑通。
>
> 本阶段**不做流式聚合（M4）、不做重试/取消（M5）、不做工具（M6）**：
> Formatter 只处理文本；ChatResponse 只做非流式解析。保持小步。

---

## 1. 前置知识：asyncio（这是本阶段的主要新东西）

### 1.1 协程与 `await`

用 `async def` 定义的函数，调用时**不会立即执行函数体**，而是返回一个**协程对象**：

```python
async def f():
    return 42

coro = f()          # 得到 coroutine，函数体还没跑
result = await coro # await 才真正驱动它执行，拿到 42
```

`await` 的含义：「**挂起当前协程，等这个可等待对象（awaitable）有结果，期间事件循环可以去跑别的任务**」。
网络请求是 IO 等待，正是异步最能发挥作用的地方——等响应时不堵住整个程序。

### 1.2 事件循环与 `asyncio.run`

协程需要一个**事件循环（event loop）**来调度。普通脚本里用 `asyncio.run(...)` 启动：

```python
import asyncio

async def main():
    ...

asyncio.run(main())   # 创建事件循环、跑完 main、关闭循环
```

> 常见错误：在普通同步代码里直接 `await xxx`——`await` 只能出现在 `async def` 内部。
> 所以入口要么是 `async def main()` + `asyncio.run`，要么在 Jupyter（自带循环）里。

### 1.3 AsyncOpenAI 为什么是 `await`

`AsyncOpenAI` 的每个请求方法都是**协程方法**：

```python
completion = await client.chat.completions.create(
    model="deepseek-flash",
    messages=[{"role": "user", "content": "你好"}],
    stream=False,
)
```

- `stream=False`：`await` 一次，直接拿到**完整的 `ChatCompletion`**；
- `stream=True`（M4）：拿到的是异步流，用 `async for chunk in ...` 逐块读。

### 1.4 本阶段你只需要记住的心智模型

```
Message(你的模型)  --Formatter-->  dict(OpenAI 要的)
        │
        └─ await client.chat.completions.create(stream=False)
                        │
                        ▼
              ChatCompletion(SDK 对象)  --Formatter-->  ChatResponse(你的模型)
```

中间这一步「await 一次网络请求」由基类/子类协作完成；重试、流式都是在这一步周围加东西。

---

## 2. 接口契约

### 2.1 `_usage.py`：`ChatUsage`

记录一次调用的用量与耗时（dataclass 或 Pydantic 均可）：

| 字段 | 类型 | 来源（非流式） |
|---|---|---|
| `input_tokens` | `int` | `completion.usage.prompt_tokens` |
| `output_tokens` | `int` | `completion.usage.completion_tokens` |
| `time` | `float` | 你在调用前后计时（`time.perf_counter()` 差值） |
| `cache_read_tokens` | `int` | 可选，`usage.prompt_tokens_details.cached_tokens`，没有则 0 |

> 用量字段在不同 SDK 版本可能为 None，读取时要做空值兜底（getattr / 默认 0）。

### 2.2 `_response.py`：`FinishedReason` 与 `ChatResponse`

```python
class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"   # M5 才会真正用到
```

`ChatResponse`（**M3 最小版**，M4 再补流式累加方法）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | `list[TextBlock | ThinkingBlock]` | M3 只可能含文本（thinking 解析可选，见 2.4） |
| `id` | `str` | `completion.id`（可能为 None，兜底自生成） |
| `created_at` | `str` | 创建时间 ISO 字符串 |
| `usage` | `ChatUsage | None` | 用量 |
| `finished_reason` | `FinishedReason` | 本阶段恒为 COMPLETED |
| `is_last` | `bool` | **为 M4 预留**，非流式恒为 `True` |

提供一个**从非流式 ChatCompletion 构造**的入口（classmethod，概念上）：
`ChatResponse.from_completion(completion, elapsed: float) -> ChatResponse`，
内部取 `choices[0].message.content` 包成 `TextBlock`，并构造 `ChatUsage`。

> 注意空 choices / content 为 None 的兜底，避免索引错误。

### 2.3 `_formatter.py`：双向转换（M3 只做文本）

**`to_openai_messages(messages: list[Message]) -> list[dict]`**

M3 规则（只处理文本，工具相关 M6 再加）：
- 一条 `Message` 转成一个 dict：`{"role": role.value, "content": <拼接文本>}`；
- 把 content 里所有 `TextBlock.text` 按顺序拼到 `content`；
- `ThinkingBlock`：非流式、且不回传思考时**先忽略**（思考链是否回传各家规则不同，M4/M6 再处理），不要让它污染 content；
- role 为 system/user/assistant 都按此处理。

**`from_completion(...)` 的解析逻辑**可以放在 Formatter，也可由 2.2 的 classmethod
调用——二选一，职责是「SDK 对象 → 你的 ChatResponse」，不要两边重复实现。

> 设计提醒：Formatter 是**唯一**知道 OpenAI dict 长什么样的地方。模型类、上层都不应
> 直接 import openai 的类型，这样将来换协议只改 Formatter / 新增子类。

### 2.4 Thinking 字段（可选，建议 M4 一起做）

非流式响应里，思考内容可能在 `message.reasoning_content`（DeepSeek）或额外字段；
普通 `message.content` 是最终答案。**M3 可先只取 `content`**，把 reasoning 的解析
留到 M4（流式时更直观）。如果你想现在做，就把它包成 `ThinkingBlock` 放在
`TextBlock` 之前。

### 2.5 `_base.py`：`ChatModelBase`

M3 最小骨架（重试 M5、结构化 M7 再加）：

```python
class ChatModelBase:
    def __init__(self, config: ModelConfig, client: AsyncOpenAI) -> None: ...

    async def __call__(self, messages: list[Message]) -> ChatResponse:
        # M3：直接 return await self._call_api(messages)
        # M5 会在这里包重试/取消；M4 会处理流式返回
        ...

    @abstractmethod
    async def _call_api(self, messages: list[Message]) -> ChatResponse: ...
```

- 基类持有 `config`（含 model 名、能力位）与 `client`；
- `__call__` 是**模板方法**：现在很薄，但它是 M4/M5 加聚合与重试的固定挂载点；
- 子类只实现 `_call_api`。

### 2.6 `providers/_openai_compat.py`：`OpenAICompatModel`

```python
class OpenAICompatModel(ChatModelBase):
    async def _call_api(self, messages: list[Message]) -> ChatResponse:
        openai_msgs = to_openai_messages(messages)
        t0 = time.perf_counter()
        completion = await self.client.chat.completions.create(
            model=self.config.model,
            messages=openai_msgs,
            stream=False,
        )
        return ChatResponse.from_completion(completion, time.perf_counter() - t0)
```

**M3 用「config 参数化」一个类即可表达三家**，不必现在就写 dashscope/deepseek/zhipu
三个子类（它们在出现独有能力时，M7/M8 再分化）。提供一个便捷工厂，例如：

```python
def build_model(spec: str) -> OpenAICompatModel:
    provider, model = parse_spec(spec)
    cfg = get_model_config(provider, model)
    return OpenAICompatModel(cfg, build_client(cfg))
```

> 思考点：为什么 `_call_api` 放在 compat 子类、而不是基类？因为「如何真正调 API」
> 是**因协议而异**的部分；将来接一个非兼容协议，新增一个 `_call_api` 即可，
> 基类的 `__call__`（重试/聚合）完全复用。

---

## 3. 数据流向（一次完整调用）

```
build_model("deepseek:deepseek-flash")
   │  parse_spec → cfg(ModelConfig) + client(AsyncOpenAI)
   ▼
OpenAICompatModel
   │  await model([Message.user("用一句话解释什么是 token")])
   ▼
__call__  →  _call_api
   │  to_openai_messages: [{"role":"user","content":"用一句话解释什么是 token"}]
   │  await client.chat.completions.create(model=..., messages=..., stream=False)
   ▼
ChatCompletion  →  ChatResponse(content=[TextBlock(...)], usage=ChatUsage(...))
```

---

## 4. 留给你的动手任务（M3）

1. 实现 `_usage.py` 的 `ChatUsage`（含 None 兜底）。
2. 实现 `_response.py` 的 `FinishedReason`、`ChatResponse` 与 `from_completion`。
3. 实现 `_formatter.py` 的 `to_openai_messages`（只处理文本，忽略 thinking）。
4. 在 `_base.py` 实现 `ChatModelBase`（__init__ / __call__ / 抽象 _call_api）。
5. 在 `providers/_openai_compat.py` 实现 `OpenAICompatModel._call_api` 与 `build_model`。
6. 写一个入口脚本（你自己的 demo），用 `asyncio.run` 调 DeepSeek 完成一问一答，
   打印回复文本与 `usage`（input/output tokens、耗时）。
7. 配齐 key 后，把 spec 换成 dashscope / zhipu，确认**同一套代码**三家都能通。

> 测试由你写：`to_openai_messages` 与 `ChatResponse.from_completion` 是纯函数，
> 可以用一个伪造的 completion（简单对象/dict 转 SimpleNamespace）做离线单测，不依赖网络。

## 5. M3 自检清单

- [ ] 能解释「协程为何不立即执行、await 在等什么、事件循环谁来启动」；
- [ ] 非流式一问一答在 DeepSeek 跑通，能拿到正文和 usage；
- [ ] 同一代码不改逻辑、只改 spec 就能切到另两家（key 就绪后）；
- [ ] Formatter 是唯一接触 OpenAI dict 的地方，模型/上层不直接依赖 SDK 类型；
- [ ] `__call__` 与 `_call_api` 分层清晰，能说出 M4/M5 将分别挂在哪；
- [ ] completion 缺 usage / 空 content 等异常输入不会让你的代码崩溃。

## 6. agentscope 源码对照

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| `ChatModelBase.__call__` | `model/_base.py` `__call__` | 模板方法：M3 薄，M5 加 retry、M4 加流式聚合 |
| `_call_api` | `model/_base.py` 抽象方法 + 各 provider `_model.py` | 子类只实现真正调 API |
| `to_openai_messages` | `formatter/`（`OpenAIChatFormatter`） | 统一消息 → 厂商 dict |
| `ChatResponse.from_completion` | `model/_model_response.py` | 非流式 → 统一响应；M4 再看 append_* |
| `ChatUsage` | `model/_model_usage.py` | prompt/completion/cached tokens |
| `OpenAICompatModel` | `model/_openai_chat/_model.py` | 非流式 create 与响应解析 |

---

## 7. 完成后

把 `_usage.py / _response.py / _formatter.py / _base.py / providers/_openai_compat.py`
（或你合并后的等价文件）连同一次 DeepSeek 非流式调用的运行结果贴给我 review。
通过后进入 **M4：流式聚合（SSE chunk 解析、delta/tool_call/thinking 累加、
`async for`、边打印边得到完整响应）**，那是本学习路径的重点难点。
