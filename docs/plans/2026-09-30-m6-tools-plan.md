# M6 工具调用闭环 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型能声明要调工具 → 上层执行 → 结果以正确角色回灌 → 模型据此继续；覆盖非流式与流式 `tool_calls` 拼接、`tool_choice`、并行调用。

**Architecture:** 沿用 M0–M5 的分工——`_formatter` 是唯一接触 OpenAI dict 的地方（出站 `to_openai_messages` / `to_openai_tools`，入站 `from_completion` / `parse_chunk`），`ChatResponse` 兼流式累加器，`ChatModelBase` 用模板方法收口重试与取消。M6 新增一个 `_tool.py` 承载 Tool 协议与工具执行，工具随**调用**传参而非随构造绑定。

**Tech Stack:** Python 3.13、pydantic v2、openai SDK 3.16.2、pytest + pytest-asyncio（`asyncio_mode = "auto"`）、ruff、uv。

**Spec:** `docs/specs/2026-09-30-m6-tools-design.md`

## Global Constraints

- 解释器一律用 `./.venv/Scripts/python.exe`——`python3` 在 PATH 上是 Windows Store 桩，会静默以退出码 49 退出。`jq` 也不存在。
- 控制台是 GBK：demo 与测试输出只用 ASCII 标记（`✓`/`✗` 会 `UnicodeEncodeError`）；demo 里 `logging.basicConfig(stream=sys.stdout)`。
- 中文 docstring，英文测试用例名。
- 测试手写 fake/stub，**不用 `unittest.mock`**。入站 Formatter 的测试直接用真实 SDK 类型（`ChatCompletionChunk` / `ChoiceDeltaToolCall` 等）构造，避免自造 fake 与协议漂移。
- 不改 `hello_agents/tools/`（旧工具包），不改 `hello_agents/core/message.py`。
- **提交粒度**：本工作线既有约定是每个里程碑一个内聚提交（M0–M4 一个、M5 一个）。因此各任务**不单独提交**，只在 Task 7 收口时汇总成一个提交。每个任务以「跑通该任务的测试」作为完成标志。
- 提交时**只 add 本工作线涉及的文件**，不带 `.gitignore` / `uv.lock` / `.claude/` / `docs/plans/context-plan-progress.md` / `hello_agents/core/message.py` 这些无关改动。
- 真实网络请求必须先取得用户同意（三家 API 都产生费用）。
- 保持代码简洁可扩展易修改；遇到仪式性代码先讲代价再定。

## Review Focus

规格没写、但真跑起来最可能咬人的五处，各自的测试落在括号里的任务中：

1. **`arguments` 是空字符串**——`json.loads("")` 抛 `JSONDecodeError`；期望产出 `is_error=True` 的回灌结果，闭环继续，而不是整个循环崩掉（Task 5）。
2. **一条 assistant 消息同时有正文与 tool_calls**——期望正文保留在 `content`，不被 `None` 覆盖掉（Task 1）。
3. **流式并行两路 tool_calls 的分片交错到达**——期望各自按 `index` 归位、arguments 互不串（Task 4）。
4. **工具返回不可 JSON 序列化的对象**——期望转成 `is_error=True`，而不是 `TypeError` 冒到闭环外（Task 5）。
5. **`tools` 未传 / 传空列表**——期望请求里根本不出现 `tools` 这个 key，而不是发一个空数组给端点（Task 3）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `hello_agents/model/_tool.py` | Tool 协议、`ToolChoice`、`execute_tool_calls` | 新建 |
| `hello_agents/model/message.py` | `ToolCallBlock.index`、`ToolResultBlock.name` | 改 |
| `hello_agents/model/_formatter.py` | 出站/入站的工具转换、`to_openai_tools`、`_map_finish_reason` | 改 |
| `hello_agents/model/_response.py` | 联合类型、`TOOL_CALLS`、累加器、`get_tool_calls`/`to_message` | 改 |
| `hello_agents/model/_base.py` | `__call__` / `_call_api` / `_stream` 签名与收尾语义 | 改 |
| `hello_agents/model/providers/_openai_compat.py` | 透传 `tools` / `tool_choice` | 改 |
| `hello_agents/model/__init__.py` | 导出 | 改 |
| `tests/test_model_tools.py` | M6 全部离线用例 | 新建 |
| `tests/test_model_retry.py` | 三个 fake 的 `_call_api` 签名 | 改 |
| `examples/model_m6_demo.py` | 三段端到端 demo | 新建 |

---

### Task 1: Tool 协议 + 出站 Formatter

工具消息能正确地发出去。

**Files:**
- Create: `hello_agents/model/_tool.py`
- Modify: `hello_agents/model/message.py:25-36`
- Modify: `hello_agents/model/_formatter.py:28-55`
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: `Message` / `Role` / `TextBlock` / `ThinkingBlock` / `ToolCallBlock` / `ToolResultBlock`（`message.py`，已存在）
- Produces:
  - `Tool`（ABC）：类属性 `name: str`、`description: str`、`parameters: dict[str, Any]`；方法 `function_spec() -> dict`（具体实现）、`async run(arguments: dict[str, Any]) -> object`（抽象）
  - `ToolChoice = Literal["auto", "none", "required"] | dict[str, Any]`
  - `to_openai_tools(tools: Sequence[Tool]) -> list[dict]`
  - `ToolCallBlock.index: int | None`、`ToolResultBlock.name: str | None`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_model_tools.py`：

```python
"""M6 工具调用闭环：出站/入站转换、流式拼接、执行回灌。全部离线，不发网络请求"""

import json
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from hello_agents.model import (
    ChatModelBase,
    ChatResponse,
    FinishedReason,
    Message,
    Provider,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    get_model_config,
)
from hello_agents.model._formatter import to_openai_messages, to_openai_tools
from hello_agents.model._tool import Tool


class _EchoTool(Tool):
    """最小工具：把收到的参数原样回显。"""

    name = "echo"
    description = "回显参数"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, arguments: dict[str, Any]) -> object:
        return arguments


# --- 出站 Formatter ---


def test_function_spec_shape():
    """function_spec 产出规格 §1.1 的形状，子类不用自己拼。"""
    assert _EchoTool().function_spec() == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "回显参数",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        },
    }


def test_to_openai_tools_maps_each_tool():
    assert to_openai_tools([_EchoTool()]) == [_EchoTool().function_spec()]


def test_assistant_tool_calls_become_tool_calls_field():
    msg = Message(
        role=Role.ASSISTANT,
        content=[ToolCallBlock(id="call_1", name="get_weather", arguments='{"city":"Xian"}')],
    )

    assert to_openai_messages([msg]) == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Xian"}'},
                }
            ],
        }
    ]


def test_assistant_text_alongside_tool_calls_is_kept():
    """模型可能「先说一句再调工具」，正文不能丢（Review Focus 2）。"""
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="我先查一下"),
            ToolCallBlock(id="call_1", name="get_weather", arguments="{}"),
        ],
    )

    out = to_openai_messages([msg])[0]

    assert out["content"] == "我先查一下"
    assert out["tool_calls"][0]["id"] == "call_1"


def test_assistant_thinking_is_still_ignored():
    msg = Message(
        role=Role.ASSISTANT,
        content=[
            ThinkingBlock(thinking="想想"),
            ToolCallBlock(id="c", name="t", arguments="{}"),
        ],
    )

    out = to_openai_messages([msg])[0]

    assert out["content"] is None
    assert len(out["tool_calls"]) == 1


