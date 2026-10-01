# M7 结构化输出 —— 设计规格

> 对应需求：`docs/model/07-milestone-7-structured.md`
> 编写时间：2026-10-01
> 状态：**待审核**（审核通过后才开始实施）
> 前置：M0–M6 已合入（注册表 / 调用 / 流式 / 重试取消 / 工具闭环）

---

## 1. 目标与边界

**目标**：让模型的输出能稳定解析成符合给定 schema 的强类型对象，并把
「工具兜底 / 原生 json_schema / json_object」三条路径放在**同一个入口**下横向可比，
最后统一走「客户端 Pydantic 校验 + 错误回灌自动修复」兜底。

**边界**：

- 只做**非流式**结构化输出。流式结构化（边流边解析增量 JSON）是另一个问题，
  本次不碰——传流式模型进来直接报错，不静默走错路径。
- 不改 M6 的 `Tool` / `execute_tool_calls` 语义。工具兜底只是「借用」Tool 协议拿
  schema，`run` 全程不会被调用（`execute_tool_calls` 也不参与）。
- 不引入 `jsonschema` 之类的第三方校验库：最后一道防线是 Pydantic 模型，
  它同时承担「校验」与「构造强类型对象」两件事。
- 不做跨模式自动降级（见决策 3）。
- 不引入真实网络请求作为验收手段；离线测试覆盖全部分支，真实网络只做探测与
  最后一次端到端确认（且需用户同意）。

**已确认的四个决策**（本次澄清结论）：

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | `extract_structured` 的返回形态 | **双入口**：`extract_structured(...) -> T` 保持规格 §6 原样，另加 `extract_structured_with_stats(...) -> (T, StructuredStats)` | 规格 §6 要强类型返回值，§8.3 的 demo 要打印修复轮数与 token；两者都要满足，就不能把统计塞进返回值 |
| 2 | tool 模式的 `tool_choice` | **三家统一 `auto` + 强系统提示**，不用 `required` | M6 e2e 已实测：DeepSeek 思考模式对 `required` 与「强制指定函数」两种形式都回 `400 Thinking mode does not support this tool_choice`；`auto` + 强提示实测稳定触发 |
| 3 | `mode="json_schema"` 但能力位为 `False` | **直接抛错**，不静默降级；端点回 400 也原样上抛 | 静默降级会让「以为走了严格模式、其实退化成弱保证」这种问题潜伏到线上 |
| 4 | 三家原生 strict 支持的实测回填（规格 §8.1） | 本次产出**探测脚本** `examples/model_m7_probe.py`，由你在本地跑并回填注册表 | 探测需要真实 key 与费用；脚本产出可直接粘贴的结论表 |

> 决策 2 的直接推论：`ModelConfig` **不需要**新增 `supports_forced_tool_choice` 能力位。
> M6 e2e 里那条「要不要补一个能力位」的待决问题，在 M7 用「统一不用 required」关掉。

---

## 2. 模块结构

**新增** `hello_agents/model/_structured.py`（预估 ~300 行），一个模块装四件事：

| 关注点 | 内容 |
|---|---|
| 公开入口 | `extract_structured` / `extract_structured_with_stats` |
| 模式适配 | 每种 mode 的「怎么发请求」「怎么从响应里取 payload」「失败怎么回灌」 |
| 修复闭环 | 统一的重试循环、错误精简、上限控制 |
| strict schema | `strict_json_schema()`：把 Pydantic schema 转成 OpenAI strict 可用的形状 |

**改动** `_formatter.py`：新增两个 response_format 构造器。理由与 M6 的
`to_openai_tools` 一致——`_formatter` 是**唯一**知道 OpenAI 格式长什么样的地方，
`response_format` 的 wire 形状属于它的管辖范围。

**不新建** `_schema.py` / `_prompt.py`：这些内容都只服务结构化输出一个入口，
拆成三个文件只会让「改一处要跳三个文件」。真长到失控再拆。

---

## 3. 数据模型

