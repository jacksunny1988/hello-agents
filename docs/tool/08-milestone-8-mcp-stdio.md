# 里程碑 T8：MCP 接入（stdio）——声明式配置 · 会话生命周期 · 工具适配

> 目标：用官方 `mcp` SDK 接入**本地 stdio MCP server**，把外部工具适配进统一 ToolBase，
> 经 Toolkit 调用。远程（SSE/Streamable HTTP）在 T9。
> 对标（只读）：`agentscope/mcp/_mcp_client.py`、`_config.py`、`tool/_adapters.py` 的 MCPTool。

---

## 8.1 设计原理（先理解）

### 1) MCP 是什么，为什么 stdio 必须 stateful

MCP（Model Context Protocol）把"工具/资源"做成独立进程或远程服务，模型侧通过统一协议
发现和调用，不关心工具用什么语言写。

stdio 传输下，客户端**启动一个子进程**，通过它的 stdin/stdout 收发 JSON-RPC。
连接（进程）建立成本高、且要维持会话状态，因此：

- **stdio 必须 stateful**：显式 connect（起进程、握手 initialize）→ 复用同一 session
  多次调用 → close（杀进程）。不能像无状态 HTTP 那样每次调用临时建连。

### 2) 两个 async context manager + AsyncExitStack

mcp SDK 把生命周期封装成两个异步上下文：

- `stdio_client(params)`：起子进程，enter 后给出 `(read_stream, write_stream)`；
- `ClientSession(read, write)`：JSON-RPC 会话，enter 后需手动 `await session.initialize()`。

用 `AsyncExitStack` 把两者压栈，close 时 `stack.aclose()` 一次性按反序关闭（关 session、
终止子进程）。这等价于 bash 的"收尸"，只是进程清理已由 SDK 上下文封装好。

### 3) 工具发现与适配（MCPTool）

`session.list_tools()` 返回 `ListToolsResult`，**必须取 `.tools`**——它是 pydantic
模型，直接迭代它拿到的是 `(字段名, 值)` 元组，不是工具。每个 `mcp.types.Tool` 含：

- `name`：server 内部工具名（可能含 `.`、`:` 等 LLM 工具名非法字符）；
- `description`；
- `input_schema`：**完整** JSON Schema，可能含 `$defs`/`$ref`/`anyOf`，必须整体保留，
  不能只挑 properties/required（否则嵌套定义丢失）；
- `annotations.read_only_hint`：是否只读。

> ⚠️ SDK 的模型字段是 **snake_case**（`input_schema` / `read_only_hint` / `is_error`），
> camelCase（`inputSchema` / `readOnlyHint` / `isError`）只是 JSON 序列化别名：
> 构造时能按别名传，但**按别名取属性会 `AttributeError`**。

适配规则（对齐 agentscope）：

- 对外工具名 `mcp__{server名}__{工具名}`，工具名里非法字符替换为 `x`（不用 `_`，
  避免和分隔符 `__` 混淆）；调用 server 时仍用**原始名** `self._raw_name`；
- schema 整体拷贝并 `setdefault` 补齐 type/properties/required（满足 ToolBase 校验）；
- is_read_only 取 readOnlyHint，默认 False；
- `call(**kwargs)` → `session.call_tool(原始名, arguments=kwargs)`：
  - 结果 content 中只提取 `TextContent.text`（图片/音频/资源本里程碑裁剪）；
  - `result.isError` → fail，否则 succeed；无文本输出标注 "(无输出)"。

### 4) 与框架其余部分的关系

- MCPTool 是 `async def call`（session 调用天然异步）；超时仍由 T2 `wait_for` 收口；
- 非只读 MCP 工具默认走 T5 人工确认；
- 适配出的 MCPTool 可直接 `register_tool` 进 Toolkit，和本地工具无差别使用。

### 5) 为什么测试分两类

真实 stdio 需要联网下载 server（`npx`/`uvx` 首次都要拉包），CI/离线不确定。因此：

- **离线单测（默认跑）**：假 session + 真实 `mcp.types` 对象，验证适配逻辑 + YAML 解析；
- **集成测试（marker=integration，默认 skip）**：真实 everything server
  跑通 connect→list_tools→call→close，需要时手动启用。

---

## 8.2 目录与文件

新建子包 `hello_agents/tool/mcp/`：