def test_tool_message_expands_one_dict_per_result():
    """同一条 Message 里多个结果要展开成多条 role=tool dict（规格 §3.2）。"""
    msg = Message(
        role=Role.TOOL,
        content=[
            ToolResultBlock(tool_call_id="call_1", output="22", name="get_weather"),
            ToolResultBlock(
                tool_call_id="call_2", output="boom", is_error=True, name="get_time"
            ),
        ],
    )

    assert to_openai_messages([msg]) == [
        {"role": "tool", "tool_call_id": "call_1", "content": "22", "name": "get_weather"},
        {"role": "tool", "tool_call_id": "call_2", "content": "boom", "name": "get_time"},
    ]


def test_tool_message_omits_name_when_absent():
    """name 为 None 时不写这个 key，避免给严格校验的端点发 null。"""
    msg = Message(role=Role.TOOL, content=[ToolResultBlock(tool_call_id="c", output="ok")])

    assert to_openai_messages([msg]) == [
        {"role": "tool", "tool_call_id": "c", "content": "ok"}
    ]


def test_tool_call_in_user_message_raises():
    msg = Message(role=Role.USER, content=[ToolCallBlock(id="c", name="t", arguments="{}")])

    with pytest.raises(ValueError, match="role=assistant"):
        to_openai_messages([msg])


def test_tool_result_in_assistant_message_raises():
    msg = Message(role=Role.ASSISTANT, content=[ToolResultBlock(tool_call_id="c", output="ok")])

    with pytest.raises(ValueError, match="role=tool"):
        to_openai_messages([msg])


def test_tool_role_message_without_result_raises():
    msg = Message(role=Role.TOOL, content=[TextBlock(text="不是结果")])

    with pytest.raises(ValueError, match="至少要有一个"):
        to_openai_messages([msg])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q
