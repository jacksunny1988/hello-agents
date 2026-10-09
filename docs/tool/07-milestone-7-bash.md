# 里程碑 T7：bash 工具（子进程执行 · 退出码 · 超时/取消杀进程 · 审批）

> 目标：实现最小但正确的 shell 命令工具。本里程碑只做"执行一条命令、收输出、按退出码
> 判定成败、超时/取消时清理子进程、走人工确认"。
> 对标（只读）：`agentscope/tool/_builtin/_bash.py`、`_bash_parser.py`。

---

## 7.1 设计原理（先理解）

### 1) 为什么用 `asyncio.create_subprocess_shell`

- 它在事件循环里异步起子进程，等待期间不阻塞循环；
- `shell` 版本会把命令交给平台 shell 解释（支持管道、重定向、内建命令）；
  平台默认 shell：Windows 为 `cmd.exe`，POSIX 为 `/bin/sh`。
- 若想强制 bash，可给 `executable="/bin/bash"`（本里程碑不强制，默认即可）。

> 跨平台提示：测试不要依赖 `ls`/`dir` 这类平台差异命令。最稳的是用当前解释器
> `sys.executable` 跑 `python -c ...`，Windows/Ubuntu 行为一致。

### 2) 核心新知识点：子进程的生命周期——必须有人"收尸"

这是 bash 区别于前面所有工具、也最容易出 bug 的地方。

当**超时（T2 wait_for）或外部取消**发生时，`await proc.communicate()` 会收到
`CancelledError`。如果此时不处理，子进程会变成**孤儿进程**：框架已经不等它了，
但它在后台继续运行（尤其 `sleep`、起服务、占端口/文件锁）。

所以 `call` 内必须：

```python
try:
    stdout, stderr = await proc.communicate()
except asyncio.CancelledError:
    proc.kill()        # 终止子进程
    await proc.wait()  # 回收，避免僵尸进程
    raise              # 继续向上传播取消（不吞）
```

> ⚠️ **Windows 上只 `kill()` 不够。** `create_subprocess_shell` 在 Windows 上先起一个
> `cmd.exe`、再由它去跑真正的命令，`proc` 指向的是那个 `cmd.exe`。`proc.kill()` 只
> 终止 cmd.exe，真正在执行的命令作为孙进程会继续跑完（实测：命令"睡 3 秒后写文件"
> 在被 kill 后仍然写出了文件，`await proc.wait()` 也一直等满 3 秒）。
> 所以 Windows 下必须用 `taskkill /F /T /PID <pid>` 连整棵进程树一起杀；
> POSIX 的 `sh -c` 通常把自己 exec 成命令本身，直接 kill 即可。见 7.2 的 `_terminate`。

完整时序（超时场景）：

```
wait_for 超时
  → 给内部 awaitable 发 CancelledError
  → call 的 communicate 收到 → proc.kill() + wait() → re-raise CancelledError
  → wait_for 据此对外抛 TimeoutError
  → execute_tool except TimeoutError → ToolResponse.fail("超时")
```

即：**超时后你拿到 ERROR 结果，且子进程已被清理**。普通取消（task.cancel）则在
kill/wait 后让 CancelledError 正常传播（与 T2/T5 结论一致）。

> `proc.kill()` 与 `terminate()`：kill 对应 SIGKILL（Windows 上 TerminateProcess，
> 二者等价），terminate 对应 SIGTERM。学习场景直接 kill 最确定。

### 3) 成功/失败如何判定：看退出码

- `returncode == 0`：成功。stdout 是主输出；有些程序（如 git）正常信息走 stderr，
  因此成功时把 stdout+stderr 都返回；
- `returncode != 0`：失败，返回 fail，文本含退出码与 stderr（或 stdout）；
- 无任何输出：返回 "(无输出)"，避免模型拿到空字符串无法判断是否执行。

### 4) 审批与参数

- bash 有副作用：`is_read_only=False`、`is_concurrency_safe=False`，默认触发 T5 人工确认；
- `description` 参数不参与执行，只给审批回调展示"这条命令要做什么"；
- 超时复用 T2 的 `timeout` 类属性（秒，默认 30），不另设命令级毫秒 timeout；
- 构造时可传 `cwd` 指定工作目录（传给子进程），默认继承当前进程目录。

### 5) 裁剪范围（本里程碑不做）

- `_bash_parser.py`（749 行）命令解析；
- 危险命令检测（如 `rm /`、删除家目录/根目录的判定）——我们靠人工确认兜底；
- 工作目录在多次调用间持久化（agentscope 维持 shell cwd）——每次都是新进程；
- 流式增量输出（我们一次 `communicate` 收完）；
- shell profile 初始化。

