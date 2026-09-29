# 里程碑 4：流式聚合（重点难点）

> 目标：把非流式闭环升级为**流式**——`stream=True` 后用 `async for` 逐块读取 SSE，
> 把**正文增量**与**思考链增量（reasoning_content）**分别累加，吸收末尾的 usage，
> 最终既「边到边打印」又能在结束时拿到一个完整 `ChatResponse`。
>
> 本阶段聚焦**文本 + thinking** 的流式聚合；**tool_call 的流式拼接放 M6**
> （但累加机制完全一致，M4 打好基础）。重试/取消在 M5。

---

## 1. 前置知识：SSE 与异步流

### 1.1 什么是 SSE

`stream=True` 时，服务器不是攒好一个 JSON 再返回，而是持续推送一系列
**Server-Sent Events**。OpenAI 兼容协议下，每个事件是一个小 JSON（SDK 已帮你解析成
`ChatCompletionChunk`），形如：

```jsonc
// 第1片：可能给 id、模型
{"id":"...","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
// 第2片：正文增量
{"choices":[{"index":0,"delta":{"content":"Token"},"finish_reason":null}]}
// 第3片：思考增量（字段名各家可能不同）
{"choices":[{"index":0,"delta":{"reasoning_content":"首先…"},"finish_reason":null}]}
// …更多增量…
// 最后一片：finish_reason 非空
{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
// 若开启 include_usage：末尾还有一片「只有 usage、没有 choices」的 carrier
{"choices":[],"usage":{"prompt_tokens":15,"completion_tokens":1813}}
```

要点：
- 增量在 `choices[].delta`，**每片只含新增的一点点**，必须自己累加；
- `finish_reason` 只有最后一片非空；
- usage 默认流式不返回，需要显式 `stream_options={"include_usage": True}`，
  且它出现在一片 `choices=[]` 的「载体片」里——**不能把这片当空响应吐给用户**，
  要吸收其 usage 后跳过。

### 1.2 `async for` 与异步生成器

异步可迭代对象用 `async for` 遍历（只能在 `async def` 内）：

```python
stream = await client.chat.completions.create(..., stream=True)  # 注意：拿到的是流
async for chunk in stream:
    ...
```

> 注意：`create(stream=True)` 用 await 拿到的是一个 **AsyncStream**（不是最终结果），
> 真正的内容在随后的 `async for` 里逐片到来。

一个「异步生成器函数」长这样（`async def` 里出现 `yield`）：

```python
async def gen():
    yield 1
    yield 2
# 消费：async for x in gen(): ...
```

M4 你会写一个异步生成器，逐片产出增量，同时内部维护一个累加结果。

### 1.3 本阶段心智模型

```
stream=True
   │ async for chunk in stream
   ▼
ChatCompletionChunk  --Formatter.parse_chunk-->  ChatResponse(增量,is_last=False)
   │                                              含 TextBlock / ThinkingBlock
   ▼
基类 __call__ 的累加器：acc.append_chat_response(delta)
   ├─ 每来一片：yield delta（上层边打印）
   └─ 结束：acc 即完整 ChatResponse（is_last=True，含 usage）
```

**职责切分（关键设计）**：
- `Formatter.parse_chunk`：只负责「原始 chunk → 增量 ChatResponse」（认识协议）；
- `ChatResponse.append_chat_response`：只负责「增量怎么并进累计结果」（纯逻辑，可单测）；
- `ChatModelBase.__call__`：负责驱动流、调用上面两者、产出逐片增量与最终完整结果
  （模板方法，聚合在此收口）。

---

## 2. 接口契约

### 2.1 Formatter 新增：`parse_chunk(chunk) -> ChatResponse`

把一个原始 `ChatCompletionChunk` 转成**增量** `ChatResponse`（`is_last=False`）：

- 取 `choices[0].delta`：
  - `delta.content` 非空 → 一个 `TextBlock(text=delta.content)`；
  - `delta.reasoning_content` 非空 → 一个 `ThinkingBlock(thinking=...)`
    （字段名以你实测三家为准，可能是 `reasoning_content`；用 getattr 读取，没有就 None）；
  - 两者可能同片出现，也可能只有其一；
- 携带 `chunk.id`；
- 若该片带 `usage`（carrier 片）→ 构造 `ChatUsage` 放进增量，供累加器吸收；
- `choices` 为空（usage carrier）→ 返回一个 content 为空、只带 usage 的增量（或返回 None
  由调用方跳过，二选一，但要能把 usage 传出去）。

> 不要在 parse_chunk 里做累加——它只翻译单帧。累加是 ChatResponse 的事。

### 2.2 ChatResponse 新增累加方法

参考 agentscope，实现按「块 id」合并：

- `append_text(text, block_id=None)`：有同 id 的 TextBlock 就 `block.text += text`，
  否则新建一个 TextBlock 追加；
- `append_thinking(thinking, block_id=None)`：同理拼到 ThinkingBlock；
- `append_chat_response(delta: ChatResponse) -> ChatResponse`：
  - 遍历 delta.content：累计结果里**已有同 id 同类型块**就拼接文本/思考；
    **没有**就把该块（深拷贝）追加；
  - delta.usage 非空 → 覆盖 `self.usage`；
  - delta.id 非空 → 更新 `self.id`。

> 为什么按 id 合并：一条流里同一个文本块/思考块的增量，需要稳定地拼到同一个块上，
> 而不是每片都新建一个块。M6 的 tool_call 还会用 `index`/id 来对齐并行的多个调用。

### 2.3 `_call_api` 支持流式

`OpenAICompatModel._call_api` 增加一个 `stream` 维度（或读取 self.stream）：