### 3.1 模式与异常

```python
StructuredMode = Literal["tool", "json_schema", "json_object"]

class StructuredOutputError(Exception):
    """修复轮数耗尽仍未得到合法结果。"""

class NativeJsonSchemaUnsupportedError(StructuredOutputError):
    """注册表能力位为 False 时请求 json_schema 模式。"""
```

- 异常放 `_structured.py`，**不进** `hello_agents/core/exceptions.py`：那是旧框架
  （`AgentError` 体系）的异常树，model 包自成一套（先例：`MissingAPIKeyError`
  就在 `_registry.py`）。两套体系混用会让 `except AgentError` 意外接住模型层错误。
- `NativeJsonSchemaUnsupportedError` 继承 `StructuredOutputError`：调用方只关心
  「结构化没成功」时可以用一个 `except` 兜住。
- 修复耗尽时 `raise StructuredOutputError(...) from last_exc`——**保留 `__cause__`**，
  排查时能顺着异常链看到最后一次的 `ValidationError`。

### 3.2 统计对象

```python
class StructuredAttempt(BaseModel):
    index: int                     # 0-based：第几次请求
    error: str | None              # 这一轮失败的精简错误；成功轮为 None
    usage: ChatUsage | None        # 这一轮的用量（厂商没给就是 None）

class StructuredStats(BaseModel):
    mode: StructuredMode
    attempts: list[StructuredAttempt]

    @property
    def rounds(self) -> int:       # 修复轮数
        return max(0, len(self.attempts) - 1)

    @property
    def ok_first_try(self) -> bool:
        return len(self.attempts) == 1

    @property
    def usage(self) -> ChatUsage:  # 各轮合计；缺 usage 的轮按 0 计
        ...                        # input/output/cache_read/time 逐项相加
```

用 pydantic 模型而不是 dataclass，与本包既有数据模型（`ChatUsage` / `ChatResponse`）
一致。`usage` 做成 property 而不是字段：它是纯派生值，存字段就有「两处真相」。

---

## 4. 三种模式

统一入口签名（`mode` / `max_fix_rounds` 做成 keyword-only：`mode="tool"` 这种
位置参数读起来没有意义）：

```python
T = TypeVar("T", bound=BaseModel)

async def extract_structured(
    model: ChatModelBase,
    messages: list[Message],
    output_model: type[T],
    *,
    mode: StructuredMode = "tool",
    max_fix_rounds: int = 2,
) -> T: ...

async def extract_structured_with_stats(
    model: ChatModelBase,
    messages: list[Message],
    output_model: type[T],
    *,
    mode: StructuredMode = "tool",
    max_fix_rounds: int = 2,
) -> tuple[T, StructuredStats]: ...
```

`extract_structured` 是 `extract_structured_with_stats(...)[0]` 的一行包装，
**循环只有一份实现**。

**入口校验**（三条，都在发请求之前）：

| 情况 | 行为 |
|---|---|
| `model.stream` 为 True | `ValueError("extract_structured 只支持非流式模型")` |
| `mode` 不在三个字面量里 | `ValueError`，列出合法取值 |
| `output_model` 不是 `BaseModel` 子类 | `TypeError` |

**不修改调用方的 `messages`**：进入循环前 `base = list(messages)`，之后所有追加都
发生在这份副本上。调用方复用同一个列表跑多种模式（demo 正是这么干的）时，
不能被前一次调用污染。

### 4.1 `mode="tool"`（最通用，复用 M6 的 Tool 协议）

**提交工具**：模块私有类 `_SubmitResultTool(Tool)`，`parameters` 由 `__init__`
注入：

```python
class _SubmitResultTool(Tool):
    name = SUBMIT_TOOL_NAME            # "submit_result"
    description = "提交最终的结构化结果。这是唯一被接受的输出方式。"
    parameters: ClassVar[dict[str, Any]] = {}   # 占位，__init__ 用真实 schema 覆盖

    def __init__(self, schema: dict[str, Any]) -> None:
        self.parameters = schema

    async def run(self, arguments: dict[str, Any]) -> object:
        return arguments     # 永远不会被调用，见下
```

