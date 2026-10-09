# 里程碑 T9：MCP 远程接入（Streamable HTTP / SSE）——传输可插拔 · 本地起 server 再连

> 目标：让 MCPClient 在 stdio 之外支持远程 HTTP 传输；会话与 MCPTool 适配逻辑完全复用。
> 按约定本地用 mcp SDK 起一个 streamable-http server，再连它做离线可重复的集成验证。
> 对标（只读）：`agentscope/mcp/_mcp_client.py` 的 `_create_http_client`。

> 环境事实：本机安装的是 **mcp 2.x**。v1 的 `FastMCP` 在 2.x 已改名为
> `mcp.server.mcpserver.MCPServer`；字段统一 snake_case。本图纸按 2.x API。

---

## 9.1 设计原理（先理解）

### 1) 传输与会话分离：为什么加 HTTP 几乎不改适配层

MCP 的分层：

```
传输层（stdio | streamable-http | sse）  只负责给出 (read_stream, write_stream)
        ↓
会话层 ClientSession                      JSON-RPC：initialize / list_tools / call_tool
        ↓
适配层 MCPTool                            ToolBase 接口、结果归一
```

三种传输的 context manager，enter 后都产出同一对 `(read, write)` 流。因此只要在 connect
时**按配置选一个传输上下文**，后面建 session、initialize、list_tools、MCPTool 全都不变。
这就是 T8 强调的解耦在新需求下的回报：T9 的 MCPTool **一行都不用改**。

### 2) 两种 HTTP 传输怎么选

- **Streamable HTTP（推荐，现代）**：单一端点，默认路径 `/mcp`，客户端
  `streamable_http_client(url, http_client=None)`；自定义请求头通过自建
  `httpx.AsyncClient(headers=...)` 传入；
- **SSE（旧）**：端点通常以 `/sse` 结尾，客户端 `sse_client(url, headers=, timeout=)`，
  原生支持 headers。

判别规则（对齐 agentscope）：URL 以 `/sse` 结尾走 sse_client，否则走 streamable_http_client。

### 3) stateful HTTP；stateless 裁剪

- 本里程碑 HTTP 也用 **stateful**：connect 建一次连接、复用 session、close 关闭，
  和 stdio 同一套生命周期，最简单；
- agentscope 还支持 HTTP stateless（每次调用临时建连，client_gen 形态）。学习边际低，裁剪，
  文档末尾留思考。

### 4) 资源清理的两个细节

- 自建的 `httpx.AsyncClient` 要在关闭时 `aclose()`：用
  `stack.push_async_callback(http_client.aclose)` 注册进 AsyncExitStack，随连接一起清理，
  避免连接泄漏；
- 集成测试里本地 server 是**独立子进程**：用完要像 T7 那样连进程树一起杀（Windows
  `taskkill /F /T`），否则 server 常驻占端口。

### 5) 为什么本地起 server 而不是连公网

本地起 server：不依赖外网、不花钱、端口/工具确定、可重复跑，还能同时练习 mcp 的 server
侧。用 `MCPServer` + `@server.tool()` 几行就能定义一个带 echo/add 的 server。

---

## 9.2 接口契约：扩展 `mcp/_config.py`

在原有 `StdioServerConfig` 基础上新增 `HttpServerConfig`，并让 `load_mcp_config` 按
`transport` 字段分发：

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


class HttpServerConfig(BaseModel):
    transport: Literal["http"] = "http"
    url: str
    headers: dict[str, str] | None = None


_SERVER_MODELS = {
    "stdio": StdioServerConfig,
    "http": HttpServerConfig,
}


def load_mcp_config(
    config_path: str | Path,
) -> dict[str, StdioServerConfig | HttpServerConfig]:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", {})

    result: dict[str, StdioServerConfig | HttpServerConfig] = {}
    for name, cfg in servers.items():
        transport = cfg.get("transport", "stdio")
        model = _SERVER_MODELS[transport]
        result[name] = model(**cfg)
    return result
```

---

## 9.3 接口契约：改造 `mcp/_client.py`

把"选传输"抽成 `_build_transport`，connect 按配置进入对应上下文：

```python
from contextlib import AsyncExitStack
from typing import Self

import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from ._config import HttpServerConfig, StdioServerConfig
from ._mcp_tool import MCPTool


