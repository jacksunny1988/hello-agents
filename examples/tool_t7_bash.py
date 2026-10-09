"""T7 演示：bash 工具（子进程执行 · 退出码 · 超时杀进程 · 审批）。

默认用 AutoApprover(True) 自动放行，保证非交互、可复现；
想手动体验审批流程，把 approver 换成 ConsoleApprover() 即可。
"""

import asyncio
import sys

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.builtin._bash import BashTool

# 用当前解释器跑 python -c，避免 Windows(cmd)/POSIX(sh) 的命令差异
PY = sys.executable


async def main() -> None:
    toolkit = Toolkit(tools=[BashTool()], approver=AutoApprover(True))

    print("=== 1) 打印工作目录（成功） ===")
    resp = await toolkit.call_tool(
        "bash",
        command=f'"{PY}" -c "import os; print(os.getcwd())"',
        description="打印子进程的工作目录",
    )
    print(f"[{resp.status}] {resp.get_text().strip()}")

    print("\n=== 2) 成功命令：退出码 0 ===")
    resp = await toolkit.call_tool(
        "bash",
        command=f'"{PY}" -c "print(\'hello from bash tool\')"',
        description="打印一行问候",
    )
    print(f"[{resp.status}] {resp.get_text().strip()}")

    print("\n=== 3) 故意失败：退出码 3，stderr 被带回来 ===")
    resp = await toolkit.call_tool(
        "bash",
        command=f'"{PY}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"',
        description="故意以退出码 3 失败",
    )
    print(f"[{resp.status}] {resp.get_text().strip()}")

    print("\n=== 4) 超时：子进程被强制终止（不会等满 30 秒） ===")
    toolkit.get_tool("bash").timeout = 0.5
    resp = await toolkit.call_tool(
        "bash",
        command=f'"{PY}" -c "import time; time.sleep(30)"',
        description="睡 30 秒，会被 0.5 秒超时打断",
    )
    print(f"[{resp.status}] {resp.get_text().strip()}")


asyncio.run(main())