`run` 只是满足 ABC 的最小实现，**`extract_structured` 全程不会调用它**——
结构化输出的取值路径是「解析 `ToolCallBlock.arguments`」，不是「执行工具」。
这一点写进 docstring，免得后来者以为这里有副作用。

**schema 用原样**（`output_model.model_json_schema()`），**不做 strict 化**：
strict 化是 `json_schema` 模式为了满足服务端强校验才需要的；工具参数另开一条
`function.strict` 通道，三家支持情况未实测（决策 4 的探测只覆盖 response_format），
不在这里顺手打开。

**请求**：

```python
attempt_messages = [Message.system(_TOOL_SYSTEM_PROMPT), *base]
response = await model(attempt_messages, tools=[submit_tool], tool_choice="auto")
```

强提示内容（对应决策 2）：

```
你必须调用 submit_result 工具来提交最终结果，不得用正文回答。
把结果放进该工具的参数里，正文留空。
```

提示词按 M6 e2e 的经验写死：**若哪天开始随机失败，正确做法是把提示词写得更死，
而不是把断言放宽**。

系统消息**插在最前面**（不动调用方原有的 system 消息）：不改写调用方内容，
同时保证「必须调工具」这条指令先于其它指令出现。三家兼容端点都接受多条 system。

**取值**：`response.get_tool_calls()` 里**第一个** `name == "submit_result"` 的调用，
`json.loads(call.arguments)` → `output_model.model_validate(...)`。

| 失败情况 | 精简错误文本 |
|---|---|
| 没有 submit_result 调用 | `没有调用 submit_result 工具。必须以工具调用的形式提交结果。` |
| `arguments` 不是合法 JSON | `submit_result 的参数不是合法 JSON：<解析错误>` |
| 解析结果不是 dict | `submit_result 的参数必须是 JSON 对象，收到 <类型>` |
| 校验失败 | 见 §5.2 |

### 4.2 `mode="json_schema"`（严格模式）

**能力位检查**（决策 3）：`model.config.supports_native_json_schema` 为 `False` 时
直接抛 `NativeJsonSchemaUnsupportedError`，**请求都不发**：

```
provider=deepseek 的注册表能力位 supports_native_json_schema=False，
未实测支持原生 strict json_schema。请改用 mode="tool"，或先跑
examples/model_m7_probe.py 实测后回填注册表。
```

**请求**：`response_format=to_response_format_json_schema(name, strict_json_schema(output_model))`。
不加系统提示（约束由服务端承担）。

**取值**：正文（所有 `TextBlock` 拼接）→ JSON → 校验。

| 失败情况 | 精简错误文本 |
|---|---|
| 响应里没有正文 | `响应里没有正文，无法解析结构化结果。` |
| 正文不是合法 JSON | 见 §5.3 |

**端点回 400**（能力位说是 True 但实际不支持）：`BadRequestError` 不在
`_with_retry` 的可重试集合里，会**原样上抛**，不包装成 `StructuredOutputError`——
这是配置错误，不是「模型没修好」，包装反而误导。上抛时错误里已含端点原文。

### 4.3 `mode="json_object"`（弱模式）

**请求**：`response_format=to_response_format_json_object()`，并把 schema
**内联进系统提示**（服务端只保证「是合法 JSON」，字段约束必须靠提示词）：

```
只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。JSON 必须符合下面的 JSON Schema：
<output_model.model_json_schema() 的 JSON 文本>
```

内联用**原样 schema**（不是 strict 化后的）：提示词是给人/模型看的说明，
保留 `default` 与「哪些字段可选」的信息更有用，而 strict 形状把一切都变成必填，
反而误导。

**取值**：正文 → §5.3 的容错解析 → 校验。

### 4.4 三模式对照