```
Expected: collection error —— `ImportError: cannot import name '_tool'`。

- [ ] **Step 3: 建 `_tool.py`**

```python
"""model 包内的最小工具协议。

与旧包 `hello_agents/tools/`（`BaseTool`：同步 `run` + `arun`）并存、互不依赖。
那套面向「工具怎么被 Agent 调用」，这套面向「工具怎么被模型调用」——只需要一份
JSON Schema 和一个异步入口。

典型用法：
    class GetWeather(Tool):
        name = "get_weather"
        description = "查询某城市当前天气"
        parameters = GetWeatherParams.model_json_schema()

        async def run(self, arguments: dict) -> object:
            return {"temp": 22}
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

# auto（模型自己决定）/ none（不调）/ required（必须调），
# 或 {"type":"function","function":{"name":"..."}}（强制调某个）
ToolChoice = Literal["auto", "none", "required"] | dict[str, Any]


class Tool(ABC):
    """一个可被模型调用的工具。

    子类声明 `name` / `description` / `parameters` 三个类属性并实现 `run`；
    `function_spec()` 由基类给具体实现，子类不用重复写。

    `parameters` 是**现成的 JSON Schema dict**，不做「传 Pydantic 模型自动推导」
    那层魔法——两套机制并存只会让人猜哪套生效。配方写在这里：
    `parameters = MyParams.model_json_schema()`。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]

    def function_spec(self) -> dict:
        """产出 `chat.completions.create(tools=...)` 数组里的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> object:
        """执行工具。返回可 JSON 序列化的结果；返回 `str` 时原样使用。

        抛出的异常由 `execute_tool_calls` 捕获并转成 is_error 结果，不会中断闭环。
        """
```

- [ ] **Step 4: 改 `message.py` 的两个块**

把 `ToolCallBlock` 换成：

```python
class ToolCallBlock(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: str
    id: str
    index: int | None = None  # 流式对齐用（同一次响应里的第几个调用）；非流式为 None
```

把 `ToolResultBlock` 换成：

```python
class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    output: str
    is_error: bool = False
    name: str | None = None  # 出站 role=tool dict 的 name 字段
```

- [ ] **Step 5: 改 `_formatter.py` 的出站**

import 区加上 `from collections.abc import Sequence` 和 `from ._tool import Tool`。

把 `to_openai_messages` 整个替换为：

```python
def to_openai_messages(messages: list[Message]) -> list[dict]:
    """把统一消息模型转成 `chat.completions.create(messages=...)` 要的列表。

    - 普通消息：`content` 为其中所有 `TextBlock.text` 按原顺序拼接的结果；
    - assistant 含 `ToolCallBlock`：转成 `content` + `tool_calls` 两个字段，
      `content` 取 `text or None`（模型可能「先说一句再调工具」，正文不能丢）；
    - role=tool 含 `ToolResultBlock`：**每个结果展开一条** dict；
    - `ThinkingBlock` 一律忽略——思考链是否回传各家规则不同，不能混进 `content`。

    结构约束违反时**显式报错而不是静默丢弃**：工具消息一旦被悄悄吞掉，模型会收到
    一段缺了上下文的对话，而错误要等到很远的地方才暴露。
    """
    out: list[dict] = []
    for msg in messages:
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        tool_calls = [b for b in msg.content if isinstance(b, ToolCallBlock)]
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]

        if tool_results:
            if msg.role is not Role.TOOL:
                raise ValueError(
                    f"ToolResultBlock 只能出现在 role=tool 的消息里"
                    f"（收到 role={msg.role.value}）"
                )
            if len(tool_results) != len(msg.content):
                raise ValueError(
                    f"role=tool 的消息只能包含 ToolResultBlock"
                    f"（混入了 {type(next(b for b in msg.content if b not in tool_results)).__name__}）"
                )
            out.extend(_tool_result_dict(b) for b in tool_results)
            continue

        if msg.role is Role.TOOL:
            raise ValueError(
                "role=tool 的消息至少要有一个 ToolResultBlock，否则它没有任何可回灌的内容"
            )

        if tool_calls:
            if msg.role is not Role.ASSISTANT:
                raise ValueError(
                    f"ToolCallBlock 只能出现在 role=assistant 的消息里"
                    f"（收到 role={msg.role.value}）"
                )
            out.append(
                {
                    "role": msg.role.value,
                    "content": text or None,
                    "tool_calls": [_tool_call_dict(tc) for tc in tool_calls],
                }
            )
            continue

        out.append({"role": msg.role.value, "content": text})
    return out


def to_openai_tools(tools: Sequence[Tool]) -> list[dict]:
    """产出 `chat.completions.create(tools=...)` 要的列表。"""
    return [t.function_spec() for t in tools]


def _tool_call_dict(tool_call: ToolCallBlock) -> dict:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {"name": tool_call.name, "arguments": tool_call.arguments},
    }


def _tool_result_dict(block: ToolResultBlock) -> dict:
    out = {
        "role": Role.TOOL.value,
        "tool_call_id": block.tool_call_id,
        "content": block.output,
    }
    if block.name:
        out["name"] = block.name
    return out
```

- [ ] **Step 6: 跑测试确认通过**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q
./.venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: 新文件 10 passed；全量不新增失败（`test_embedding.py` 那条既有失败与本工作线无关）。

- [ ] **Step 7: lint**

```bash
uv run ruff check hello_agents/model tests/test_model_tools.py
uv run ruff format --check hello_agents/model tests/test_model_tools.py
```

---

### Task 2: 入站 Formatter + `finish_reason` 映射

模型说要调工具时，我们能接住。

**Files:**
- Modify: `hello_agents/model/_response.py:15-18, 30`
- Modify: `hello_agents/model/_formatter.py`（`from_completion`、`parse_chunk`）
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `ToolCallBlock.index`
- Produces:
  - `FinishedReason.TOOL_CALLS = "tool_calls"`
  - `ChatResponse.content: list[TextBlock | ThinkingBlock | ToolCallBlock]`
  - `_formatter._map_finish_reason(raw: object) -> FinishedReason`

- [ ] **Step 1: 写失败测试**

在 `tests/test_model_tools.py` 的 import 区补上 SDK 类型与 `parse_chunk`：

```python
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion import Choice as CompletionChoice
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from hello_agents.model._formatter import (
    from_completion,
    parse_chunk,
    to_openai_messages,
    to_openai_tools,
)
```

再追加这一节（放在文件末尾）：

```python
# --- 入站 Formatter ---


def _delta_tool_call(
    index: int, *, id: str | None = None, name: str | None = None, arguments: str = ""
) -> ChoiceDeltaToolCall:
    """流式的一小片 tool_call。续片只有 index + arguments，没有 id/name。"""
    return ChoiceDeltaToolCall(
        index=index,
        id=id,
        type="function",
        function=ChoiceDeltaToolCallFunction(name=name, arguments=arguments),
    )


def _tool_chunk(
    tool_calls: list[ChoiceDeltaToolCall], *, finish_reason: str | None = None
) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chunk_1",
        choices=[
            ChunkChoice(
                index=0, delta=ChoiceDelta(tool_calls=tool_calls), finish_reason=finish_reason
            )
        ],
        created=1,
        model="m",
        object="chat.completion.chunk",
    )


def _text_chunk(text: str, *, finish_reason: str | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="chunk_t",
        choices=[
            ChunkChoice(
                index=0, delta=ChoiceDelta(content=text), finish_reason=finish_reason
            )
        ],
        created=1,
        model="m",
        object="chat.completion.chunk",
    )


def _carrier_chunk() -> ChatCompletionChunk:
    """usage 载体帧：choices 为空、只带 usage。dashscope 在末片**之后**发它。"""
    return ChatCompletionChunk(
        id="chunk_u",
        choices=[],
        created=1,
        model="m",
        object="chat.completion.chunk",
        usage=CompletionUsage(completion_tokens=9, prompt_tokens=7, total_tokens=16),
    )


def test_from_completion_reads_tool_calls_and_finish_reason():
    tool_call = ChatCompletionMessageToolCall(
        id="call_1",
        type="function",
        function=Function(name="get_weather", arguments='{"city":"Xian"}'),
    )
    completion = ChatCompletion(
        id="x",
        choices=[
            CompletionChoice(
                index=0,
                finish_reason="tool_calls",
                message=ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
            )
        ],
        created=1,
        model="m",
        object="chat.completion",
    )

    response = from_completion(completion, 0.1)

    assert response.finished_reason is FinishedReason.TOOL_CALLS
    assert len(response.content) == 1
    block = response.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name, block.arguments) == (
        "call_1",
        "get_weather",
        '{"city":"Xian"}',
    )
    assert block.index is None  # 非流式没有 index（实测 SDK 类型里就没这个字段）


def test_from_completion_stop_maps_to_completed():
    completion = ChatCompletion(
        id="x",
        choices=[
            CompletionChoice(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(role="assistant", content="你好"),
            )
        ],
        created=1,
        model="m",
        object="chat.completion",
    )

    assert from_completion(completion, 0.1).finished_reason is FinishedReason.COMPLETED


def test_parse_chunk_reads_first_fragment():
    delta = parse_chunk(
        _tool_chunk([_delta_tool_call(0, id="call_1", name="get_weather")])
    )

    block = delta.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name, block.arguments, block.index) == (
        "call_1",
        "get_weather",
        "",
        0,
    )


def test_parse_chunk_continuation_fragment_has_empty_id_and_name():
    """续片没有 id/name —— 用空串（匿名），靠 index 归位。"""
    delta = parse_chunk(_tool_chunk([_delta_tool_call(0, arguments='{"city')]))

    block = delta.content[0]
    assert isinstance(block, ToolCallBlock)
    assert (block.id, block.name) == ("", "")
    assert (block.arguments, block.index) == ('{"city', 0)


def test_parse_chunk_maps_finish_reason():
    delta = parse_chunk(_tool_chunk([], finish_reason="tool_calls"))

    assert delta.finished_reason is FinishedReason.TOOL_CALLS
    assert delta.content == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q -k "from_completion or parse_chunk"
```
Expected: FAIL —— `AttributeError: 'ChoiceDeltaToolCall' object has no attribute 'index'` 之类；`finished_reason` 断言拿到 `COMPLETED`。

- [ ] **Step 3: 改 `_response.py` 的枚举与联合类型**

```python
class FinishedReason(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"  # M5 才会真正用到
    TOOL_CALLS = "tool_calls"
```

```python
    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = Field(default_factory=list)
```

import 区：`from .message import TextBlock, ThinkingBlock, ToolCallBlock`。

- [ ] **Step 4: 改 `_formatter.py` 的入站**

新增：

```python
def _map_finish_reason(raw: object) -> FinishedReason:
    """`finish_reason` 字符串 → `FinishedReason`。

    只认 `tool_calls`；`stop` / `length` / `None` / 未知值都落到 COMPLETED。
    `length` 的截断语义留到 M7 处理（见设计规格 §8）。
    """
    return FinishedReason.TOOL_CALLS if raw == "tool_calls" else FinishedReason.COMPLETED
```

`from_completion` 的 choices 分支换成：

```python
    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = []
    finished = FinishedReason.COMPLETED
    if choices:
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if text:  # None 与 "" 都不产出空块
            content.append(TextBlock(text=text))
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            content.append(
                ToolCallBlock(
                    id=getattr(tool_call, "id", None) or "",
                    name=getattr(function, "name", None) or "",
                    arguments=getattr(function, "arguments", None) or "",
                )
            )
        finished = _map_finish_reason(getattr(choices[0], "finish_reason", None))
```

并把返回处的 `finished_reason=FinishedReason.COMPLETED` 换成 `finished_reason=finished`。

`parse_chunk` 的 choices 分支换成：

```python
    content: list[TextBlock | ThinkingBlock | ToolCallBlock] = []
    finished = FinishedReason.COMPLETED
    if choices:
        delta = getattr(choices[0], "delta", None)
        thinking = getattr(delta, "reasoning_content", None)
        text = getattr(delta, "content", None)
        # 同一片里思考排在正文前：推理模型总是先想后答，按时间序放前面，
        # 累加时块的先后就自然还原了「先思考、后答案」。
        if thinking:
            content.append(ThinkingBlock(thinking=thinking, id=""))
        if text:
            content.append(TextBlock(text=text, id=""))
        # tool_calls 排在正文之后：实测三家不会在同一帧里混发两者，
        # 这个顺序只是给「万一混发」定个确定行为。
        for tool_call in getattr(delta, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            content.append(
                ToolCallBlock(
                    # 续片没有 id/name，用空串（匿名）——与文本块同一个理由：
                    # 随机 id 会让每片都建新块。区别是并行多路不能靠空 id 合并，
                    # 必须靠 index 归位。
                    id=getattr(tool_call, "id", None) or "",
                    name=getattr(function, "name", None) or "",
                    arguments=getattr(function, "arguments", None) or "",
                    index=getattr(tool_call, "index", None),
                )
            )
        finished = _map_finish_reason(getattr(choices[0], "finish_reason", None))
```

`parse_chunk` 的返回处把 `is_last=False` 那一行的上方补上 `finished_reason=finished,`。

- [ ] **Step 5: 跑测试确认通过**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q
./.venv/Scripts/python.exe -m pytest tests/test_model_retry.py -q
```
Expected: 全绿。

---

### Task 3: 模型层接口透传 `tools` / `tool_choice`

工具能一路走到 HTTP 请求。

**Files:**
- Modify: `hello_agents/model/_base.py:58-91`
- Modify: `hello_agents/model/providers/_openai_compat.py:12-45`
- Modify: `tests/test_model_retry.py`（三个 fake 的 `_call_api` 签名）
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `Tool` / `ToolChoice` / `to_openai_tools`
- Produces:
  - `ChatModelBase.__call__(messages, tools=None, tool_choice=None)`
  - `ChatModelBase._call_api(messages, stream, tools=None, tool_choice=None)`（抽象签名）
  - `ChatModelBase._stream(messages, tools=None, tool_choice=None)`

- [ ] **Step 1: 写失败测试**

先给 `tests/test_model_tools.py` 加两个共用 fake 与一个 helper（后面 Task 4/6 复用）：

```python
from collections.abc import AsyncGenerator, Sequence

from openai.types.chat import ChatCompletion

from hello_agents.model._tool import Tool, ToolChoice


async def _stream_of(deltas: Sequence[ChatResponse]) -> AsyncGenerator[ChatResponse]:
    """把一串增量帧包成异步生成器。"""
    for delta in deltas:
        yield delta


class _ScriptedModel(ChatModelBase):
    """按脚本逐个返回响应的假模型，并记录每次 `_call_api` 收到的参数。

    脚本元素：`ChatResponse`（非流式）或 `list[ChatResponse]`（流式的增量序列）。
    """

    def __init__(self, script, **kwargs):
        super().__init__(
            config=get_model_config(Provider.DEEPSEEK),
            client=cast(AsyncOpenAI, None),
            **kwargs,
        )
        self.script = list(script)
        self.seen: list[dict] = []

    async def _call_api(self, messages, stream, tools=None, tool_choice=None):
        self.seen.append(
            {"messages": messages, "stream": stream, "tools": tools, "tool_choice": tool_choice}
        )
        item = self.script.pop(0) if self.script else ChatResponse(content=[])
        if stream:
            return _stream_of(item if isinstance(item, list) else [item])
        return item


class _FakeCompletions:
    """记录 create() 收到的 kwargs，返回一个空 completion。"""

    def __init__(self):
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return ChatCompletion(id="x", choices=[], created=1, model="m", object="chat.completion")


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)
```

再加这一节：

```python
# --- 模型层接口透传 ---


@pytest.mark.asyncio
async def test_tools_and_tool_choice_reach_call_api():
    """tools / tool_choice 透传到 _call_api，基类不做解释。"""
    model = _ScriptedModel([])
    tools = [_EchoTool()]

    await model([Message.user("hi")], tools=tools, tool_choice="required")

    assert model.seen[0]["tools"] == tools
    assert model.seen[0]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_tools_default_to_none():
    model = _ScriptedModel([])

    await model([Message.user("hi")])

    assert model.seen[0]["tools"] is None
    assert model.seen[0]["tool_choice"] is None


@pytest.mark.asyncio
async def test_streaming_forwards_tools_too():
    model = _ScriptedModel([[]], stream=True)

    async for _ in await model([Message.user("hi")], tools=[_EchoTool()], tool_choice="auto"):
        pass

    assert model.seen[0]["stream"] is True
    assert model.seen[0]["tools"] is not None


@pytest.mark.asyncio
async def test_openai_compat_omits_tools_key_when_not_given():
    """未传 tools 时请求里根本不出现这个 key（Review Focus 5）。"""
    completions = _FakeCompletions()
    model = OpenAICompatModel(
        get_model_config(Provider.DEEPSEEK), cast(AsyncOpenAI, _FakeClient(completions))
    )

    await model([Message.user("hi")])

    assert "tools" not in completions.kwargs[0]
    assert "tool_choice" not in completions.kwargs[0]


@pytest.mark.asyncio
async def test_openai_compat_omits_tools_key_for_empty_list():
    """空列表同样不发——发一个空数组给端点是另一种语义。"""
    completions = _FakeCompletions()
    model = OpenAICompatModel(
        get_model_config(Provider.DEEPSEEK), cast(AsyncOpenAI, _FakeClient(completions))
    )

    await model([Message.user("hi")], tools=[])

    assert "tools" not in completions.kwargs[0]


@pytest.mark.asyncio
async def test_openai_compat_sends_tools_when_given():
    completions = _FakeCompletions()
    model = OpenAICompatModel(
        get_model_config(Provider.DEEPSEEK), cast(AsyncOpenAI, _FakeClient(completions))
    )

    await model([Message.user("hi")], tools=[_EchoTool()], tool_choice="required")

    assert completions.kwargs[0]["tools"] == [_EchoTool().function_spec()]
    assert completions.kwargs[0]["tool_choice"] == "required"
```

import 区补上 `from hello_agents.model.providers import OpenAICompatModel`。

- [ ] **Step 2: 跑测试确认失败**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q -k "tools or call_api"
```
Expected: FAIL —— `TypeError: __call__() got an unexpected keyword argument 'tools'`。

- [ ] **Step 3: 改 `_base.py` 的签名**

import 区加 `from collections.abc import Sequence` 与 `from ._tool import Tool, ToolChoice`。

`__call__` 的签名与内部调用换成：

```python
    async def __call__(
        self,
        messages: list[Message],
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
```

docstring 补一段：

```
        `tools` / `tool_choice` 只做透传：工具协议的解释在 Formatter 与上层，
        重试、取消、聚合这些收口逻辑与「这次带不带工具」无关。
```

非流式分支里的 `lambda` 换成：

```python
                return await self._with_retry(
                    lambda: self._call_api(
                        messages=messages,
                        stream=False,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                )
```

最后一行 `return self._stream(messages)` 换成 `return self._stream(messages, tools, tool_choice)`。

`_stream` 的签名换成：

```python
    async def _stream(
        self,
        messages: list[Message],
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator[ChatResponse]:
```

里面的 `lambda: self._call_api(messages=messages, stream=True)` 换成：

```python
            raw: AsyncGenerator[ChatResponse] = await self._with_retry(
                lambda: self._call_api(
                    messages=messages, stream=True, tools=tools, tool_choice=tool_choice
                )
            )
```

`_call_api` 的抽象签名换成：

```python
    @abstractmethod
    async def _call_api(
        self,
        messages: list[Message],
        stream: bool,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]: ...
```

- [ ] **Step 4: 改 `_openai_compat.py`**

import 区加 `from collections.abc import Sequence`、`from .._formatter import to_openai_tools`、`from .._tool import Tool, ToolChoice`。

`_call_api` 换成：

```python
    async def _call_api(
        self,
        messages: list[Message],
        stream: bool,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        """三家共用：同一份请求代码，只靠 `self.config` 区分。

        非流式返回完整响应；流式返回「增量响应」的异步生成器。
        """
        openai_msgs = to_openai_messages(messages)
        # 未传就不写这两个 key，让端点用自己的默认值——发一个空的 tools 数组
        # 是另一种语义（有些端点会因此拒绝请求）。
        extra: dict = {}
        if tools:
            extra["tools"] = to_openai_tools(tools)
        if tool_choice is not None:
            extra["tool_choice"] = tool_choice

        if not stream:
            t0 = time.perf_counter()
            completion = await self.client.chat.completions.create(
                model=self.config.model,
                messages=openai_msgs,
                stream=False,
                **extra,
            )
            return ChatResponse.from_completion(completion, time.perf_counter() - t0)

        # stream=True 时 create() 返回的是流本身，内容在随后的 async for 里逐片到来。
        # include_usage 必须显式开：流式默认不返回 usage，末片就没有那个载体帧。
        raw_stream = await self.client.chat.completions.create(
            model=self.config.model,
            messages=openai_msgs,
            stream=True,
            stream_options={"include_usage": True},
            **extra,
        )

        async def gen() -> AsyncGenerator[ChatResponse]:
            # parse_chunk 恒返回增量——载体帧也返回，只是 content 为空——
            # 所以这里不用判 None；空帧由基类的 `if delta.content` 挡掉。
            async for chunk in raw_stream:
                yield parse_chunk(chunk)

        return gen()
```

- [ ] **Step 5: 修既有 fake 的签名**

`tests/test_model_retry.py` 里三处 `async def _call_api(self, messages, stream):` 全部改成：

```python
    async def _call_api(self, messages, stream, tools=None, tool_choice=None):
```

（`_FakeModel`、`_BlockingModel`、`_StreamingFake` 各一处；`_BlockingModel` 的覆盖版本也要改，否则它盖掉父类后签名不兼容。）

- [ ] **Step 6: 跑测试确认通过**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py tests/test_model_retry.py -q
```
Expected: 全绿。

---

### Task 4: 累加器按 `index` 拼接并行 tool_calls

**Files:**
- Modify: `hello_agents/model/_response.py:37-83`
- Modify: `hello_agents/model/_base.py`（`_stream` 收尾一行）
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: Task 2 的 `ToolCallBlock.index` 与 `FinishedReason.TOOL_CALLS`
- Produces: `ChatResponse.append_tool_call(block)`、`ChatResponse.get_tool_calls()`、`ChatResponse.to_message()`

- [ ] **Step 1: 写失败测试**

```python
# --- 累加器 ---


def test_parallel_stream_fragments_are_aligned_by_index():
    """规格 §2 那个多片例子：并行两路 arguments 不能互相串（Review Focus 3）。"""
    acc = ChatResponse(content=[], is_last=True)

    # 片1：index=0 给出 id 与 name
    acc.append_chat_response(parse_chunk(_tool_chunk([_delta_tool_call(0, id="call_1", name="get_weather")])))
    # 片2：index=0 的 arguments 增量
    acc.append_chat_response(parse_chunk(_tool_chunk([_delta_tool_call(0, arguments='{"city')])))
    # 片3：index=0 继续，同时 index=1 开始第二个并行调用
    acc.append_chat_response(
        parse_chunk(
            _tool_chunk(
                [
                    _delta_tool_call(0, arguments='":"Xian"}'),
                    _delta_tool_call(1, id="call_2", name="get_time"),
                ]
            )
        )
    )
    # 片4：index=1 的 arguments
    acc.append_chat_response(parse_chunk(_tool_chunk([_delta_tool_call(1, arguments='{"tz":"CST"}')])))
    # 末片：finish_reason
    acc.append_chat_response(parse_chunk(_tool_chunk([], finish_reason="tool_calls")))

    calls = acc.get_tool_calls()

    assert [c.id for c in calls] == ["call_1", "call_2"]
    assert [c.name for c in calls] == ["get_weather", "get_time"]
    assert [c.arguments for c in calls] == ['{"city":"Xian"}', '{"tz":"CST"}']
    assert acc.finished_reason is FinishedReason.TOOL_CALLS


def test_usage_carrier_after_finish_reason_does_not_reset_it():
    """载体帧在末片**之后**到达（dashscope），不能把 TOOL_CALLS 冲回 COMPLETED。"""
    acc = ChatResponse(content=[], is_last=True)

    acc.append_chat_response(parse_chunk(_tool_chunk([], finish_reason="tool_calls")))
    acc.append_chat_response(parse_chunk(_carrier_chunk()))

    assert acc.finished_reason is FinishedReason.TOOL_CALLS
    assert acc.usage is not None


def test_tool_calls_without_index_merge_by_id():
    """非流式路径没有 index，按 id 合并。"""
    acc = ChatResponse(content=[], is_last=True)

    acc.append_chat_response(
        ChatResponse(content=[ToolCallBlock(id="c1", name="t", arguments='{"a"')], is_last=False)
    )
    acc.append_chat_response(
        ChatResponse(content=[ToolCallBlock(id="c1", name="", arguments=":1}")], is_last=False)
    )

    calls = acc.get_tool_calls()
    assert len(calls) == 1
    assert calls[0].arguments == '{"a":1}'
    assert calls[0].name == "t"  # 后片的空 name 不能把已有的名字冲掉


def test_tool_result_block_in_a_response_still_raises():
    """工具结果出现在响应方向是编程错误，不是「M6 待实现」。"""
    acc = ChatResponse(content=[], is_last=True)

    with pytest.raises(NotImplementedError, match="ToolResultBlock"):
        acc.append_chat_response(
            ChatResponse(
                content=[ToolResultBlock(tool_call_id="c", output="ok")], is_last=False
            )
        )


def test_to_message_keeps_blocks_and_role():
    response = ChatResponse(
        content=[
            TextBlock(text="我先查一下"),
            ToolCallBlock(id="call_1", name="get_weather", arguments="{}"),
        ]
    )

    msg = response.to_message()

    assert msg.role is Role.ASSISTANT
    assert [type(b).__name__ for b in msg.content] == ["TextBlock", "ToolCallBlock"]


@pytest.mark.asyncio
async def test_stream_final_reason_is_tool_calls():
    """收尾对象不能把末片写进去的 TOOL_CALLS 冲回 COMPLETED。"""
    model = _ScriptedModel([[_tool_chunk_delta(finish_reason="tool_calls")]], stream=True)

    parts = [p async for p in await model([Message.user("hi")])]

    assert parts[-1].is_last is True
    assert parts[-1].finished_reason is FinishedReason.TOOL_CALLS


@pytest.mark.asyncio
async def test_stream_final_reason_stays_completed_without_finish_frame():
    """没有 finish 帧时仍是 COMPLETED（守住 M5 的既有行为）。"""
    model = _ScriptedModel([[parse_chunk(_text_chunk("你"))]], stream=True)

    parts = [p async for p in await model([Message.user("hi")])]

    assert parts[-1].finished_reason is FinishedReason.COMPLETED
```

并在 helper 区补一个便捷函数：

```python
def _tool_chunk_delta(*, finish_reason: str | None = None) -> ChatResponse:
    """一个 content 为空、只带 finish_reason 的增量（末片）。"""
    return parse_chunk(_tool_chunk([], finish_reason=finish_reason))
```

- [ ] **Step 2: 跑测试确认失败**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q -k "accumulator or aligned or carrier or to_message or final_reason"
```
Expected: FAIL —— `NotImplementedError: 累加 ToolCallBlock 属于 M6`。

- [ ] **Step 3: 改 `_response.py` 的累加器**

import 区换成 `from .message import Message, Role, TextBlock, ThinkingBlock, ToolCallBlock`。

在 `_find_block` 之后加：

```python
    def _find_tool_call(self, block: ToolCallBlock) -> ToolCallBlock | None:
        """找同一个调用的累加块。

        流式分片带 `index`（同一次响应里的第几个调用），非流式没有 index——
        前者按 index 归位（并行多路靠它才不会串），后者退回按 id 匹配。
        """
        for existing in self.content:
            if not isinstance(existing, ToolCallBlock):
                continue
            if block.index is not None:
                if existing.index == block.index:
                    return existing
            elif block.id and existing.id == block.id:
                return existing
        return None
```

在 `append_thinking` 之后加：

```python
    def append_tool_call(self, block: ToolCallBlock) -> None:
        """把一片 tool_call 增量并进累计结果。

        arguments 在流式里是分片到达的，每一片单独看都是非法 JSON——所以这里只做
        字符串拼接，`json.loads` 留给 `execute_tool_calls`（那里每个调用的参数才完整）。
        """
        existing = self._find_tool_call(block)
        if existing is None:
            self.content.append(block.model_copy())
            return
        # 首片给了 name / id 之后就不再改：续片这两个字段是空串。
        if block.name and not existing.name:
            existing.name = block.name
        if block.id and not existing.id:
            existing.id = block.id
        existing.arguments += block.arguments
```

`append_chat_response` 换成：

```python
    def append_chat_response(self, delta: "ChatResponse") -> "ChatResponse":
        for block in delta.content:
            if isinstance(block, TextBlock):
                self.append_text(block.text, block.id)
            elif isinstance(block, ThinkingBlock):
                self.append_thinking(block.thinking, block.id)
            elif isinstance(block, ToolCallBlock):
                self.append_tool_call(block)
            else:
                raise NotImplementedError(
                    f"响应方向不会出现 {type(block).__name__}："
                    f"工具结果是出站回灌的内容，不该出现在模型的回复里"
                )

        if delta.usage is not None:
            self.usage = delta.usage
        if delta.id:
            self.id = delta.id
        if delta.finished_reason is not FinishedReason.COMPLETED:
            # COMPLETED 是默认值，等价于「这一帧没说」。载体帧（choices=[]）恒为
            # 默认值，而无条件吸收会把末片刚写进去的 TOOL_CALLS 又冲回去——
            # dashscope 的载体帧正是在末片**之后**到达。
            self.finished_reason = delta.finished_reason
        return self
```

再在类里加：

```python
    def get_tool_calls(self) -> list[ToolCallBlock]:
        return [b for b in self.content if isinstance(b, ToolCallBlock)]

    def to_message(self) -> Message:
        """响应 → 可回灌进消息历史的 assistant 消息。

        角色恒为 assistant（响应只可能来自模型）。`ThinkingBlock` 一并带上，
        由 Formatter 出站时忽略。
        """
        return Message(role=Role.ASSISTANT, content=list(self.content))
```

- [ ] **Step 4: 改 `_base.py` 的 `_stream` 收尾**

把 `reason = FinishedReason.COMPLETED` 这一行删掉，`except` 块里的 `reason = FinishedReason.INTERRUPTED` 换成 `acc.finished_reason = FinishedReason.INTERRUPTED`，并把收尾的 `acc.finished_reason = reason` 整行删掉。

`_stream` 的 docstring 里「被取消时它的 `finished_reason` 是 `INTERRUPTED`」那句补成：

```
        - 最后一片是**完整响应**（`is_last=True`），文本已拼好、usage 已吸收，
          `finished_reason` 来自末片的 `finish_reason`（如 `TOOL_CALLS`）；
          被取消时是 `INTERRUPTED`。
```

- [ ] **Step 5: 跑测试确认通过**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py tests/test_model_retry.py -q
```
Expected: 全绿（`test_model_retry.py` 里 `final.finished_reason is COMPLETED` 的断言仍然成立）。

---

### Task 5: `execute_tool_calls`

**Files:**
- Modify: `hello_agents/model/_tool.py`
- Modify: `hello_agents/model/__init__.py`
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `Tool`、`ToolResultBlock`、`Message`、`Role`
- Produces:
  - `execute_tool_calls(tool_calls: Sequence[ToolCallBlock], tools: Mapping[str, Tool]) -> list[Message]`
  - 从 `hello_agents.model` 可导入 `Tool` / `ToolChoice` / `execute_tool_calls`

- [ ] **Step 1: 写失败测试**

```python
# --- execute_tool_calls ---


class _BoomTool(Tool):
    name = "boom"
    description = "总是抛错"
    parameters = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> object:
        raise RuntimeError("炸了")


class _StrTool(Tool):
    name = "as_text"
    description = "返回字符串"
    parameters = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> object:
        return "已经是字符串"


def _call(id: str, name: str, arguments: str = "{}") -> ToolCallBlock:
    return ToolCallBlock(id=id, name=name, arguments=arguments)


@pytest.mark.asyncio
async def test_success_result_is_json_dumped():
    msgs = await execute_tool_calls([_call("c1", "echo", '{"city":"Xian"}')], {"echo": _EchoTool()})

    assert len(msgs) == 1
    assert msgs[0].role is Role.TOOL
    block = msgs[0].content[0]
    assert block.is_error is False
    assert json.loads(block.output) == {"city": "Xian"}
    assert block.tool_call_id == "c1"
    assert block.name == "echo"


@pytest.mark.asyncio
async def test_string_result_is_used_as_is():
    msgs = await execute_tool_calls([_call("c1", "as_text")], {"as_text": _StrTool()})

    assert msgs[0].content[0].output == "已经是字符串"


@pytest.mark.asyncio
async def test_empty_tool_calls_returns_empty_list():
    assert await execute_tool_calls([], {}) == []


@pytest.mark.asyncio
async def test_unknown_tool_becomes_error_result():
    msgs = await execute_tool_calls([_call("c1", "nope")], {})

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "nope" in block.output
    assert block.tool_call_id == "c1"


@pytest.mark.asyncio
async def test_empty_arguments_string_becomes_error_result():
    """`json.loads("")` 抛 JSONDecodeError——必须转成 is_error（Review Focus 1）。"""
    msgs = await execute_tool_calls([_call("c1", "echo", "")], {"echo": _EchoTool()})

    assert msgs[0].content[0].is_error is True


@pytest.mark.asyncio
async def test_non_object_arguments_becomes_error_result():
    msgs = await execute_tool_calls([_call("c1", "echo", '["不是对象"]')], {"echo": _EchoTool()})

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "JSON 对象" in block.output


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result():
    msgs = await execute_tool_calls([_call("c1", "boom")], {"boom": _BoomTool()})

    block = msgs[0].content[0]
    assert block.is_error is True
    assert "RuntimeError" in block.output
    assert "炸了" in block.output


@pytest.mark.asyncio
async def test_unserializable_result_becomes_error_result():
    """工具返回不可 JSON 序列化的对象时不能把 TypeError 冒到闭环外（Review Focus 4）。"""

    class _BadTool(Tool):
        name = "bad"
        description = "返回不可序列化对象"
        parameters = {"type": "object", "properties": {}}

        async def run(self, arguments: dict[str, Any]) -> object:
            return object()

    msgs = await execute_tool_calls([_call("c1", "bad")], {"bad": _BadTool()})

    assert msgs[0].content[0].is_error is True


@pytest.mark.asyncio
async def test_results_keep_input_order():
    """并发执行，但返回顺序与入参一致——id ↔ tool_call_id 才能正确配对。"""
    calls = [_call("c1", "echo", '{"n":1}'), _call("c2", "echo", '{"n":2}'), _call("c3", "echo", '{"n":3}')]

    msgs = await execute_tool_calls(calls, {"echo": _EchoTool()})

    assert [m.content[0].tool_call_id for m in msgs] == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_tool_calls_run_concurrently():
    """并行工具是并发执行的——串行实现会让 peak 停在 1。"""
    running = 0
    peak = 0

    class _GatedTool(Tool):
        name = "gated"
        description = "每个调用都让出一次控制权"
        parameters = {"type": "object", "properties": {}}

        async def run(self, arguments: dict[str, Any]) -> object:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1
            return "ok"

    calls = [_call(f"c{i}", "gated") for i in range(3)]

    await execute_tool_calls(calls, {"gated": _GatedTool()})

    assert peak == 3


def test_tool_protocol_is_exported_from_package():
    from hello_agents.model import Tool as ExportedTool
    from hello_agents.model import execute_tool_calls as exported_execute

    assert ExportedTool is Tool
    assert exported_execute is execute_tool_calls
```

import 区补上 `import asyncio`、`from hello_agents.model._tool import Tool, execute_tool_calls`。

- [ ] **Step 2: 跑测试确认失败**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q -k "execute or result or concurrently or exported"
```
Expected: FAIL —— `ImportError: cannot import name 'execute_tool_calls'`。

- [ ] **Step 3: 在 `_tool.py` 里实现**

import 区加上：

```python
import asyncio
import json
import logging
from collections.abc import Mapping, Sequence

from .message import Message, Role, ToolCallBlock, ToolResultBlock

logger = logging.getLogger(__name__)
```

文件末尾加：

```python
async def execute_tool_calls(
    tool_calls: Sequence[ToolCallBlock], tools: Mapping[str, Tool]
) -> list[Message]:
    """执行模型请求的工具调用，产出可直接拼进消息历史的消息列表。

    每个调用**独立兜底**：未知工具、参数解析失败、工具抛错都转成 `is_error=True`
    的结果，不会让整批崩掉——闭环断了比一次工具失败严重得多。

    并发执行（`asyncio.gather`），返回顺序与入参一致：工具之间无依赖是模型发起
    并行调用的前提，串行只会让总耗时累加。
    """
    return list(await asyncio.gather(*(_run_one(call, tools) for call in tool_calls)))


async def _run_one(tool_call: ToolCallBlock, tools: Mapping[str, Tool]) -> Message:
    output, is_error = await _invoke(tool_call, tools)
    return Message(
        role=Role.TOOL,
        content=[
            ToolResultBlock(
                tool_call_id=tool_call.id,
                output=output,
                is_error=is_error,
                name=tool_call.name or None,
            )
        ],
    )


async def _invoke(
    tool_call: ToolCallBlock, tools: Mapping[str, Tool]
) -> tuple[str, bool]:
    """跑一个工具，返回 `(回灌文本, 是否失败)`。任何异常都不外泄。"""
    tool = tools.get(tool_call.name)
    if tool is None:
        return f"未知工具 {tool_call.name!r}", True

    try:
        arguments = json.loads(tool_call.arguments)
    except json.JSONDecodeError as exc:
        return f"参数不是合法 JSON：{exc}", True

    if not isinstance(arguments, dict):
        return f"参数必须是 JSON 对象，收到 {type(arguments).__name__}", True

    try:
        result = await tool.run(arguments)
    except Exception as exc:  # noqa: BLE001 —— 工具是自己人写的，什么都能抛
        # 给模型看简短文本，完整栈进日志：把 traceback 塞进回灌消息既浪费
        # token 又干扰模型，但排查时又必须能看到。
        logger.exception("工具 %s 执行失败", tool_call.name)
        return f"{type(exc).__name__}: {exc}", True

    return _to_output(result)


def _to_output(result: object) -> tuple[str, bool]:
    if isinstance(result, str):
        return result, False
    try:
        return json.dumps(result, ensure_ascii=False), False
    except (TypeError, ValueError) as exc:
        return f"工具返回值无法序列化为 JSON：{exc}", True
```

- [ ] **Step 4: 改 `__init__.py` 导出**

```python
from ._tool import Tool, ToolChoice, execute_tool_calls
```

`__all__` 里按字母序插入 `"Tool"`、`"ToolChoice"`、`"execute_tool_calls"`。

- [ ] **Step 5: 跑测试确认通过**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q
```
Expected: 全绿。

- [ ] **Step 6: lint**

```bash
uv run ruff check hello_agents/model tests/test_model_tools.py
uv run ruff format --check hello_agents/model tests/test_model_tools.py
```

---

### Task 6: 离线闭环

用假模型把 `assistant(tool_calls) → tool → assistant(stop)` 整条链路跑通。

**Files:**
- Test: `tests/test_model_tools.py`

**Interfaces:**
- Consumes: Task 3 的 `_ScriptedModel`、Task 4 的 `to_message`/`get_tool_calls`、Task 5 的 `execute_tool_calls`
- Produces: 无（只验证）

- [ ] **Step 1: 写测试**

```python
# --- 离线闭环（规格 §3.6 的主循环）---


@pytest.mark.asyncio
async def test_offline_tool_loop_reaches_final_answer():
    """假模型按脚本先返回 tool_calls、再返回 stop，验证多轮配对与闭环。"""
    model = _ScriptedModel(
        [
            ChatResponse(
                content=[ToolCallBlock(id="call_1", name="echo", arguments='{"city":"Xian"}')],
                finished_reason=FinishedReason.TOOL_CALLS,
            ),
            ChatResponse(content=[TextBlock(text="西安 22 度")]),
        ]
    )
    tools = {"echo": _EchoTool()}

    messages = [Message.system("你是助手"), Message.user("西安天气如何？")]
    while True:
        response = await model(messages, tools=list(tools.values()), tool_choice="auto")
        messages.append(response.to_message())
        calls = response.get_tool_calls()
        if not calls:
            break
        messages.extend(await execute_tool_calls(calls, tools))

    assert [m.role for m in messages] == [
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    # assistant(tool_calls) 与 role=tool 结果靠 id ↔ tool_call_id 配对
    assert messages[2].get_tool_calls()[0].id == messages[3].content[0].tool_call_id
    assert messages[4].get_text_blocks()[0].text == "西安 22 度"
    assert len(model.seen) == 2
    assert model.seen[1]["messages"][3].role is Role.TOOL


@pytest.mark.asyncio
async def test_offline_streaming_tool_loop():
    """流式版本：消费完流拿到收尾响应，再走同一套判断（规格 §3.6 末句）。"""
    model = _ScriptedModel(
        [
            [
                parse_chunk(_tool_chunk([_delta_tool_call(0, id="call_1", name="echo")])),
                parse_chunk(_tool_chunk([_delta_tool_call(0, arguments='{"city":"Xian"}')])),
                _tool_chunk_delta(finish_reason="tool_calls"),
            ],
            [parse_chunk(_text_chunk("西安 22 度"))],
        ],
        stream=True,
    )
    tools = {"echo": _EchoTool()}
    messages = [Message.user("西安天气如何？")]

    while True:
        final = None
        async for part in await model(messages, tools=list(tools.values())):
            if part.is_last:
                final = part
        messages.append(final.to_message())
        calls = final.get_tool_calls()
        if not calls:
            break
        messages.extend(await execute_tool_calls(calls, tools))

    assert messages[1].get_tool_calls()[0].arguments == '{"city":"Xian"}'
    assert messages[1].get_tool_calls()[0].id == "call_1"
    assert messages[3].get_text_blocks()[0].text == "西安 22 度"
```

- [ ] **Step 2: 跑测试**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_model_tools.py -q -k offline
```
Expected: PASS（前面的任务已把依赖建好）。若失败，按 `superpowers:systematic-debugging` 查，不要靠猜改断言。

---

### Task 7: demo + 端到端验证 + 收口提交

**Files:**
- Create: `examples/model_m6_demo.py`

- [ ] **Step 1: 写 demo**

```python
"""M6 工具调用闭环 demo：非流式闭环 / 并行两个工具 / 流式 tool_calls。

