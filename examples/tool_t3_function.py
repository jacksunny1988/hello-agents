import asyncio

from hello_agents.tool._adapters import FunctionTool


def add(a: int, b: int = 2) -> int:
    """两数相加。

    Args:
        a: 第一个加数。
        b: 第二个加数，默认 2、非必填。
    """
    return a + b


async def greet(name: str) -> str:
    """向某人打招呼。

    Args:
        name: 要问候的名字。
    """
    return f"hello {name}"


def list_user() -> dict:
    """返回一个字典，验证返回值 JSON 归一。"""
    return {"id": 1, "tags": ["x", "y"]}


async def main() -> None:
    add_tool = FunctionTool(add, is_read_only=True)
    print(add_tool.name)
    print(add_tool.description)
    print(add_tool.input_schema)
    print((await add_tool(a=1)).get_text())  # 3（b 用默认值）
    print((await add_tool(a=1, b=5)).get_text())  # 6

    greet_tool = FunctionTool(greet)
    print((await greet_tool(name="world")).get_text())

    user_tool = FunctionTool(list_user)
    print((await user_tool()).get_text())  # JSON 字符串


asyncio.run(main())
