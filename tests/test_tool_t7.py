"""T7：bash 工具（子进程执行 · 退出码 · 超时/取消杀进程 · 审批），离线确定性"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._response import ToolStatus
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.builtin._bash import BashTool

PY = sys.executable


def _sleep_then_touch(seconds: float, marker: Path) -> str:
    """构造「先睡再写文件」的命令：子进程若没被杀掉，marker 就会被写出来。"""
    code = f"import time; time.sleep({seconds}); open(r'{marker}','w').write('x')"
    return f'"{PY}" -c "{code}"'


async def test_successful_command_returns_stdout():
    resp = await BashTool()(command=f'"{PY}" -c "print(123)"')
    assert resp.status is ToolStatus.SUCCESS
    assert "123" in resp.get_text()


async def test_nonzero_exit_returns_error_with_code():
    resp = await BashTool()(command=f'"{PY}" -c "import sys; sys.exit(3)"')
    assert resp.status is ToolStatus.ERROR
    assert "3" in resp.get_text()


async def test_stderr_is_captured():
    resp = await BashTool()(
        command=f'"{PY}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(1)"'
    )
    assert "boom" in resp.get_text()


async def test_empty_output_is_labelled():
    resp = await BashTool()(command=f'"{PY}" -c "pass"')
    assert "(无输出)" in resp.get_text()


async def test_timeout_kills_and_returns_error():
    tool = BashTool()
    tool.timeout = 0.3
    resp = await tool(command=f'"{PY}" -c "import time; time.sleep(30)"')
    assert resp.status is ToolStatus.ERROR
    assert "超时" in resp.get_text()


async def test_cancel_kills_and_propagates():
    task = asyncio.create_task(
        BashTool()(command=f'"{PY}" -c "import time; time.sleep(30)"')
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- 收尸：超时/取消后子进程必须真的死掉 --------------------------------------


async def test_timeout_really_kills_the_child(tmp_path: Path):
    """只返回 ERROR 不算数：超时后子进程必须被终止，而不是等它自己跑完。

    Windows 下 create_subprocess_shell 会先起 cmd.exe，只 kill 它并不会终止
    真正在执行的命令——这里用「睡完写文件」来验证它确实没机会写完。
    """
    marker = tmp_path / "survived.txt"
    tool = BashTool()
    tool.timeout = 0.2

    started = time.monotonic()
    resp = await tool(command=_sleep_then_touch(2.0, marker))
    elapsed = time.monotonic() - started

    assert resp.status is ToolStatus.ERROR
    assert "超时" in resp.get_text()
    assert elapsed < 1.2, f"超时没有及时返回，等了 {elapsed:.2f}s"

    await asyncio.sleep(2.2)  # 子进程若还活着，这段时间足够它写出 marker
    assert not marker.exists(), "子进程在超时后仍然存活并执行了命令"


async def test_cancel_really_kills_the_child(tmp_path: Path):
    marker = tmp_path / "survived.txt"
    task = asyncio.create_task(BashTool()(command=_sleep_then_touch(2.0, marker)))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(2.2)
    assert not marker.exists(), "子进程在取消后仍然存活并执行了命令"


async def test_cwd_is_used(tmp_path: Path):
    resp = await BashTool(cwd=str(tmp_path))(
        command=f'"{PY}" -c "import os; print(os.getcwd())"'
    )
    assert str(tmp_path) in resp.get_text()


# --- 审批 -------------------------------------------------------------------


async def test_bash_via_toolkit_approved():
    toolkit = Toolkit(tools=[BashTool()], approver=AutoApprover(True))
    resp = await toolkit.call_tool(
        "bash", command=f'"{PY}" -c "print(\'ok\')"', description="测试"
    )
    assert resp.status is ToolStatus.SUCCESS


async def test_bash_without_approver_raises():
    toolkit = Toolkit(tools=[BashTool()])
    with pytest.raises(RuntimeError, match="approver"):
        await toolkit.call_tool("bash", command="echo x")


async def test_denied_command_does_not_run(tmp_path: Path):
    marker = tmp_path / "marker.txt"
    toolkit = Toolkit(tools=[BashTool()], approver=AutoApprover(False))
    resp = await toolkit.call_tool(
        "bash",
        command=f"\"{PY}\" -c \"open(r'{marker}','w').write('x')\"",
    )
    assert resp.status is ToolStatus.DENIED
    assert not marker.exists()  # 被拒绝就根本没执行