---

## 7.2 接口契约：填充 `builtin/_bash.py`

### 字段说明

| 成员 | 种类 | 说明 |
|---|---|---|
| command | 入参（必填） | 要执行的 shell 命令 |
| description | 入参（默认 ""） | 命令用途，供审批参考 |
| cwd | 构造参数 | 子进程工作目录，默认 None |
| timeout | 类属性，默认 30.0 秒 | 复用 T2 超时 |

### 完整代码

```python
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
```

> 说明：`call` 是 `async def`（T2 直接 await、不进线程池）；子进程本身并行于事件循环，
> 不会阻塞它。

---

## 7.3 留给你的动手任务

### 1) 填充 `builtin/_bash.py`。

### 2) 写测试 `tests/test_tool_t7.py`（离线、确定，用 `sys.executable` 避免 shell 差异）：

```python
import asyncio
import sys
from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._response import ToolStatus
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.builtin._bash import BashTool

PY = sys.executable


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
        command=f'"{PY}" -c "open(r\'{marker}\',\'w\').write(\'x\')"',
    )
    assert resp.status is ToolStatus.DENIED
    assert not marker.exists()  # 被拒绝就根本没执行
```

> 补充（实现后追加）：`test_timeout_really_kills_the_child` /
> `test_cancel_really_kills_the_child` 用「睡 2 秒再写文件」的命令验证子进程**真的**
> 被终止。只断言返回 ERROR 是不够的——上面那两条 `test_timeout_kills_and_returns_error`
> / `test_cancel_kills_and_propagates` 在修好 Windows 杀进程之前**就是绿的**，
> 只是各耗时 30 秒：`wait_for` 确实按时抛了超时，但子进程活得好好的。

> Windows 下 `sys.executable` 路径可能含空格或反斜杠，命令里已用双引号包裹；
> 若个别用例因引号转义失败，可改用 `create_subprocess_exec(PY, "-c", "...")` 的
> 等价写法验证（exec 版不经 shell、无需转义），但工具本体保持 shell 版本。

### 3) 写演示 `examples/tool_t7_bash.py`：

- 配 `ConsoleApprover`（交互）或 `AutoApprover(True)`（自动）；
- 跑 2–3 条命令：打印工作目录、一个成功命令、一个故意失败命令，观察输出与退出码。

---

## 7.4 验收清单

- [x] `_bash.py` 与契约一致（含 CancelledError 中 kill/wait；Windows 用 `taskkill /T` 杀整棵进程树）；
- [x] test_tool_t7 全绿（成功/退出码/stderr/无输出/超时/取消/cwd/审批/拒绝不执行/超时·取消后子进程确实死掉）；
- [x] T1–T6 回归全绿（138 passed）；
- [x] ruff 干净：

```powershell
uv run ruff check hello_agents/tool tests/test_tool_t7.py examples/tool_t7_bash.py
uv run ruff format --check hello_agents/tool tests/test_tool_t7.py examples/tool_t7_bash.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py tests/test_tool_t7.py -q
```

---

## 7.5 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| create_subprocess_shell | `_backend.py` LocalBackend.exec_shell（create_subprocess_exec） | 我们用 shell 变体；agentscope 经 backend 抽象 |
| CancelledError kill/wait | backend 超时处理（exit_code=-1） | 我们显式 kill 收尸 |
| 退出码判定 | `_bash.py` call 末尾、超时 exit_code=-1 | 语义对应 |
| 审批 | permission 引擎 + 危险命令检测 | 简化为 T5 approver；裁剪 bash_parser/危险路径 |
| cwd | Bash(cwd=...) | 保留构造参数；不做跨调用持久化 |

> 进阶思考：
> ① 若要流式回传长命令输出，可改为读 `proc.stdout` 逐行 yield（async generator），
>   并把 T2/T5 改成支持流式——即 agentscope 的形态；
> ② 若要加危险命令拦截，可在执行前做最轻量的字符串/前缀判断（如命令 strip 后 ==
>   `rm -rf /`），不必引入完整解析器；
> ③ 想强制 bash/profile，给 create_subprocess_shell 传 `executable="/bin/bash"`。

---

## 完成后

把 `_bash.py`、tool_t7 输出与 pytest 结果贴给我 review。
通过后进入 **T8：MCP 接入（stdio）——mcp_servers.yaml 声明 + uvx 真实 server**。
