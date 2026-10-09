import asyncio
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._governance import (
    ConsoleApprover,
    ToolHook,
)
from hello_agents.tool._response import ToolResponse
from hello_agents.tool._toolkit import Toolkit


class EchoTool(ToolBase):
    """只读：应免确认。"""

    name = "echo"
    description = "回显"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


class WriteNoteTool(ToolBase):
    """写操作：默认需确认（这里用 ConsoleApprover 真人交互）。"""

    name = "write_note"
    description = "写入一条笔记"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    }

    async def call(self, *, content: str) -> ToolResponse:
        return ToolResponse.succeed(f"已写入：{content}")


class FlakyTool(ToolBase):
    """前两次失败、第三次成功：演示重试。"""

    name = "flaky"
    description = "不稳定工具"
    max_retries: ClassVar[int] = 2
    retry_backoff: ClassVar[float] = 0.05
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    attempts: ClassVar[int] = 0

    async def call(self) -> ToolResponse:
        type(self).attempts += 1
        if type(self).attempts < 3:
            return ToolResponse.fail("transient error")
        return ToolResponse.succeed("ok")


class LoggingHook(ToolHook):
    def before(self, tool, kwargs):
        print(f"[log] before {tool.name} kwargs={kwargs}")

    def after(self, tool, kwargs, response):
        print(f"[log] after {tool.name} status={response.status}")


async def main() -> None:
    toolkit = Toolkit(tools=[EchoTool(), WriteNoteTool(), FlakyTool()])
    toolkit.add_hook(LoggingHook())

    # 只读免确认（approver 配不配对它无所谓）
    print((await toolkit.call_tool("echo", text="hi")).get_text())

    # 写操作：交互审批；想自动跑可换成 AutoApprover(True)
    toolkit.set_approver(ConsoleApprover())
    print((await toolkit.call_tool("write_note", content="hello")).get_text())

    # 重试：最终成功
    FlakyTool.attempts = 0
    print((await toolkit.call_tool("flaky")).get_text())


asyncio.run(main())