| | tool | json_schema | json_object |
|---|---|---|---|
| 服务端保证 | 工具参数符合 schema（`auto` 下不强制） | 严格符合 schema（strict） | 只是合法 JSON |
| 依赖 | function calling（三家都有） | 该模型支持原生 strict | 无 |
| 系统提示注入 | 必须调工具的强提示 | 无 | 内联 schema |
| 取值来源 | `ToolCallBlock.arguments` | 正文 | 正文 |
| 客户端兜底 | 统一的校验 + 修复闭环 | 同左 | 同左 |

---

## 5. 客户端校验 + 自动修复闭环

### 5.1 循环

```
attempts = []
current = base                    # 副本，含模式专属的前置消息
for round in 0..max_fix_rounds:   # 含首次，共 max_fix_rounds + 1 次请求
    response = await model(current, **mode_kwargs)
    payload, error = 按模式取值(response)
    if payload is not None:
        try:
            value = output_model.model_validate(payload)
        except ValidationError as exc:
            error = _validation_error_text(exc, response)
        else:
            attempts.append(成功轮); return value, stats
    attempts.append(失败轮(error))
    if round == max_fix_rounds: break
    current = current + _repair_messages(response, error, mode)
raise StructuredOutputError(...) from last_exc
```

**轮数语义**（写死在规格里，避免「0 是一轮还是两轮」的歧义）：
`max_fix_rounds=N` → 最多发出 **N+1** 次请求（1 次原始 + N 次修复）。
`max_fix_rounds=0` → 只发一次，失败即抛。

`max_fix_rounds` 为负数时按 0 处理（与 `_with_retry` 对负数 `max_retries` 的处理一致）。

### 5.2 精简错误文本

回灌的是**精简错误**，不是 traceback，也不是 `str(ValidationError)`（后者会带
`input` 字段，把模型自己刚输出的一长串原文再喂回去，白烧 token）：

```
上次提交未通过校验，请修正后重新提交：
- age: 字段缺失 [missing]
- city: 输入应为字符串，实际为整数 [string_type]
只提交修正后的完整结果，不要解释。
```

- 每条取 `loc`（点号连接，空则 `(根)`）、`msg`、`type`；
- **不含** `input` / `ctx` / `url`；
- 最多列 `_MAX_ERROR_ITEMS = 10` 条，超出补 `…（其余 N 条略）`；
- 总长截断到 `_MAX_ERROR_CHARS = 1200`，超出补 `…（已截断）`。

`loc` 用点号连接即可：字段名里有 `.` 的模型极罕见，为它引一套转义规则不值得。

### 5.3 JSON 容错解析

`_parse_json_payload(text) -> Any`（只服务两个正文模式）：

1. `json.loads(text.strip())`；
2. 失败则尝试剥掉 Markdown 代码围栏（` ```json ... ``` `）后再 `json.loads`；
3. 再失败则抛错，交给修复闭环。

容错解析不会削弱保证——真正的门是后面的 `model_validate`；这里只是省掉一轮
「模型手滑加了围栏」的修复成本。返回值标成 `Any` 而不是 `dict`：解析出来不是
dict 时**不在这里拦**，统一交给 `model_validate` 报错（一个报错出口比两个好）。

### 5.4 失败回灌（按模式）

**tool 模式**：必须按 OpenAI 的配对规则回灌，否则请求本身不合法。

```python
results = []
for call in response.get_tool_calls():        # 这一轮的**全部**调用
    if call.name == SUBMIT_TOOL_NAME:
        results.append(ToolResultBlock(tool_call_id=call.id, output=error,
                                       is_error=True, name=call.name))
    else:
        results.append(ToolResultBlock(tool_call_id=call.id,
                                       output=f"未知工具 {call.name!r}", is_error=True,
                                       name=call.name))
repair = [response.to_message(), Message(role=Role.TOOL, content=results)]
```

三点必须写清楚：

