import asyncio
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse, ToolStatus


class EchoTool(ToolBase):
    name = "echo"
    description = "原样返回传入的文本"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


async def main() -> None:
    tool = EchoTool()
    resp = await tool(text="hello tool")
    print(resp.status is ToolStatus.SUCCESS, resp.get_text())
    print(tool.get_function_schema())


asyncio.run(main())
