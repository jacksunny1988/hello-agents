import asyncio
import os
import subprocess
from typing import Any, ClassVar

from .._base import ToolBase
from .._response import ToolResponse


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """终止子进程及其后代。

    Windows 上 `create_subprocess_shell` 会先起一个 `cmd.exe`、再由它去跑真正的
    命令，只 `kill()` 这个 cmd.exe 会留下仍在后台执行的命令（孤儿进程），所以必须
    用 `taskkill /T` 连整棵进程树一起杀。POSIX 下 `sh -c` 通常把自己 exec 成命令
    本身，直接 kill 即可。

    taskkill 是一次约 0.2s 的同步调用，只出现在超时/取消的收尾路径上。
    """
    if os.name == "nt" and proc.pid is not None:
        try:
            done = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        except OSError:
            done = None
        if done is not None and done.returncode == 0:
            return
    proc.kill()


class BashTool(ToolBase):
    name = "bash"
    description = (
        "执行一条 shell 命令并返回输出；退出码非 0 返回错误。"
        "有副作用，执行前需人工确认。"
    )
    is_read_only = False
    is_concurrency_safe = False
    timeout: ClassVar[float | None] = 30.0

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "description": {
                "type": "string",
                "description": "这条命令做什么（供审批参考）",
            },
        },
        "required": ["command"],
    }

    def __init__(self, cwd: str | None = None) -> None:
        super().__init__()
        self._cwd = cwd

    async def call(self, command: str, description: str = "") -> ToolResponse:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )

        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            # 超时/取消：杀掉子进程（Windows 下连同 shell 包出的后代）并回收，
            # 杜绝孤儿/僵尸进程
            _terminate(proc)
            await proc.wait()
            raise

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        code = proc.returncode

        if code == 0:
            body = out + err
            return ToolResponse.succeed(body if body.strip() else "(无输出)")

        return ToolResponse.fail(
            f"命令退出码 {code}\n{err or out}",
            command=command,
        )