```
hello_agents/tool/mcp/
  __init__.py
  _config.py     # StdioServerConfig + YAML 加载
  _client.py     # MCPClient（stdio stateful 生命周期）
  _mcp_tool.py   # MCPTool（ToolBase 适配器）
mcp_servers.yaml # 项目根，声明式配置（不写密钥）
```

---

## 8.3 接口契约：`mcp/_config.py`

```python
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StdioServerConfig(BaseModel):
    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


def load_mcp_config(path: str | Path) -> dict[str, StdioServerConfig]:
    """从 YAML 读取 {server名: 配置}。

    YAML 结构：
        servers:
          everything:
            transport: stdio
            command: npx
            args: [-y, "@modelcontextprotocol/server-everything"]
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", {})
    return {name: StdioServerConfig(**cfg) for name, cfg in servers.items()}
```

### `mcp_servers.yaml`（项目根）

```yaml
servers:
  everything:
    transport: stdio
    command: npx
    args:
      - -y
      - "@modelcontextprotocol/server-everything"
```

> ⚠️ **`uvx mcp-server-everything` 跑不通**：PyPI 上不存在这个包（uvx 直接
> "No solution found ... not found in the package registry"，server 起不来，表现为
> 握手时报 `MCPError: Connection closed`）。官方 everything 参考 server 只有 npm 包，
> 所以用 `npx -y @modelcontextprotocol/server-everything`（需要 Node）。
> PyPI 上的 `mcp-everything` / `everything-mcp` 是别的项目，不是它。
>
> 另一个真实可用、且不需要 Node 的选择是 `uvx mcp-server-time` / `uvx mcp-server-fetch`，
> 但它们没有 echo 工具，8.6 的断言要跟着改。

---

## 8.4 接口契约：`mcp/_mcp_tool.py`

```python
import re
from typing import Any

import mcp.types

from .._base import ToolBase
from .._response import ToolResponse


class MCPTool(ToolBase):
    """把一个 MCP 远程工具适配成 hello-agents 的 ToolBase。

    name 命名空间化（`mcp__<server>__<tool>`）以避免多个 server 之间的重名冲突；
    注意 `super().__init__()` 必须在属性赋值之后——它会校验 `input_schema`。

    另注意：mcp SDK 的模型字段是 **snake_case**（`input_schema` / `read_only_hint`
    / `is_error`），camelCase 只是 JSON 序列化别名。按 wire 名取属性会 AttributeError。
    """

    def __init__(
        self,
        server_name: str,
        raw_tool: mcp.types.Tool,
        session: Any,
    ) -> None:
        self._raw_name = raw_tool.name
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "x", raw_tool.name)
        self.name = f"mcp__{server_name}__{sanitized}"
        self.description = raw_tool.description or ""

        schema = dict(raw_tool.input_schema) if raw_tool.input_schema else {}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        self.input_schema = schema

        annotations = raw_tool.annotations
        self.is_read_only = bool(
            annotations is not None and getattr(annotations, "read_only_hint", False)
        )

        self._session = session
        super().__init__()

    async def call(self, **kwargs: Any) -> ToolResponse:
        result = await self._session.call_tool(self._raw_name, arguments=kwargs)

        texts = [
            block.text
            for block in result.content
            if isinstance(block, mcp.types.TextContent)
        ]
        text = "\n".join(texts)

        if result.is_error:
            return ToolResponse.fail(text or "MCP 工具返回错误")
        return ToolResponse.succeed(text or "(无输出)")
```

> ⚠️ 契约原文把 `super().__init__()` 放在最前面，那样**必崩**：
> `ToolBase.__init__` 会立刻校验 `self.input_schema`，而此时它还没被赋值
> （实测 `AttributeError: 'MCPTool' object has no attribute 'input_schema'`）。

---

## 8.5 接口契约：`mcp/_client.py`

```python
from contextlib import AsyncExitStack
from typing import Self

from mcp import ClientSession, StdioServerParameters, stdio_client

from ._config import StdioServerConfig
from ._mcp_tool import MCPTool


class MCPClient:
    """stdio MCP 的 stateful 客户端：connect → list_tools → close。"""

    def __init__(self, name: str, config: StdioServerConfig) -> None:
        self._name = name
        self._config = config
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def connect(self) -> None:
        if self._stack is not None:
            raise RuntimeError(f"MCP '{self._name}' 已连接")

        cfg = self._config
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            cwd=cfg.cwd,
        )

        stack = AsyncExitStack()
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        self._stack = stack
        self._session = session

    async def list_tools(self) -> list[MCPTool]:
        if self._session is None:
            raise RuntimeError(f"MCP '{self._name}' 未连接，先 connect()")
        result = await self._session.list_tools()
        return [
            MCPTool(self._name, tool, self._session) for tool in result.tools
        ]

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
```

