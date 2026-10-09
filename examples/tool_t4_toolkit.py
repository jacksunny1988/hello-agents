import asyncio
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse
from hello_agents.tool._toolkit import Toolkit


class EchoTool(ToolBase):
    name = "echo"
    description = "回显"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    is_read_only = True

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


def add(a: int, b: int = 1) -> int:
    """两数相加。

    Args:
        a: 第一个数。
        b: 第二个数。
    """
    return a + b


async def main() -> None:
    echo = EchoTool()
    toolkit = Toolkit(tools=[echo])
    toolkit.register_function(add, group="math", is_read_only=True)

    print(toolkit.list_groups())  # ['basic', 'math']
    print([t.name for t in toolkit.list_tools()])  # ['echo', 'add']
    print([t.name for t in toolkit.list_tools(groups=["math"])])  # ['add']
    print(len(toolkit.get_tool_schemas()))  # 2
    print((await toolkit.call_tool("echo", text="hi")).get_text())
    print((await toolkit.call_tool("add", a=2)).get_text())  # 3（b 默认 1）


asyncio.run(main())
