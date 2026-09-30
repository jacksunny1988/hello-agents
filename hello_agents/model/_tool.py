"""model 包内的最小工具协议。

与旧包 `hello_agents/tools/`（`BaseTool`：同步 `run` + `arun`）并存、互不依赖。
那套面向「工具怎么被 Agent 调用」，这套面向「工具怎么被模型调用」——只需要一份
JSON Schema 和一个异步入口。

典型用法：
    class GetWeather(Tool):
        name = "get_weather"
        description = "查询某城市当前天气"
        parameters: ClassVar[dict[str, Any]] = GetWeatherParams.model_json_schema()

        async def run(self, arguments: dict) -> object:
            return {"temp": 22}

`parameters` 是 dict（可变），子类**必须**带上 `ClassVar[...]` 标注——否则 ruff 的
RUF012 会报「可变类属性」；这也是仓库既有写法（见 `memory/rag/document.py`）。
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

from .message import Message, Role, ToolCallBlock, ToolResultBlock

logger = logging.getLogger(__name__)

# auto（模型自己决定）/ none（不调）/ required（必须调），
# 或 {"type":"function","function":{"name":"..."}}（强制调某个）
ToolChoice = Literal["auto", "none", "required"] | dict[str, Any]


class Tool(ABC):
    """一个可被模型调用的工具。

    子类声明 `name` / `description` / `parameters` 三个类属性并实现 `run`；
    `function_spec()` 由基类给具体实现，子类不用重复写。

    `parameters` 是**现成的 JSON Schema dict**，不做「传 Pydantic 模型自动推导」
    那层魔法——两套机制并存只会让人猜哪套生效。配方写在这里：
    `parameters = MyParams.model_json_schema()`。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]

    def function_spec(self) -> dict:
        """产出 `chat.completions.create(tools=...)` 数组里的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> object:
        """执行工具。返回可 JSON 序列化的结果；返回 `str` 时原样使用。

        抛出的异常由 `execute_tool_calls` 捕获并转成 is_error 结果，不会中断闭环。
        """


async def execute_tool_calls(
    tool_calls: Sequence[ToolCallBlock], tools: Mapping[str, Tool]
) -> list[Message]:
    """执行模型请求的工具调用，产出可直接拼进消息历史的消息列表。

    每个调用**独立兜底**：未知工具、参数解析失败、工具抛错都转成 `is_error=True`
    的结果，不会让整批崩掉——闭环断了比一次工具失败严重得多。

    并发执行（`asyncio.gather`），返回顺序与入参一致：工具之间无依赖是模型发起
    并行调用的前提，串行只会让总耗时累加。
    """
    return list(await asyncio.gather(*(_run_one(call, tools) for call in tool_calls)))


async def _run_one(tool_call: ToolCallBlock, tools: Mapping[str, Tool]) -> Message:
    output, is_error = await _invoke(tool_call, tools)
    return Message(
        role=Role.TOOL,
        content=[
            ToolResultBlock(
                tool_call_id=tool_call.id,
                output=output,
                is_error=is_error,
                name=tool_call.name or None,
            )
        ],
    )


async def _invoke(
    tool_call: ToolCallBlock, tools: Mapping[str, Tool]
) -> tuple[str, bool]:
    """跑一个工具，返回 `(回灌文本, 是否失败)`。任何异常都不外泄。"""
    tool = tools.get(tool_call.name)
    if tool is None:
        return f"未知工具 {tool_call.name!r}", True

    try:
        arguments = json.loads(tool_call.arguments)
    except json.JSONDecodeError as exc:
        return f"参数不是合法 JSON：{exc}", True
    except RecursionError:
        # 超深嵌套的 JSON 会让 json.loads 撞上解释器的递归上限。规格 §5.2 承诺
        # 「任何失败都转成 is_error」，这条路径漏了就等于承诺失效——而且是唯一
        # 会让一个坏调用炸掉整批的路径。
        return "参数嵌套层级过深，无法解析", True

    if not isinstance(arguments, dict):
        return f"参数必须是 JSON 对象，收到 {type(arguments).__name__}", True

    try:
        result = await tool.run(arguments)
    except Exception as exc:
        # 给模型看简短文本，完整栈进日志：把 traceback 塞进回灌消息既浪费
        # token 又干扰模型，但排查时又必须能看到。
        logger.exception("工具 %s 执行失败", tool_call.name)
        return f"{type(exc).__name__}: {exc}", True

    return _to_output(result)


def _to_output(result: object) -> tuple[str, bool]:
    if isinstance(result, str):
        return result, False
    try:
        return json.dumps(result, ensure_ascii=False), False
    except (TypeError, ValueError) as exc:
        return f"工具返回值无法序列化为 JSON：{exc}", True
    except RecursionError:
        # 同 `_invoke`：json.dumps 对超深嵌套对象也会撞递归上限。
        return "工具返回值嵌套层级过深，无法序列化", True
