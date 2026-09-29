# 里程碑 5：取消/中断 + 失败重试

> 目标：在基类模板方法里收口两类横切逻辑——
> ① **任务取消**：`asyncio.CancelledError` 时优雅结束、标记 `INTERRUPTED`；
> ② **失败重试**：对「可重试异常」按 `max_retries` + 退避自动重试，
>    不可重试异常立即抛出。
>
> 这些对所有厂商一致，所以必须在基类、且只写一次。

---

## 1. 前置知识：asyncio 的取消机制

### 1.1 取消是怎么发生的

每个协程通常运行在一个 `Task` 里。调用 `task.cancel()` 后，事件循环会在该任务
**当前 `await` 的挂起点**注入一个 `asyncio.CancelledError`，让它有机会收尾：

```python
task = asyncio.create_task(model(messages))
...
task.cancel()          # 请求取消
```

### 1.2 CancelledError 不是普通异常

Python 3.8+ 起，`CancelledError` 继承自 `BaseException`（不是 `Exception`），
这是刻意的——它表达「这个协程该结束了」，**不能被 `except Exception` 顺手吞掉**。

正确处理有两种策略，你要明确选一种并能说出理由：

- **A. 捕获 → 清理 → 重新抛出**（最通用）：在 `finally`/捕获里关闭资源、标记状态，
  然后 `raise`，让上层明确知道任务被取消。
- **B. 捕获 → 返回一个 INTERRUPTED 响应**（agentscope 的选择）：
  `__call__` 捕获 CancelledError，返回/产出一个
  `finished_reason=INTERRUPTED` 的 ChatResponse，让上层用「处理普通响应」的统一方式处理中断。

> agentscope 在 `model/_base.py` 的 `__call__` 与 `_stream` 里都捕获了 CancelledError：
> 非流式返回一个 INTERRUPTED 的空 ChatResponse；流式把累加器置为 INTERRUPTED 并收尾。
> 你沿用 B 即可，但要能解释「为什么不直接重新抛出」——为了给上层一个统一、可观测的收尾对象。

---

## 2. 前置知识：异常分类（重试的判据）

重试的核心问题不是「怎么重试」，而是「**哪些错误值得重试**」。重试一个 400 鉴权错误
毫无意义。OpenAI SDK 的异常体系（`openai` 包，建议**惰性 import**，保持 SDK 为可选依赖）：

| 异常 | 典型场景 | 是否可重试 |
|---|---|---|
| `APITimeoutError` | 请求超时 | 是 |
| `APIConnectionError` | 网络连不上 | 是 |
| `RateLimitError` | 429 限流/超额 | 是（退避后） |
| `InternalServerError` / 5xx `APIStatusError` | 服务端故障 | 是 |
| `BadRequestError` (400) | 参数错误 | 否（重试还是错） |
| `AuthenticationError` (401) | key 错/失效 | 否 |
| `PermissionDeniedError` (403) | 无权限 | 否 |
| `NotFoundError` (404) | 模型/端点不存在 | 否 |

> 用一个 **`@classmethod _get_retryable_exceptions()`** 返回可重试异常类型元组，
> 默认空元组；在方法内部 `from openai import ...` 惰性导入。
> 这样「哪些可重试」是**可被子类覆盖、可声明**的，而不是写死在循环里。

---

## 3. 重试策略

- 参数：`max_retries: int = 3`、`retry_delay: float = 1.0`；
- 循环执行 `max_retries + 1` 次：
  - 成功即返回；
  - 捕获异常：**不是** retryable → 立即 `raise`；是 retryable → 记录、
    `await asyncio.sleep(delay)` 后再来；
  - 次数耗尽 → 抛出**最后一次**的原始异常（保留 traceback）。
- 退避：M5 先用**固定延迟**即可；想加深可做指数退避（`delay * 2**attempt`）
  并加少量随机 jitter 防止"重试风暴"。能说出固定 vs 指数的取舍即可。

> 注意 CancelledError **不参与重试**：它不是业务失败，重试一个被取消的任务是错误的。
> 要单独 except 处理。

---

## 4. 流式重试的边界（重点理解，别踩坑）

流式请求的失败可能发生在两个阶段：

1. **建立连接 / 首片到达之前**：还没向消费方 yield 任何可见内容 → 可以安全重试；
2. **已经产出部分增量之后中途失败**：上层**已经看到/打印了部分输出**，
   此时不能"从头重试"（会重复已发送内容、语义错乱）→ 应直接上抛，
   或在文档明确"不支持中途自动重试"。

> 这是把重试放进模板方法时最容易错的地方。实现时让重试只覆盖
> 「拿到首片可见帧之前」的阶段；一旦开始 yield，就退出重试保护。