需要真实网络与 .env 里的 API key，会产生费用。

    ./.venv/Scripts/python.exe examples/model_m6_demo.py
"""

import asyncio
import logging
import sys
from typing import Any

from hello_agents.model import ChatResponse, Message, TextBlock, ToolCallBlock
from hello_agents.model._tool import Tool, execute_tool_calls
from hello_agents.model.providers import build_model

# 控制台是 GBK：traceback 与 logging 走 stderr 时中文会乱码，统一导到 stdout。
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)

SPEC = "deepseek:deepseek-flash"


class GetWeather(Tool):
    name = "get_weather"
    description = "查询某城市当前天气"
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名，如 Xian"}},
        "required": ["city"],
    }

    async def run(self, arguments: dict[str, Any]) -> object:
        fake = {
            "Xian": {"temp": 22, "desc": "晴"},
            "Beijing": {"temp": 18, "desc": "多云"},
        }
        return fake.get(arguments["city"], {"temp": None, "desc": "未知城市"})


class Calculator(Tool):
    name = "calculator"
    description = "计算两个数的加减乘除"
    parameters = {
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
            "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
        },
        "required": ["a", "b", "op"],
    }

    async def run(self, arguments: dict[str, Any]) -> object:
        a, b, op = arguments["a"], arguments["b"], arguments["op"]
        table = {"add": a + b, "sub": a - b, "mul": a * b, "div": a / b}
        return {"result": table[op]}


def _text(response: ChatResponse) -> str:
    return "".join(b.text for b in response.content if isinstance(b, TextBlock))


def _report(response: ChatResponse, tools: dict[str, Tool]) -> list[Message]:
    """打印一轮的 tool_calls 与执行结果，返回要回灌的消息。"""
    calls = response.get_tool_calls()
    print(f"  finish={response.finished_reason.value}  tool_calls={len(calls)}")
    for call in calls:
        print(f"    -> {call.name}({call.arguments})")
    return []


async def run_loop(model, messages: list[Message], tools: dict[str, Tool]) -> ChatResponse:
    """规格 §3.6 的主循环——「要不要再来一轮」属于上层，不写进 model 层。"""
    for turn in range(1, 6):
        print(f"[round {turn}]")
        response = await model(messages, tools=list(tools.values()), tool_choice="auto")
        messages.append(response.to_message())
        calls = response.get_tool_calls()
        print(f"  finish={response.finished_reason.value}  tool_calls={len(calls)}")
        if not calls:
            print(f"  answer: {_text(response)}")
            return response
        results = await execute_tool_calls(calls, tools)
        for message in results:
            block = message.content[0]
            print(f"    <- {block.name}: {block.output}  is_error={block.is_error}")
        messages.extend(results)
    raise RuntimeError("超过 5 轮还没收敛——主循环该有上限")


async def run_stream_loop(model, messages: list[Message], tools: dict[str, Tool]) -> ChatResponse:
    """流式版本：消费完整条流拿到收尾响应，再走同一套判断。"""
    final: ChatResponse | None = None
    async for part in await model(messages, tools=list(tools.values()), tool_choice="auto"):
        if part.is_last:
            final = part
            continue
        for block in part.content:
            if isinstance(block, ToolCallBlock):
                print(f"    [frag] index={block.index} id={block.id!r} args={block.arguments!r}")
    assert final is not None
    messages.append(final.to_message())
    calls = final.get_tool_calls()
    print(f"  finish={final.finished_reason.value}  tool_calls={len(calls)}")
    for call in calls:
        print(f"    -> {call.name}({call.arguments})")
    if not calls:
        print(f"  answer: {_text(final)}")
        return final
    results = await execute_tool_calls(calls, tools)
    for message in results:
        block = message.content[0]
        print(f"    <- {block.name}: {block.output}  is_error={block.is_error}")
    messages.extend(results)
    return final


async def main() -> None:
    tools = {"get_weather": GetWeather(), "calculator": Calculator()}

    print("=" * 60)
    print("[1] 非流式闭环：单工具")
    model = build_model(SPEC)
    await run_loop(
        model,
        [Message.system("你在回答前必须先调用工具查证。"), Message.user("西安天气如何？")],
        tools,
    )

    print("=" * 60)
    print("[2] 并行两个工具")
    model = build_model(SPEC)
    await run_loop(
        model,
        [
            Message.system("你在回答前必须先调用工具查证。"),
            Message.user("西安天气如何？顺便帮我算一下 12 * 34 等于多少。"),
        ],
        tools,
    )

    print("=" * 60)
    print("[3] 流式 tool_calls")
    model = build_model(SPEC, stream=True)
    messages = [Message.system("你在回答前必须先调用工具查证。"), Message.user("西安天气如何？")]
    await run_stream_loop(model, messages, tools)
    print(f"  [流式] 拼接后的 arguments = {messages[1].get_tool_calls()[0].arguments!r}")
    final = await run_stream_loop(model, messages, tools)
    print(f"  [流式] 最终正文 = {_text(final)}")


if __name__ == "__main__":
    asyncio.run(main())
```

写完后删掉没用上的 `_report`（Step 1 里若留下就删）。

- [ ] **Step 2: 全量离线测试 + lint**

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q
uv run ruff check hello_agents tests examples
uv run ruff format --check hello_agents tests examples
```
Expected: 464 + 新增用例全过，仅 `test_embedding.py::test_factory_explicit_backend_raises_when_missing` 那条既有失败。`ruff check` 的既有 BLE001 数量不变。

- [ ] **Step 3: 征求同意后跑真实网络**

先问用户，得到同意再跑：

```bash
./.venv/Scripts/python.exe examples/model_m6_demo.py
```

Expected:
- [1] 至少一轮 `tool_calls=1` → 回灌 `is_error=False` → 下一轮 `finish=completed` 并打印答案；
- [2] 出现一轮 `tool_calls=2`（若模型仍只调一个，改写提示词再试，不要改代码）；
- [3] `[frag]` 行显示分片按 `index=0` 到达，拼接后的 `arguments` 是合法 JSON。

任一段不成立，按 `superpowers:systematic-debugging` 查根因，不要放宽断言或改 demo 掩盖。

- [ ] **Step 4: 收口提交**

只 add 本工作线涉及的文件：

```bash
git add hello_agents/model tests/test_model_tools.py tests/test_model_retry.py \
        examples/model_m6_demo.py docs/specs/2026-09-30-m6-tools-design.md \
        docs/plans/2026-09-30-m6-tools-plan.md
git status --short   # 确认没有 .gitignore / uv.lock / .claude/ / core/message.py 混进来
git commit -F - <<'MSG'
feat(model): M6 工具调用闭环（Tool 协议 / 流式 index 拼接 / execute_tool_calls）

<按 M5 的写法，逐条写清本阶段的决策与理由，末尾附
Co-Authored-By: Claude Code <noreply@anthropic.com>>
MSG
```

**不要 `git push`**——用户明确说过不需要开 PR，推送前先问。

---

## Self-Review

**Spec coverage**：规格 §3.1→Task 1/5；§3.2→Task 1；§3.3→Task 2；§3.4→Task 2/4；§3.5→Task 5；§3.6→Task 6/7；§5 动手任务 1–7 逐条对上。规格 §6 自检清单第 7 条（`tool_choice` 改变模型行为）在 Task 3 的透传测试 + Task 7 的 demo [2] 里覆盖。

**类型一致性**：`ToolCallBlock.index: int | None`、`ToolResultBlock.name: str | None`、`ToolChoice`、`to_openai_tools`、`_map_finish_reason`、`append_tool_call`、`get_tool_calls`、`to_message`、`execute_tool_calls` 在各任务中名称与签名一致。`_ScriptedModel` / `_FakeCompletions` / `_stream_of` / `_tool_chunk` / `_delta_tool_call` / `_carrier_chunk` / `_tool_chunk_delta` / `_call` 这些 helper 只在 Task 3/4/5/6 定义一次，后续任务直接复用。

**Review Focus**：五条各已落到 `test_empty_arguments_string_becomes_error_result`（Task 5）、`test_assistant_text_alongside_tool_calls_is_kept`（Task 1）、`test_parallel_stream_fragments_are_aligned_by_index`（Task 4）、`test_unserializable_result_becomes_error_result`（Task 5）、`test_openai_compat_omits_tools_key_when_not_given` + `..._for_empty_list`（Task 3）。