class MCPClient:
    """MCP 客户端：支持 stdio / streamable-http / sse，统一 stateful 生命周期。"""

    def __init__(self, name: str, config: StdioServerConfig | HttpServerConfig):
        self._name = name
        self._config = config
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    def _build_transport(self, stack: AsyncExitStack):
        cfg = self._config

        if isinstance(cfg, StdioServerConfig):
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env,
                cwd=cfg.cwd,
            )
            return stdio_client(params)

        # HTTP：URL 以 /sse 结尾走 SSE，否则走 Streamable HTTP
        if cfg.url.rstrip("/").endswith("/sse"):
            return sse_client(cfg.url, headers=cfg.headers)

        http_client = None
        if cfg.headers:
            http_client = httpx.AsyncClient(headers=cfg.headers)
            # 随连接关闭一起释放，杜绝泄漏
            stack.push_async_callback(http_client.aclose)
        return streamable_http_client(cfg.url, http_client=http_client)

    async def connect(self) -> None:
        if self._stack is not None:
            raise RuntimeError(f"MCP '{self._name}' 已连接")

        stack = AsyncExitStack()
        await stack.__aenter__()

        transport = self._build_transport(stack)
        read_stream, write_stream = await stack.enter_async_context(transport)
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
            MCPTool(server_name=self._name, raw_tool=tool, session=self._session)
            for tool in result.tools
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

> `mcp/_mcp_tool.py` **不需要任何改动**。

更新 `mcp/__init__.py` 导出 `HttpServerConfig`：

```python
from ._client import MCPClient
from ._config import (
    HttpServerConfig,
    StdioServerConfig,
    load_mcp_config,
)
from ._mcp_tool import MCPTool

__all__ = [
    "HttpServerConfig",
    "MCPClient",
    "MCPTool",
    "StdioServerConfig",
    "load_mcp_config",
]
```

> `__all__` 的顺序是 ruff `RUF022` 排的（不是手写的），照上面抄即可过 `ruff check`。

---

## 9.4 本地 server 脚本：`tests/_mcp_http_server.py`

```python
"""本地 Streamable HTTP MCP server，供 T9 集成测试连接。"""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("local-demo")


@server.tool()
def echo(message: str) -> str:
    """原样回显消息。"""
    return f"echo:{message}"


@server.tool()
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server.run("streamable-http", host="127.0.0.1", port=port)
```

> 端点默认 `/mcp`。若要练 SSE，把最后一行改为
> `server.run("sse", host="127.0.0.1", port=port)`，URL 用 `http://.../sse`。

更新根目录 `mcp_servers.yaml`（保留 everything，新增本地 HTTP 占位）：

```yaml
servers:
  everything:
    transport: stdio
    command: npx
    args:
      - -y
      - "@modelcontextprotocol/server-everything"

  local_http:
    transport: http
    url: http://127.0.0.1:8765/mcp
```

> `everything` 这条沿用 T8 的修正：`uvx mcp-server-everything` 在 PyPI 上不存在，
> 官方参考 server 只有 npm 包，所以用 `npx -y`（见 08 文档 8.3 的说明）。

---

## 9.5 留给你的动手任务

### 1) 按 9.2/9.3 扩展 `_config.py`、`_client.py`、`__init__.py`；新增 server 脚本与 yaml 条目。

### 2) 离线单测 `tests/test_tool_t9.py`（默认跑）：

```python
from pathlib import Path

from hello_agents.tool.mcp._config import (
    HttpServerConfig,
    StdioServerConfig,
    load_mcp_config,
)


def test_load_http_config(tmp_path: Path):
    f = tmp_path / "mcp.yaml"
    f.write_text(
        "servers:\n  local_http:\n    transport: http\n"
        "    url: http://127.0.0.1:8765/mcp\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(f)
    cfg = configs["local_http"]
    assert isinstance(cfg, HttpServerConfig)
    assert cfg.url == "http://127.0.0.1:8765/mcp"


def test_load_mixed_stdio_and_http(tmp_path: Path):
    f = tmp_path / "mcp.yaml"
    f.write_text(
        "servers:\n"
        "  everything:\n    transport: stdio\n    command: uvx\n"
        "    args: [mcp-server-everything]\n"
        "  web:\n    transport: http\n    url: http://x/sse\n",
        encoding="utf-8",
    )
    configs = load_mcp_config(f)
    assert isinstance(configs["everything"], StdioServerConfig)
    assert isinstance(configs["web"], HttpServerConfig)
    assert configs["web"].url.endswith("/sse")


def test_http_headers_optional():
    assert HttpServerConfig(url="http://x/mcp").headers is None
    cfg = HttpServerConfig(url="http://x/mcp", headers={"X": "y"})
    assert cfg.headers == {"X": "y"}
```

