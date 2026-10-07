# 里程碑 T0/T1：环境骨架 + 出参模型与 ToolBase

> 本文档尽量给出可直接照做的步骤、完整接口契约、字段说明、示例、边界与验收清单。
> 对标（只读）：`agentscope/src/agentscope/tool/` 的 `_base.py`、`_response.py`、
> `_types.py`。我们**裁剪**掉 agentscope 里的 permission 引擎、洋葱 middleware、
> 多模态 DataBlock、外部工具/状态注入——这些不在当前范围。

---

# T0：环境与包骨架

## T0.1 目标

建立独立包 `hello_agents/tool/` 的目录与空文件，装好 MCP 依赖，做到能 `import`。
**本阶段不写任何业务逻辑。**

## T0.2 目录树（最终要建成）

```
hello_agents/tool/
  __init__.py
  _types.py
  _response.py
  _base.py
  _executor.py
  _adapters.py
  _toolkit.py
  _governance.py
  builtin/
    __init__.py
    _fs.py
    _search.py
    _bash.py
  mcp/
    __init__.py
    _config.py
    _adapter.py
    _stdio.py
    _http.py
    mcp_servers.yaml
```

## T0.3 操作步骤（PowerShell，项目根目录）

1. 安装 MCP 官方 SDK（pyyaml 已随 model 里程碑装过，可在 `pyproject.toml` 确认）：

```powershell
Set-Location D:\projects\hello-agents
uv add mcp
```

2. 创建目录：

```powershell
New-Item -ItemType Directory -Force hello_agents\tool\builtin, hello_agents\tool\mcp | Out-Null
```

3. 创建空占位文件（T1 起逐个填充）：

```powershell
$files = @(
  'hello_agents\tool\__init__.py','hello_agents\tool\_types.py',
  'hello_agents\tool\_response.py','hello_agents\tool\_base.py',
  'hello_agents\tool\_executor.py','hello_agents\tool\_adapters.py',
  'hello_agents\tool\_toolkit.py','hello_agents\tool\_governance.py',
  'hello_agents\tool\builtin\__init__.py','hello_agents\tool\builtin\_fs.py',
  'hello_agents\tool\builtin\_search.py','hello_agents\tool\builtin\_bash.py',
  'hello_agents\tool\mcp\__init__.py','hello_agents\tool\mcp\_config.py',
  'hello_agents\tool\mcp\_adapter.py','hello_agents\tool\mcp\_stdio.py',
  'hello_agents\tool\mcp\_http.py','hello_agents\tool\mcp\mcp_servers.yaml'
)
foreach ($f in $files) { if (-not (Test-Path $f)) { New-Item -ItemType File $f | Out-Null } }
```

4. 在 `hello_agents/tool/__init__.py` 先写一行（T1 后再补导出）：

```python
"""hello_agents tool subsystem (independent of the model layer)."""
```

## T0.4 验证

```powershell
uv run python -c "import hello_agents.tool; print('tool pkg ok')"
```

期望输出 `tool pkg ok`。

## T0.5 自检

- [ ] 目录树与 T0.2 一致；
- [ ] `mcp` 已写入 `pyproject.toml` 的 dependencies；
- [ ] 包可导入，无报错。

---

# T1：出参模型（ToolResponse）+ ToolBase 核心抽象

## T1.1 设计原理（先理解再写）

### 为什么 tool 包要有自己的"出参模型"

你在 M6/model 里用的是 model 层的 `ToolResultBlock`。但我们约定 **tool 包完全独立、
不 import model**（总览 §3）。所以 tool 包需要一个属于自己的结果类型 `ToolResponse`，
将来由**薄桥接层**（T10）把它转成 model 的消息。这样工具框架可以脱离 model 单独复用。

### 为什么结果用 "content blocks" 而不是一个 `output: str`

agentscope 的 `ToolResponse` 内含 `content: list[TextBlock | DataBlock]`：
- 一个工具可能产出多段内容；
- 为将来多模态（图像/文件）预留统一形状。

我们当前**不含多模态**，但保留 block 列表结构，只实现一个 `ToolTextBlock`。
这样你学到的是可扩展形态，而不是将来要推倒重来的 `str`。

### 为什么把 `call` 和 `__call__` 分开