1. **回灌全部调用**。assistant 消息里每个 `tool_call` 都必须有一条对应的 tool 结果，
   漏一条端点就报错。模型幻觉调了别的工具时，给它一条「未知工具」结果即可
   ——这正是 `execute_tool_calls` 的兜底思路，只是这里不执行任何工具。
2. **没有调用时走另一条路**：`response.get_tool_calls()` 为空说明模型直接答了正文，
   此时没有 `tool_call_id` 可配对，回灌改成
   `[response.to_message(), Message.user(error)]`。
3. **`response.to_message()` 带上模型这一轮的原始输出**（含它写错的 arguments），
   这是模型自我修正的主要依据。

**json_schema / json_object 模式**：

```python
repair = [response.to_message(), Message.user(error)]
```

响应里**没有正文块**时（`content` 为空）跳过 assistant 消息，只发 `Message.user(error)`
——空 content 的 assistant 消息在部分端点上是非法请求。

**原始需求消息保留**：`current` 是在 `base` 上追加，从不重建。修复轮里模型
始终看得到最初的任务与（tool / json_object 模式的）schema 提示，上下文不会越修越偏。

---

## 6. strict schema 转换

`strict_json_schema(output_model) -> dict`：`deepcopy(model_json_schema())` 后递归
改写。这是规格 §3 点名的坑（Pydantic 默认形状不满足 strict 要求）。

对**每个含 `properties` 的节点**：

```python
node["additionalProperties"] = False
node["required"] = list(node["properties"].keys())    # 覆盖原值
```

递归进入 `properties` 的每个值、`$defs` / `definitions` 的每个值、`items`
（dict 或 list）、`anyOf` / `oneOf` / `allOf` 的每个分支。最后删掉 `title` 与
`default` 两个键。

- **删 `title`**：Pydantic 到处生成它，纯噪音；
- **删 `default`**：字段已进 `required`，「必填且有默认值」自相矛盾，部分严格校验器
  直接拒绝；
- **保留 `$defs` / `$ref`**：标准 JSON Schema，OpenAI strict 支持；不做内联展开
  （内联要处理递归引用，代价远大于收益）。嵌套模型的 `$defs` 也会被递归改写。
- **`extra="allow"` 的模型**：`additionalProperties` 被强制成 `False`，这是 strict
  模式的硬要求——需要额外字段的模型不能走这个模式。

**语义影响必须写进 docstring**：strict 模式下没有「可省略」——带默认值的字段也会
进 `required`；想让某字段可以不填，用 `X | None = None`（结果是 required 但允许
`null`）。

**`json_schema.name`**：由 `output_model.__name__` 清洗而来——非
`[A-Za-z0-9_-]` 的字符换成 `_`，截断到 64 字符，清洗后为空则用 `"output"`。
用类名而不是常量，是为了在厂商后台的日志里能一眼看出是哪个模型。

---

## 7. 模型层接口改动

### 7.1 `response_format` 透传

`_call_api` / `__call__` / `_stream` 三处签名各加一个参数，与 M6 的 `tools` 完全同构：

```python
response_format: dict | None = None
```

- `_base.__call__`：非流式与流式两条路径都把 `response_format` 交给 `_call_api`
  （流式虽不用于结构化输出，但签名一致性不能破——规格 §6 明确要求「`__call__` /
  `_stream` / 抽象签名一致透传」）；
- `_base._stream`：同上；
- `_openai_compat._call_api`：并入现有的 `extra` 字典，**`None` 时不写这个 key**
  （与 `tools` / `tool_choice` 同一套规则：不传就用端点默认值，传空 dict 是另一种语义）。

### 7.2 `FinishedReason.LENGTH`（承接 M6 的遗留项）

M6 规格 §8 把 `finish_reason="length"` 明确留给 M7：「M7（结构化输出）关心截断，
届时一并处理」。本次收口：

```python
class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"          # 新增
```

`_map_finish_reason`：`"length"` → `LENGTH`，其余映射不变。

**用途**：截断的响应在 JSON 模式下表现为「JSON 解析失败」，这个错误文本会误导模型
（它以为是自己格式写错了，其实是被砍了）。修复轮里带上前缀：