---

## 5. 接口契约

### 5.1 `ChatModelBase` 构造增加

```python
def __init__(
    self, config, client, stream=False,
    max_retries: int = 3, retry_delay: float = 1.0,
): ...
```

### 5.2 可重试异常声明

```python
@classmethod
def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
    from openai import (
        APITimeoutError, APIConnectionError, RateLimitError, InternalServerError,
    )
    return (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)
```

### 5.3 `__call__` 收口（非流式）

- 用一个重试循环包住 `_call_api(stream=False)`；
- `except asyncio.CancelledError`：返回 INTERRUPTED 的 ChatResponse（策略 B）；
- `except Exception`：按 `_get_retryable_exceptions()` 判断重试或上抛；
- 成功返回完整 ChatResponse。

可抽一个内部协程 `_with_retry(afn)` 复用，避免非流式/流式各写一遍循环。

### 5.4 `_stream` 改造

- **首片前**的连接/取流阶段用 `_with_retry` 保护；
- 进入 `async for` 正常聚合；
- `except asyncio.CancelledError`：把 `acc.finished_reason = INTERRUPTED`，
  产出/收尾 acc（不抛空、给上层明确中断对象）；
- 已开始产出后的中途异常：直接上抛（见 §4）。

### 5.5 工厂透传

`build_model(spec, stream=False, max_retries=3, retry_delay=1.0)` 透传给构造函数。

---

## 6. 时序：取消与重试分别长什么样

```
取消：
task = create_task(model(msgs)) → task.cancel()
   → __call__/_stream 在 await 点收到 CancelledError
   → 标记 finished_reason=INTERRUPTED，收尾
   → 上层拿到 INTERRUPTED 的 ChatResponse

重试（非流式）：
_call_api 抛 RateLimitError → 命中 retryable → sleep(retry_delay)
   → 再调一次成功 → 返回 ChatResponse
连续失败 max_retries+1 次 → 抛出最后一次 RateLimitError
```

---

## 7. 留给你的动手任务（M5）

1. 构造增加 `max_retries / retry_delay`；实现 `_get_retryable_exceptions`（惰性 import）。
2. 实现 `_with_retry`（或等价循环）：非流式完整重试；区分可重试/不可重试/取消。
3. `_stream`：首片前可重试、中途失败上抛；CancelledError 置 INTERRUPTED 收尾。
4. **取消 demo**：`asyncio.create_task` 发起调用后短延迟 `task.cancel()`，
   打印最终响应的 finished_reason（期望 interrupted）。
5. **重试 demo/单测（不依赖真实限流）**：monkeypatch `_call_api`，
   让它前 2 次抛一个可重试异常、第 3 次成功，断言确实重试并最终成功、记录了尝试次数；
   再测：① 连续失败耗尽后抛出最后异常；② 抛不可重试异常时**立即**抛出、不重试；
   ③ CancelledError 不被当重试。

> 重试/取消几乎都能用"假 _call_api"离线测，不必真等限流——这也让测试快速稳定。

## 8. M5 自检清单

- [ ] 能解释 CancelledError 为何继承 BaseException、为何不能当普通失败重试；
- [ ] 能背出哪些 OpenAI 异常可重试、哪些不可，并说明理由；
- [ ] 非流式可重试错误自动重试成功；耗尽后抛最后异常；不可重试立即抛；
- [ ] 取消时上层拿到 INTERRUPTED 响应，且没有"吞掉"取消后假装完成；
- [ ] 流式只在首片前重试，能说清中途失败为何不能自动重试；
- [ ] 重试/取消逻辑只在基类出现一次，子类与上层不重复。

## 9. agentscope 源码对照

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| 非流式重试循环 | `model/_base.py` `__call__` | for attempt 循环、retryable 判断、sleep、耗尽抛最后错误 |
| CancelledError（非流式） | `__call__` 内 `except asyncio.CancelledError` | 返回 INTERRUPTED 的空 ChatResponse |
| CancelledError（流式） | `__call__` 内 `_stream()` 的 except | acc 置 INTERRUPTED、产出累计结果 |
| 可重试异常声明 | `_get_retryable_exceptions()` | 类方法、惰性 import SDK 异常 |
| 流式聚合收尾 | `_stream()` | 与 M4 聚合逻辑结合，取消时改 finished_reason |

---

## 10. 完成后

把 `_base.py`（及相关工厂）改动连同「取消 demo」「重试单测」的运行结果贴给我 review。
通过后进入 **M6：工具调用闭环（tools 定义、tool_calls 流式拼接、`role=tool` 回灌、
tool_choice）**，那是 Agent 区别于"聊天机器人"的关键一环。