- `call`：工具作者**实现**的业务逻辑（子类覆盖）；
- `__call__`：框架的**统一入口**，T1 先简单 `await call`，T2 在这里收口
  sync/async 适配、超时、取消、异常兜底，T5 再挂 hook/确认/重试。
- 工具作者只关心 `call`，横切逻辑不污染每个工具。这是模板方法模式。

### `is_read_only` / `is_concurrency_safe` 有什么用

- `is_read_only=True`：无副作用（如读文件、计算）。T5 人工确认时，**只读工具可免确认、
  写操作才需确认**——这个标志就是判据；
- `is_concurrency_safe=True`：该工具可被并行调用（无共享可变状态）。
  T4/Toolkit 并行执行时参考。

---

## T1.2 接口契约：`_types.py`

工具执行状态枚举：

```python
from enum import StrEnum


class ToolStatus(StrEnum):
    SUCCESS = "success"        # 正常完成
    ERROR = "error"            # 工具自身报错（已被捕获、转成结构化结果）
    DENIED = "denied"          # 人工确认被拒绝
    INTERRUPTED = "interrupted"  # 执行中被取消
```

> 说明：agentscope 还有 `RUNNING`（用于工具**流式产出**多段 chunk）。
> 我们 T1–T9 的工具都是"一次返回一个完整结果"，不做工具流式，故暂不需要 RUNNING。

---

## T1.3 接口契约：`_response.py`

### 字段说明

| 类 | 字段 | 类型 | 含义 |
|---|---|---|---|
| ToolTextBlock | type | Literal["text"]，默认 "text" | 判别字段 |
| ToolTextBlock | text | str | 一段文本内容 |
| ToolTextBlock | id | str，默认 uuid hex | 块标识（同 model 的 TextBlock） |
| ToolResponse | id | str，默认 uuid hex | 本次工具结果标识 |
| ToolResponse | status | ToolStatus，默认 SUCCESS | 执行状态 |
| ToolResponse | content | list[ToolTextBlock]，默认 [] | 结果块（当前只有文本块） |
| ToolResponse | metadata | dict，默认 {} | 附加信息（不回灌正文、供程序读取） |

### 完整代码

```python
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._types import ToolStatus


class ToolTextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ToolResponse(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: ToolStatus = ToolStatus.SUCCESS
    content: list[ToolTextBlock] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def get_text(self) -> str:
        """把所有文本块按顺序拼成一个字符串。"""
        return "".join(block.text for block in self.content)

    @classmethod
    def succeed(cls, text: str, **metadata: object) -> "ToolResponse":
        """成功结果：单段文本，status=SUCCESS。"""
        return cls(
            status=ToolStatus.SUCCESS,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )

    @classmethod
    def fail(cls, text: str, **metadata: object) -> "ToolResponse":
        """失败结果：错误说明文本，status=ERROR。"""
        return cls(
            status=ToolStatus.ERROR,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )

    @classmethod
    def denied(cls, reason: str | None = None, **metadata: object) -> "ToolResponse":
        """人工确认被拒：status=DENIED。"""
        text = reason or "工具调用被用户拒绝"
        return cls(
            status=ToolStatus.DENIED,
            content=[ToolTextBlock(text=text)],
            metadata=dict(metadata),
        )
```

> 边界：`INTERRUPTED` 状态的结果在 T2（取消收口）里构造，T1 不必提供 classmethod。

---

## T1.4 接口契约：`_base.py`

### 字段/方法说明

| 成员 | 种类 | 含义 |
|---|---|---|
| name | ClassVar[str] | 呈现给模型的工具名（必填） |
| description | ClassVar[str] | 给模型看的工具说明（必填） |
| input_schema | ClassVar[dict] | 入参 JSON Schema，必须是 type=object |
| is_read_only | ClassVar[bool] = False | 是否无副作用（免确认判据） |
| is_concurrency_safe | ClassVar[bool] = True | 是否可并行调用 |
| call | abstract async 方法 | 工具作者实现业务逻辑，返回 ToolResponse |
| __call__ | async 方法 | 框架统一入口（T1 仅 await call） |
| get_function_schema | 方法 | 产出 OpenAI `tools` 数组里的一项（纯 dict） |

### 完整代码