```
上一轮输出因长度上限被截断（finish_reason=length），请输出更精简的完整 JSON。
```

只加这一处消费点。累加器不用改：`LENGTH` 是非默认值，`append_chat_response`
的「只吸收非默认值」规则天然覆盖它。

---

## 8. Demo 与探测脚本

### 8.1 `examples/model_m7_demo.py`（需真实网络，实施时单独征求同意）

**同一个目标 schema**（`Person{name, age, city}`）+ **同一段输入文本**，依次跑三种
模式，打印对照表：

```
mode          first_try  rounds  in_tok  out_tok  result
tool          yes        0       412     38       Person(name='老张', age=30, city='西安')
json_schema   yes        0       380     31       ...
json_object   no         1       702     55       ...
```

- 用 `extract_structured_with_stats` 取统计（决策 1 的直接用途）；
- `json_schema` 一列按能力位决定跑不跑：`False` 时打印
  `skipped (supports_native_json_schema=False)`，不制造一次注定失败的请求；
- 第二段**制造一次修复**：schema 用 `field_validator` 表达约束（如「11 位纯数字」），
  给一段自然写成 `138-1234-5678` 的文本，打印每一轮的 `error` 文本，观察回灌后模型补全。
  **约束写在 validator 而不是 `Field(pattern=...)` 是刻意的**：前者不出现在 JSON Schema
  里，模型看不到，第一次才会真的写错——这正是「客户端 Pydantic 校验是最后一道防线」
  的现场演示。若写成 `pattern`，模型在 schema 里就看得到，第一次多半直接写对，
  反而演示不到修复闭环。
- 输出全部用 ASCII 标记（控制台 GBK，`✓`/`✗` 会 `UnicodeEncodeError`）。

> 说明写进 demo 的 docstring：**真实模型会不会真的先错一次不可控**。要稳定看到修复
> 过程请跑离线测试 `tests/test_model_structured.py`；demo 的价值是「可观测性」，
> 不是「保证失败」。

### 8.2 `examples/model_m7_probe.py`（你在本地跑，回填注册表）

对三家各发一次请求，打印可直接粘贴的结论：

1. `response_format={"type":"json_object"}` 是否被接受；
2. `response_format={"type":"json_schema",...,"strict":True}` 是否被接受；
3. 被拒绝时原样打印端点错误（规格 §6：「端点通常报 400 response_format
   json_schema not supported 之类」）。

**判定必须分三态，不能只看「有没有报错」**（实测踩到的坑，见下）：

| 状态 | 含义 | 能力位 |
|---|---|---|
| `rejected` | 端点报错（如 400 response_format unavailable） | False |
| `ignored` | 端点**接受**参数但**不遵守**，返回的还是散文 | **False** |
| `honored` | 返回合法 JSON | True |

第一版按「没报错就算支持」判定，把 zhipu 判成了 True——而实测 glm-5.2 的
`json_schema` 请求不报错、静默忽略参数、返回散文「这句话里的人物是：**老张**。」
**接受但忽略比直接拒绝更坏**：调用方以为走了严格模式，实际拿到的是自由文本。
能力位的语义是「strict 是否真的约束了输出」，所以这种情况必须是 `False`。

**两个 prompt 是刻意的**：`json_schema` 用**不提 JSON** 的 prompt，约束才只能来自
服务端参数——若 prompt 里写了「以 JSON 返回」，模型照做，就分不清是服务端约束生效
还是提示词在起作用。`json_object` 反过来必须提 JSON：协议要求 prompt 里出现这个词
（否则三家都回 400 `must contain the word 'json'`），那是参数本身的前提。

纪律：

- **一家失败不影响下一家**：逐家 `try/except Exception`，把异常类型与消息打出来；
- 缺少对应环境变量的 provider 直接跳过并打印 `skipped (no DEEPSEEK_API_KEY)`，
  不中断；