### `mcp/__init__.py`

```python
from ._client import MCPClient
from ._config import StdioServerConfig, load_mcp_config
from ._mcp_tool import MCPTool

__all__ = [
    "MCPClient",
    "StdioServerConfig",
    "load_mcp_config",
    "MCPTool",
]
```

---

## 8.6 留给你的动手任务

### 1) 安装依赖（mcp 已装则跳过）：

```powershell
uv add mcp pyyaml
```

### 2) 按契约创建 `mcp/` 四个文件与根目录 `mcp_servers.yaml`。

### 3) 离线单测 `tests/test_tool_t8.py`（默认跑，不联网）：

```python
from pathlib import Path

import mcp.types

from hello_agents.tool._response import ToolStatus
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import StdioServerConfig, load_mcp_config
from hello_agents.tool.mcp._mcp_tool import MCPTool


def _raw_tool(name="echo", schema=None, read_only=False):
    """用真实的 mcp.types.Tool，而不是 SimpleNamespace。

    手写 fake 会与 SDK 漂移：SDK 的字段是 snake_case，camelCase 只是序列化别名，
    用 SimpleNamespace 按 wire 名搭一个"看起来一样"的对象，正好把这个差异盖住。
    """
    return mcp.types.Tool(
        name=name,
        description="a tool",
        inputSchema=schema
        or {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        annotations=mcp.types.ToolAnnotations(readOnlyHint=read_only),
    )


class _FakeSession:
    def __init__(self, text="hi", is_error=False):
        self._text = text
        self._is_error = is_error

    async def call_tool(self, name, arguments=None):
        return mcp.types.CallToolResult(
            isError=self._is_error,
            content=[mcp.types.TextContent(type="text", text=self._text)],
        )


def test_tool_name_is_namespaced():
    tool = MCPTool("srv", _raw_tool(), _FakeSession())
    assert tool.name == "mcp__srv__echo"


def test_tool_name_sanitizes_illegal_chars():
    tool = MCPTool("srv", _raw_tool(name="a.b:c"), _FakeSession())
    assert tool.name == "mcp__srv__axbxc"


def test_input_schema_is_preserved():
    schema = {
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/X"}},
        "$defs": {"X": {"type": "string"}},
    }
    tool = MCPTool("srv", _raw_tool(schema=schema), _FakeSession())
    assert "$defs" in tool.input_schema
    assert tool.input_schema["properties"]["x"]["$ref"] == "#/$defs/X"


def test_read_only_hint():
    tool = MCPTool("srv", _raw_tool(read_only=True), _FakeSession())
    assert tool.is_read_only is True


async def test_call_success_extracts_text():
    tool = MCPTool("srv", _raw_tool(), _FakeSession(text="hello"))
    resp = await tool(message="hello")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hello"


async def test_call_error_returns_fail():
    tool = MCPTool("srv", _raw_tool(), _FakeSession(text="boom", is_error=True))
    resp = await tool(message="x")
    assert resp.status is ToolStatus.ERROR
    assert "boom" in resp.get_text()


class _FakeListSession:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return mcp.types.ListToolsResult(tools=self._tools)


async def test_client_namespaces_tools_by_server_name():
    """list_tools 必须取 result.tools。

    直接迭代 ListToolsResult 得到的是 pydantic 的 (字段名, 值) 元组，不是工具。
    """
    raw = mcp.types.Tool(
        name="echo",
        description="a tool",
        inputSchema={"type": "object", "properties": {}},
    )
    client = MCPClient("everything", StdioServerConfig(command="npx"))
    client._session = _FakeListSession([raw])

    tools = await client.list_tools()

    assert [t.name for t in tools] == ["mcp__everything__echo"]


def test_load_mcp_config(tmp_path: Path):
    yaml_file = tmp_path / "mcp.yaml"
    yaml_file.write_text(
        "servers:\n  everything:\n    transport: stdio\n"
        "    command: uvx\n    args: [mcp-server-everything]\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(yaml_file)
    assert "everything" in configs
    assert configs["everything"].command == "uvx"
    assert configs["everything"].args == ["mcp-server-everything"]


def test_load_mcp_config_missing_servers(tmp_path: Path):
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("{}\n", encoding="utf-8")
    assert load_mcp_config(yaml_file) == {}
```