```python
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ._response import ToolResponse


class ToolBase(ABC):
    """所有工具的统一抽象。工具作者：声明类属性 + 实现 `call`。

    横切逻辑（sync/async 适配、超时、取消、异常兜底、hook、确认、重试）
    不在本类暴露给作者，T2/T5 在 `__call__` 统一收口。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    is_read_only: ClassVar[bool] = False
    is_concurrency_safe: ClassVar[bool] = True

    def __init__(self) -> None:
        self._validate_input_schema()

    def _validate_input_schema(self) -> None:
        """input_schema 必须是「对象 + properties」，否则在实例化期就报错。

        无参工具也合法：properties 为空 dict、required 为空列表。
        """
        schema = self.input_schema
        if not (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
        ):
            raise ValueError(
                f"工具 {self.name!r} 的 input_schema 必须是 type='object' 且含 "
                f"properties 的 JSON Schema，收到：{schema!r}"
            )

    @abstractmethod
    async def call(self, **kwargs: Any) -> ToolResponse:
        """业务逻辑。入参以关键字参数传入（即 input_schema 声明的字段）。"""

    async def __call__(self, **kwargs: Any) -> ToolResponse:
        """框架统一入口。T1：直接 await call；T2 在此收口执行治理。"""
        return await self.call(**kwargs)

    def get_function_schema(self) -> dict[str, Any]:
        """产出 OpenAI function 形式的 schema（不依赖 model 层，纯 dict）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
```

> 边界：本类不认识 model，也不负责把结果回灌——那是 T10 桥接层的事。

---

## T1.5 留给你的动手任务

1. 完成 T0，建好骨架并装依赖。
2. 在 `_types.py`、`_response.py`、`_base.py` 填入上述实现。
3. 亲手写**第一个工具**（可放进 `hello_agents/tool/builtin/`，例如先在一个
   `examples/tool_t1_smoke.py` 里验证）：

```python
from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse


class EchoTool(ToolBase):
    name = "echo"
    description = "原样返回传入的文本"
    is_read_only = True
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)
```

   验证脚本：

```python
import asyncio
from hello_agents.tool._response import ToolStatus


async def main() -> None:
    tool = EchoTool()
    resp = await tool(text="hello tool")
    print(resp.status is ToolStatus.SUCCESS, resp.get_text())
    print(tool.get_function_schema())


asyncio.run(main())
```

   期望：`True hello tool`，并打印出含 name/description/parameters 的 dict。

4. 写测试 `tests/test_tool_t1.py`（离线、不依赖网络）：
   - `ToolResponse.succeed/fail/denied` 的 status 与 get_text 正确；
   - metadata 能存取；
   - 合法 input_schema 的工具可实例化；
   - 非法 input_schema（缺 type、properties 不是 dict）实例化即抛 ValueError；
   - `get_function_schema` 形状为 `{"type":"function","function":{...}}`；
   - EchoTool 调用返回 SUCCESS 且文本一致。

---

## T1.6 验收清单

- [ ] T0 骨架与依赖就绪、包可导入；
- [ ] ToolStatus 四态齐备；
- [ ] ToolTextBlock / ToolResponse 字段与方法符合契约；
- [ ] ToolBase 抽象、schema 校验、get_function_schema 正确；
- [ ] EchoTool 冒烟通过；
- [ ] test_tool_t1 全绿；
- [ ] ruff check / format 通过：

```powershell
uv run ruff check hello_agents/tool tests/test_tool_t1.py
uv run ruff format --check hello_agents/tool tests/test_tool_t1.py
```

---

## T1.7 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪说明 |
|---|---|---|
| ToolStatus | `message.ToolResultState`（经 `_response` 引用） | 状态语义对应；RUNNING 暂不做 |
| ToolTextBlock | `message.TextBlock` | 文本块；DataBlock（多模态）裁剪 |
| ToolResponse | `tool/_response.py` ToolResponse | content/status/metadata；append_chunk（工具流式）裁剪 |
| ToolBase.call | `tool/_base.py` call | 业务覆盖点 |
| ToolBase.__call__ | `tool/_base.py` __call__ | T1 简化；middleware 洋葱在 T5 用 hook 替代 |
| get_function_schema | `_types.RegisteredTool.get_tool_schema` | 我们直接在 ToolBase 给最简版 |

---

## 完成后

把 `_types.py`、`_response.py`、`_base.py`、EchoTool 冒烟输出与 `test_tool_t1.py`
结果贴给我 review。通过后进入 **T2：执行收口（sync/async 统一、线程池、超时、取消、
异常结构化）**。