- 最后打印一段「建议注册表值」的表格，形如
  `DASHSCOPE  supports_native_json_schema=False`，直接照抄回 `_registry.py`；
- 每家的真实响应只打印**形状**（是否合法 JSON、字段名列表），不打印全文——
  探测脚本的目的是能力位，不是看模型说什么。

**回填动作**：拿到你的实测输出后，我按事实改 `_registry.py` 的三处能力位，
并在注释里记下探测日期。

### 8.3 `tests/test_model_e2e.py` 追加（默认跳过）

沿用该文件既有的 `e2e` 标记与 `RUN_MODEL_E2E=1` 开关，追加：

- tool 模式：`extract_structured(...)` 返回的 `Person` 字段非空，`stats.ok_first_try` 为真；
- json_object 模式：同上；
- json_schema 模式：能力位为 `False` 时 `pytest.skip`，为 `True` 时才真发请求。

只断言契约（类型正确、字段齐、`attempts >= 1`），**不断言模型措辞**——沿用该文件
开头写下的两条纪律。

---

## 9. 测试（离线，`tests/test_model_structured.py`）

手写假模型，不用 `unittest.mock`；中文 docstring、英文用例名。

| 分组 | 用例要点 |
|---|---|
| strict schema | `additionalProperties=False`；`required` 覆盖全部属性（含带默认值的）；嵌套 `$defs` 里的对象同样被改写；`title` / `default` 被删；`X \| None` 字段进 required；`enum` / `items` 不受损 |
| response_format 构造 | 两个构造器的**精确 dict 形状**（`strict` 为 True、`name` 清洗与截断、超长/非法类名回退 `output`） |
| tool 模式 | 一次通过；`arguments` 坏 JSON → 修复；参数非 dict → 修复；模型没调工具 → 走 assistant+user 回灌；模型调了别的工具 → 回灌「未知工具」且不炸 |
| json_object 模式 | 正文 JSON 一次通过；带 Markdown 代码围栏也能解析；坏 JSON → 修复 |
| json_schema 模式 | 能力位 `False` → 抛 `NativeJsonSchemaUnsupportedError` **且没有发出请求**（假模型记录调用次数为 0）；能力位 `True` → 假模型收到的 `response_format` 形状正确 |
| 修复闭环 | 连续失败到上限 → `StructuredOutputError`，`attempts == max_fix_rounds + 1`，`__cause__` 是最后一次的 `ValidationError`；`max_fix_rounds=0` 只发一次；负数按 0 |
| 回灌内容 | 第二轮消息里 assistant 消息带原始 tool_calls、tool 结果的 `tool_call_id` 与调用配对、`is_error=True`；错误文本**不含**模型原文（`input`）；超长被截断 |
| 上下文 | 修复轮仍包含最初的用户消息与 schema 提示 |
| 统计 | `rounds` / `ok_first_try` / usage 逐项合计正确；缺 usage 的轮按 0 计 |
| 入口校验 | 流式模型 → `ValueError`；非法 `mode` → `ValueError`；非 BaseModel 的 `output_model` → `TypeError` |
| 副作用 | 调用方传入的 `messages` 列表**长度不变**（没有被就地追加） |
| `FinishedReason.LENGTH` | `_map_finish_reason("length")` 映射正确；截断响应的修复错误文本含「截断」 |
| 透传 | 假模型断言 `_call_api` 收到的 `response_format` 与模式一致；不传时为 `None` |

**必须同步修改的既有测试**（签名变更的连带改动，不是顺手重构）：
`tests/test_model_retry.py` 的三处与 `tests/test_model_tools.py:376` 的假模型
`_call_api(self, messages, stream, tools=None, tool_choice=None)` 要加
`response_format=None`，否则基类透传 kwargs 时会 `TypeError`。

---

## 10. 有意不做（不是遗漏）

