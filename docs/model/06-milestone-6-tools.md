# 里程碑 6：工具调用闭环（Agent 区别于聊天机器人的关键）

> 目标：让模型能**声明要调工具** → 你**执行** → 把结果以正确角色**回灌** →
> 模型据此继续（可能再调，直到给最终答案）。覆盖非流式与**流式 tool_calls 拼接**、
> `tool_choice`、并行调用。
>
> 边界：**"是否继续循环"的 Agent 主循环属于上层**，本里程碑在 model 层只提供
> 「工具协议 + 执行工具调用并生成回灌消息」的能力，主循环写在你的 demo 里。

---

## 1. 前置知识：Function Calling 协议

### 1.1 声明可用工具（请求里的 `tools`）

```jsonc
"tools": [
  {
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "查询某城市当前天气",
      "parameters": { /* JSON Schema，描述参数 */ }
    }
  }
],
"tool_choice": "auto",            // auto | none | required | 指定函数
"parallel_tool_calls": true
```

- `parameters` 是一段 **JSON Schema**（Pydantic 的 `model_json_schema()` 直接产出）；
- `tool_choice`：`auto`（模型自己决定）、`none`（不调）、`required`（必须调）、
  或 `{"type":"function","function":{"name":"..."}}`（强制调某个）。

### 1.2 模型如何表达"我要调工具"

非流式响应里：

```jsonc
"choices": [{
  "finish_reason": "tool_calls",
  "message": {
    "role": "assistant",
    "content": null,
    "tool_calls": [
      {"id": "call_1", "type": "function",
       "function": {"name": "get_weather", "arguments": "{\"city\":\"Xi'an\"}"}}
    ]
  }
}]
```

要点：
- `arguments` 是**字符串**（不是对象），需 `json.loads`；
- `finish_reason="tool_calls"` 表示这轮是工具请求；
- `tool_calls` 是数组 → 支持一次并行多个。

### 1.3 结果如何回灌（多轮结构）

工具结果必须作为**新的消息**追加，且在 OpenAI 协议里**每个 tool_call 一条
`role="tool"` 消息**：

```jsonc
// 先保留那条 assistant 消息（含 tool_calls），再追加：
{"role":"tool", "tool_call_id":"call_1", "name":"get_weather",
 "content":"{\"temp\":22}"}
```

然后再次发起请求。模型可能：
- 继续请求别的工具（再来一轮 tool_calls）；
- 或信息够了，`finish_reason="stop"` 给最终答案。

> 这就是工具闭环：**assistant(tool_calls) → tool 结果 → assistant(...) 直到 stop。**

---

## 2. 流式 tool_calls 拼接（本阶段重点难点）

流式时，`tool_calls` 也是**分片**到达，且用 **`index` 区分并行的多个调用**——
这正是 M5 之前那个"匿名 id 边界"问题的答案：**tool_call 必须按 index 对齐，不能用匿名 id。**

```jsonc
// 片1：index=0 给 id 和 name
{"choices":[{"index":0,"delta":{"tool_calls":[
   {"index":0,"id":"call_1","type":"function",
    "function":{"name":"get_weather","arguments":""}}]}}]}
// 片2：index=0 的 arguments 增量
{"choices":[{"index":0,"delta":{"tool_calls":[
   {"index":0,"function":{"arguments":"{\"city"}}]}}]}
// 片3：index=0 arguments 继续；同时 index=1 开始第二个并行调用
{"choices":[{"index":0,"delta":{"tool_calls":[
   {"index":0,"function":{"arguments":":\"Xi'an\"}"},
   {"index":1,"id":"call_2","function":{"name":"get_time","arguments":""}}]}}]}
// 末片 finish_reason="tool_calls"
```

累加规则（按 `index`）：
- 维护 `index -> 累加槽`；
- `id` / `name` 通常只在首片出现，收到就存；
- `arguments` 每片做**字符串拼接**；
- 流结束：每个槽产出一个 `ToolCallBlock(id, name, arguments)`，再统一 `json.loads`。

> 对比文本流：文本只有一条匿名块可拼；tool_calls 有 N 条并行，必须靠 index 归位，
> 否则两个工具的参数会互相串。

---

## 3. 接口契约

### 3.1 最小 Tool 协议（在 model 包内，与旧 `hello_agents/tools` 并存）

定义一个轻量工具接口（ABC 或 Protocol），职责：

- `name: str`
- `description: str`
- `parameters` 的 JSON Schema（可用一个 Pydantic 模型 + `model_json_schema()`）；
- `async def run(self, arguments: dict) -> object`：执行，返回可 JSON 序列化结果；
- `function_spec(self) -> dict`：产出 §1.1 的 `{"type":"function","function":{...}}`。

> 执行异常要被捕获并转成 `is_error=True` 的结果，而不是让整个闭环崩掉。

### 3.2 Formatter 扩展（出站）

`to_openai_messages` 增加（替换 M3 的 NotImplementedError）：

- 一条 assistant `Message` 含 `ToolCallBlock`：
  → `{"role":"assistant","content":None,
      "tool_calls":[{"id":tc.id,"type":"function",
      "function":{"name":tc.name,"arguments":tc.arguments}}]}`；
- `ToolResultBlock`（role=tool）：
  → 每个结果一条 `{"role":"tool","tool_call_id":...,"name":...,"content":output}`；