### 4) 集成测试 `tests/test_tool_t8_integration.py`（真实 server，默认 skip）：

```python
from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import load_mcp_config

pytestmark = pytest.mark.integration

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mcp_servers.yaml"


async def test_real_everything_server():
    # 直接读 mcp_servers.yaml，和 examples/tool_t8_mcp.py 共用同一份声明
    cfg = load_mcp_config(CONFIG_PATH)["everything"]
    async with MCPClient("everything", cfg) as client:
        tools = await client.list_tools()
        assert len(tools) > 0

        # everything 的 echo 参数名是 message（不是 text）
        echo = next(t for t in tools if t.name.endswith("echo"))
        toolkit = Toolkit(tools=[echo], approver=AutoApprover(True))
        resp = await toolkit.call_tool(echo.name, message="hello-mcp")
        assert "hello-mcp" in resp.get_text()
```

在 `pyproject.toml` 注册 marker（避免 unknown-mark 警告）**并让默认运行排除它**：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
# 默认排除需要外部进程/网络的集成测试；显式打开：uv run pytest -m integration
addopts = "-m 'not integration'"
markers = [
  "e2e: 端到端测试，发真实网络请求并产生费用；默认跳过，用 RUN_MODEL_E2E=1 打开",
  "integration: 需要外部进程/网络的集成测试，默认不跑；用 -m integration 打开",
]
```

> ⚠️ 只注册 marker 是**达不到"默认不跑"的**——`pytest` 照样会执行带标记的用例。
> 必须再加 `addopts`（命令行 `-m integration` 会覆盖 addopts 里的 `-m`，实测可行）。

手动运行集成测试（首次会下载 server，需联网、耐心等待）：

```powershell
uv run pytest -m integration tests/test_tool_t8_integration.py -q
```

### 5) 演示 `examples/tool_t8_mcp.py`：

- `load_mcp_config("mcp_servers.yaml")`；
- 对每个 server：`async with MCPClient(name, cfg)` → list_tools → 打印工具名/schema；
- 找到 echo 工具，经 Toolkit（AutoApprover(True)）调用并打印结果。

---

## 8.7 验收清单

- [x] `mcp/` 四文件 + `mcp_servers.yaml` 与契约一致（含上面标出的 4 处契约修正）；
- [x] test_tool_t8 离线全绿（9 条）；
- [x] T1–T7 回归全绿；
- [x] ruff 干净；
- [x] （推荐，需联网）集成测试 `-m integration` 通过，真实 echo 回灌成功（7.8s）：

```powershell
uv run ruff check hello_agents/tool tests/test_tool_t8.py examples/tool_t8_mcp.py
uv run ruff format --check hello_agents/tool tests/test_tool_t8.py examples/tool_t8_mcp.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py tests/test_tool_t7.py tests/test_tool_t8.py -q
```

---

## 8.8 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| StdioServerConfig | `mcp/_config.py` StdioMCPConfig | 字段对应；裁剪 encoding_error_handler |
| MCPClient | `mcp/_mcp_client.py` MCPClient | stdio stateful 生命周期一致；裁剪 HTTP/stateless/enable-disable |
| MCPTool | `tool/_adapters.py` MCPTool | 命名/schema 透传/call 一致；裁剪图片音频资源块、权限引擎 |
| load_mcp_config | （agentscope 多为构造传入） | 我们新增 YAML 声明式加载 |

> 进阶思考（T9 预告）：
> ① HTTP MCP 用 `streamable_http_client(url)` / `sse_client(url)` 替换 stdio_client，
>   其余会话/适配逻辑完全复用；
> ② HTTP 支持 stateless：每次 call 临时建连（agentscope 的 client_gen 形态）；
> ③ 非文本 content（图片/资源）可在打通多模态后补 DataBlock。

---

## 完成后

把 `mcp/` 代码、离线 pytest 结果，以及（若跑了）集成测试/演示输出贴给我 review。
通过后进入 **T9：MCP 远程接入（SSE / Streamable HTTP，含本地起 server 再连）**。
