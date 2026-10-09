import asyncio
import tempfile
from pathlib import Path

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.builtin._fs import ReadFileTool, WriteFileTool
from hello_agents.tool.builtin._search import GlobTool, GrepTool

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    # 只读工具免确认；write_file 是写操作，用 AutoApprover 放行避免交互
    toolkit = Toolkit(
        tools=[ReadFileTool(), WriteFileTool(), GlobTool(), GrepTool()],
        approver=AutoApprover(True),
    )

    print("=== glob: hello_agents/tool/**/*.py（前 5 条）===")
    glob_resp = await toolkit.call_tool(
        "glob", pattern="hello_agents/tool/**/*.py", path=str(PROJECT_ROOT)
    )
    print("\n".join(glob_resp.get_text().splitlines()[:5]))

    print("\n=== grep: class Toolkit（include=*.py）===")
    grep_resp = await toolkit.call_tool(
        "grep", pattern="class Toolkit", path=str(PROJECT_ROOT), include="*.py"
    )
    print(grep_resp.get_text())

    print("\n=== read_file: _toolkit.py 前 5 行 ===")
    read_resp = await toolkit.call_tool(
        "read_file",
        file_path=str(PROJECT_ROOT / "hello_agents" / "tool" / "_toolkit.py"),
        limit=5,
    )
    print(read_resp.get_text())

    print("\n=== write_file: 写入临时目录 ===")
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "demo" / "note.txt"
        write_resp = await toolkit.call_tool(
            "write_file", file_path=str(target), content="hello from tool_t6\n"
        )
        print(write_resp.get_text())
        print("磁盘内容:", repr(target.read_text(encoding="utf-8")))


asyncio.run(main())