> 传输选择逻辑（`/sse` 后缀判别、headers 建 client）由下面的集成测试做真实覆盖；
> 离线只锁定配置解析。

### 3) 集成测试 `tests/test_tool_t9_integration.py`（marker integration，默认 skip）：

```python
"""T9 集成：本地起 streamable-http server，再以 HTTP 连入并调用。"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.mcp._client import MCPClient
from hello_agents.tool.mcp._config import HttpServerConfig

pytestmark = pytest.mark.integration

SERVER_SCRIPT = Path(__file__).resolve().parent / "_mcp_http_server.py"


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server 在 {timeout}s 内未监听 {port}")


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if os.name == "nt" and proc.pid is not None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()


async def test_local_streamable_http_server():
    port = 8765
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SERVER_SCRIPT), str(port)
    )
    try:
        await asyncio.to_thread(_wait_for_port, port)

        cfg = HttpServerConfig(url=f"http://127.0.0.1:{port}/mcp")
        async with MCPClient("local_http", cfg) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert any(n.endswith("echo") for n in names)
            assert any(n.endswith("add") for n in names)

            echo = next(t for t in tools if t.name.endswith("echo"))
            toolkit = Toolkit(tools=[echo], approver=AutoApprover(True))
            resp = await toolkit.call_tool(echo.name, message="hi-http")
            assert "hi-http" in resp.get_text()
    finally:
        _kill_tree(proc)
        await proc.wait()
```

运行（本地、无需外网）：

```powershell
uv run pytest -m integration tests/test_tool_t9_integration.py -q
```

### 4) 演示 `examples/tool_t9_mcp_http.py`：

- 启动方式可手动开一个终端跑 `uv run python tests/_mcp_http_server.py 8765`；
- 演示脚本用 `HttpServerConfig` + MCPClient 连入，list_tools 并调用 echo/add，打印结果。

---

## 9.6 验收清单

- [x] `_config.py`/`_client.py`/`__init__.py` 与契约一致；MCPTool 未改动（`git diff` 为空）；
- [x] test_tool_t9 离线全绿（3 条）；
- [x] T1–T8 回归全绿（147 passed；连 T9 合计 150 passed）；
- [x] ruff 干净；
- [x] 集成测试 `-m integration` 通过（8.1s）：本地 server 经 HTTP 被发现并成功调用，进程被清理
      （跑完 `netstat` 无 LISTENING，端口已释放）：

```powershell
uv run ruff check hello_agents/tool tests/test_tool_t9.py tests/test_tool_t9_integration.py
uv run ruff format --check hello_agents/tool tests/test_tool_t9.py tests/test_tool_t9_integration.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py tests/test_tool_t7.py tests/test_tool_t8.py tests/test_tool_t9.py -q
```

---

## 9.7 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| HttpServerConfig | `mcp/_config.py` HttpMCPConfig | url/headers 对应；timeout 裁剪 |
| _build_transport | `_mcp_client.py` _create_http_client | `/sse` 判别、streamable 建 httpx client 一致 |
| stateful connect/close | connect/close | 生命周期一致 |
| 本地 MCPServer | （agentscope 不内置演示 server） | 我们新增，便于离线集成 |
| stateless HTTP | list_raw_tools/get_tool 的 client_gen 形态 | 裁剪 |

> 进阶思考：
> ① stateless HTTP：不维持 session，MCPTool 持有一个"传输工厂"，每次 call 临时
>   `async with transport: ClientSession → initialize → call_tool`（agentscope client_gen）；
> ② 远程鉴权：headers 里放 Bearer Token（密钥仍从 .env 读、不写进 yaml）；
> ③ mcp 2.x 的鉴权也支持 OAuth（mcp.server.auth），需要时再深入。

---

## 完成后

把改动的 mcp 代码、离线 pytest 结果与本地 HTTP 集成测试输出贴给我 review。
通过后进入 **T10：薄桥接层——把 ToolResponse 转成 model 层 Message/ToolResultBlock（双向解耦）**。
