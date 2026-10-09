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