- 同一条 Message 里多个 ToolResultBlock 要**展开成多条** role=tool dict。

### 3.3 Formatter 扩展（入站）

- `from_completion`：`message.tool_calls` 非空 → 每个转 `ToolCallBlock`
  （id/name/arguments 原样），`finish_reason` 记录；
- `parse_chunk`：解析 `delta.tool_calls`，**按 index 产出/累加**，
  产出含 ToolCallBlock 的增量（arguments 为该片分片）。

### 3.4 ChatResponse / 累加扩展

- content 联合类型加入 `ToolCallBlock`；
- 实现 `append_tool_call(...)` 与 `append_chat_response` 对 ToolCallBlock 的合并：
  **按 id（流式内部按 index 映射到 id）对齐，arguments 字符串拼接**；
  新 id 的调用追加为新块；
- 去掉之前对 ToolCallBlock 的 NotImplementedError。

> 设计提示：流式 chunk 用 index、你的 Block 用 id——在累加器里维护
> `index -> id` 映射（首片拿到 id 后建立），后续分片按 index 找到对应块拼 arguments。

### 3.5 执行工具调用并生成回灌消息（model 层能力）

提供一个协程，概念上：

```python
async def execute_tool_calls(
    tool_calls: list[ToolCallBlock], tools: dict[str, Tool]
) -> list[Message]:
    """对每个 ToolCallBlock：json.loads(arguments) → tool.run →
    成功/失败都包成 ToolResultBlock，返回 role=tool 的 Message 列表。"""
```

- arguments 解析失败、未知工具、run 抛错 → 都生成 `is_error=True` 的结果；
- 返回的 Message 可直接拼进消息历史再次调用。

### 3.6 主循环写在 demo（上层，不属于 model）

```python
messages = [Message.system(...), Message.user("西安天气如何？")]
while True:
    response = await model(messages)          # 非流式
    messages.append(response_to_message(response))
    calls = response.get_tool_calls()
    if not calls:                            # finish_reason=stop，拿到最终答案
        break
    messages.extend(await execute_tool_calls(calls, tools))
# 打印最终正文
```

> 流式版本同理：消费流拿到完整 ChatResponse 后再走同样判断。

---

## 4. 时序：一次"调工具→据结果回答"

```
user: 西安天气如何？
assistant: tool_calls=[get_weather(city="Xi'an")]   finish=tool_calls
tool(tool_call_id=call_1): {"temp":22,"desc":"晴"}
assistant: 西安当前约 22°C，晴。                     finish=stop
```

---

## 5. 留给你的动手任务（M6）

1. 实现最小 Tool 协议 + 2 个真实工具（如 get_weather 可用假数据/免费 API、calculator）。
2. Formatter 出站：tool_calls / role=tool 转换（含多结果展开）。
3. Formatter 入站：非流式 tool_calls 解析；流式按 **index** 拼接 arguments。
4. ChatResponse：append_tool_call / append_chat_response 支持工具块（index→id 映射）。
5. 实现 `execute_tool_calls`（错误全部转 is_error，不崩）。
6. demo：实现 §3.6 主循环，跑通"模型调工具→据结果回答"；
   再做一次**并行两个工具**与一次**流式 tool_calls** 的演示。
7. 测试：
   - 纯逻辑：Formatter 的 tool 转换、流式 index 分片拼接（伪造 chunk）；
   - execute_tool_calls：未知工具/坏 arguments/run 抛错都产出 is_error；
   - 用一个"假模型"按脚本返回 tool_calls/stop，离线验证闭环，不依赖真实网络。

## 6. M6 自检清单

- [ ] 能背出 tools / tool_calls / role=tool 三段 JSON 的关键字段；
- [ ] 能解释 arguments 为什么是字符串、在哪个环节 json.loads；
- [ ] 流式并行多个工具时，arguments 按 index 正确归位、互不串；
- [ ] assistant(tool_calls) 与 role=tool 结果在多轮中正确配对（id ↔ tool_call_id）；
- [ ] 工具失败被转成 is_error 回灌，闭环不中断；
- [ ] 能说清"主循环"为什么属于上层、model 层只提供到哪一步；
- [ ] tool_choice 的 auto/required/指定函数能正确改变模型行为。

## 7. agentscope 源码对照

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| Tool 协议 / tools schema | `src/agentscope/tool/`（`ToolBase`、`ToolChoice`） | 工具定义、JSON schema、tool_choice 结构 |
| tool_calls 入站解析 | 各 provider `_model.py` 流式段 | 按 index 累加 id/name/arguments |
| ToolCallBlock 合并 | `model/_model_response.py` `append_tool_call` | 同 id 拼接 arguments、额外字段 |
| role=tool 出站 | `formatter/`（OpenAIChatFormatter） | assistant.tool_calls 与 tool 消息展开 |
| 工具结果回灌 | message 的 ToolResultBlock + Formatter | output、is_error、tool_call_id |

---

## 8. 完成后

把 Tool 协议、`_formatter.py`、`_response.py`、`execute_tool_calls` 与主循环 demo、
相关测试贴给我 review。通过后进入
**M7：结构化输出（工具兜底 + schema 校验/修复 + 原生 json_schema override 对比）**。