```python
async def _call_api(self, messages, stream: bool):
    openai_msgs = to_openai_messages(messages)
    if not stream:
        completion = await self.client.chat.completions.create(
            model=..., messages=openai_msgs, stream=False)
        return from_completion(completion, elapsed)

    raw_stream = await self.client.chat.completions.create(
        model=..., messages=openai_msgs, stream=True,
        stream_options={"include_usage": True},
    )

    async def gen():
        async for chunk in raw_stream:
            delta = parse_chunk(chunk)
            if delta is not None:
                yield delta

    return gen()   # AsyncGenerator[ChatResponse]
```

> 即：非流式返回完整 ChatResponse；流式返回「增量 ChatResponse 的异步生成器」。

### 2.4 `ChatModelBase.__call__` 改造（聚合收口）

让 `__call__` 同时支持两种形态（是否流式由构造参数或调用参数决定，二选一保持一致）：

- **非流式**：`return await self._call_api(messages, stream=False)`；
- **流式**：返回一个异步生成器，内部：

```python
acc = ChatResponse(content=[], is_last=True)
async for delta in await self._call_api(messages, stream=True):
    acc.append_chat_response(delta)
    # carrier（content 空、只带 usage）不向用户产出可见空帧
    if delta.content:
        yield delta
# 流结束：yield 一个完整结果（is_last=True），或把 acc 作为最终值——约定统一即可
acc.finished_reason = FinishedReason.COMPLETED
yield acc
```

> 设计取舍点（想清楚再定，没有唯一答案）：
> ① 逐片 yield 的是「增量 delta」还是「当前累计快照 acc」？上层打印方式不同
>    （增量直接 print(end='')；快照要 diff）。推荐 yield 增量、最后再 yield 完整 acc。
> ② 最后一片用 `is_last=True` 标记完整响应，让消费方能区分「过程增量」与「最终完整」。

### 2.5 构造参数

`ChatModelBase` / `OpenAICompatModel` 增加 `stream: bool`（默认 True 或 False 自定，
建议显式传入，避免隐式）。`build_model(spec, stream=True)` 透传。

---

## 3. 一次流式调用的数据流

```
model = build_model("dashscope:qwen3.7-plus", stream=True)
stream = model([Message.user("一句话解释 token")])   # __call__ 返回异步生成器
async for part in stream:
    if not part.is_last:
        # ThinkingBlock 与 TextBlock 分别处理，边到边显示
        for b in part.content: ...print 思考/正文...
    else:
        final = part        # 完整 ChatResponse：拼接好的思考+正文+usage
```

> 这能直接验证 M3 的疑点：qwen3.7 那 1813 个 output token 里，
> 你会看到大段 ThinkingBlock（被单独累加），TextBlock 才是给用户的短答案。

---

## 4. 留给你的动手任务（M4）

1. Formatter 实现 `parse_chunk`（content / reasoning_content / usage carrier 都覆盖）。
2. ChatResponse 实现 `append_text`、`append_thinking`、`append_chat_response`。
3. `_call_api` 增加流式分支（含 `stream_options include_usage`），返回增量异步生成器。
4. `__call__` 实现流式聚合：逐片 yield 增量、跳过空 carrier、结束 yield 完整 acc。
5. 写流式 demo（你自己的）：对三家分别流式请求，**思考用一种样式、正文用另一种样式**
   边到边打印；流结束后打印完整 usage（对比 output_tokens 与可见正文长度）。
6. 故意不发 include_usage 跑一次，观察 usage 缺失时你的兜底；再打开，确认 carrier 被正确吸收。

> 测试由你写：`append_chat_response` 是纯逻辑、最适合单测——构造若干增量
> ChatResponse（含同 id 拼接、新块追加、usage 覆盖），断言累加结果；
> `parse_chunk` 可用 SimpleNamespace 伪造 chunk 离线测，全部不依赖网络。

## 5. M4 自检清单

- [ ] 能脱稿画出 SSE 一帧帧的结构，指出 delta / finish_reason / usage carrier 位置；
- [ ] 流式时正文与思考分别累加、边到边显示，互不串台；
- [ ] 结束拿到的完整 ChatResponse 文本 == 所有增量正确拼接；
- [ ] usage carrier（空 choices）被吸收 usage 且不产生可见空帧；
- [ ] 三家都能流式跑通；能解释 qwen 输出 token 为何远多于可见正文；
- [ ] 非流式路径不被破坏（同一模型两种模式都正确）；
- [ ] 聚合逻辑在基类、协议解析在 Formatter，子类不重复实现累加。

## 6. agentscope 源码对照

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| `__call__` 流式包装 | `model/_base.py` `__call__` 内 `_stream()` | 累加 acc、跳过空 carrier、末尾产出完整响应 |
| `append_chat_response` | `model/_model_response.py` 同名方法 | 同 id 拼接、新块深拷贝追加、usage 覆盖 |
| `append_text/thinking` | `_model_response.py` | 按 block id 累加、provider 额外字段处理 |
| `parse_chunk` | 各 provider `_model.py` 的流式解析段 | delta.content / reasoning(thinking) / usage 提取 |
| carrier 处理 | `_base.py` `_stream` 注释（OpenAI 尾片 usage-only） | 吸收元数据、不向上抛空帧 |

---

## 7. 完成后

把 `_formatter.py / _response.py / _base.py / providers/_openai_compat.py` 的改动，
连同一次**流式**运行（思考与正文分样式打印 + 最终 usage）贴给我 review。
通过后进入 **M5：取消/中断 + 失败重试（CancelledError 收口、可重试异常、退避）**。