| 项 | 理由 |
|---|---|
| 流式结构化输出 | 增量 JSON 解析是独立课题；本次直接拒绝流式模型，不静默降级 |
| `function.strict`（工具参数严格模式） | 三家支持情况未实测；探测脚本只覆盖 response_format，不顺手打开 |
| 跨模式自动降级 | 决策 3 的反面：静默降级会让保证强度不可见 |
| `jsonschema` 库二次校验 | Pydantic 模型已是最后一道防线，再加一层是仪式 |
| 修复时给模型换更便宜的模型 / 调温度 | 没有证据表明必要；先有数据再优化 |
| `extract_structured` 支持多结果 / 列表抽取 | 目标 schema 本身就是 `list[Person]`，用 `output_model` 表达即可，不需要新参数 |
| 注册表能力位的自动更新 | 探测结果由人回填，代码不自己改自己（M8 的 YAML 卡片再谈配置与代码分离） |

---

## 11. 影响面清单

**新增**
- `hello_agents/model/_structured.py` —— 入口 / 三模式 / 修复闭环 / strict schema
- `tests/test_model_structured.py`
- `examples/model_m7_demo.py`、`examples/model_m7_probe.py`

**改动**
- `hello_agents/model/_base.py` —— `__call__` / `_stream` / `_call_api` 加
  `response_format` 透传
- `hello_agents/model/providers/_openai_compat.py` —— `extra` 并入 `response_format`
- `hello_agents/model/_formatter.py` —— `to_response_format_json_object` /
  `to_response_format_json_schema`；`_map_finish_reason` 增加 `length`
- `hello_agents/model/_response.py` —— `FinishedReason.LENGTH`
- `hello_agents/model/__init__.py` —— 导出（见下）
- `hello_agents/model/_registry.py` —— 探测后按事实回填三处
  `supports_native_json_schema`
- `tests/test_model_retry.py`、`tests/test_model_tools.py` —— 假模型签名补参数
- `tests/test_model_e2e.py` —— 追加三个 e2e 用例

**新增导出**（`hello_agents/model/__init__.py`）：
`StructuredMode`、`StructuredAttempt`、`StructuredStats`、`StructuredOutputError`、
`NativeJsonSchemaUnsupportedError`、`extract_structured`、
`extract_structured_with_stats`、`SUBMIT_TOOL_NAME`。

**不动**
- `hello_agents/core/exceptions.py`（旧异常树）、`hello_agents/tools/`（旧工具包）、
  `hello_agents/model/_tool.py`、`_response.py` 的累加逻辑，以及与 M7 无关的
  未提交改动（`.gitignore` / `uv.lock` / `.claude/` / `docs/plans/`）。

---

## 12. 验收标准

1. `tests/test_model_structured.py` 全绿；`test_model_retry.py` / `test_model_tools.py`
   补签名后仍全绿；
2. 全量 `pytest tests/ -q` 不新增失败（既有 `test_embedding.py` 那条与本工作线无关）；
3. `ruff check` / `ruff format --check` 在本工作线涉及文件上干净；
4. `examples/model_m7_demo.py` 三模式可横向对比（成功率 / 修复轮数 / token）；
5. 探测脚本输出已回填注册表，且注释里记了探测日期；
6. 规格 §9 自检清单逐条可答：三模式保证强度与依赖、实测结论、横向对比、
   Pydantic 是最后防线、修复闭环有上限且回灌精简、`response_format` 与 `tools`
   同构透传。

---

## 13. 与 agentscope 的对照（规格 §10）

| 本实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| `extract_structured` / `_structured.py` | `src/agentscope/structured/` | `create_structured_model` 的入口与「输出模型约定」；我们不做动态生成模型类，直接用调用方给的 Pydantic 类 |
| `strict_json_schema` | provider `_model.py` 里的 response_format 构造 | strict 形状（`additionalProperties` / `required`）与 `$defs` 处理 |
| tool 模式 | `tool/` + formatter | 与 M6 同一套 tool_calls 机制，但**不执行**工具 |
| 修复闭环 | structured 模块的错误回灌循环 | `ValidationError` 文本化、保留原始需求、重试上限 |
