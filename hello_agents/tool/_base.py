from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ._executor import execute_tool
from ._response import ToolResponse


class ToolBase(ABC):
    """所有工具的统一抽象。工具作者：声明类属性 + 实现 `call`（def 或 async def）。

    横切逻辑（sync/async 适配、超时、取消、异常兜底、hook、确认、重试）
    不在本类暴露给作者，统一在 `__call__` 收口（T2 执行收口；T5 挂 hook/确认/重试）。
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    is_read_only: ClassVar[bool] = False
    is_concurrency_safe: ClassVar[bool] = True
    timeout: ClassVar[float | None] = None

    requires_confirmation: ClassVar[bool | None] = None
    max_retries: ClassVar[int] = 0
    retry_backoff: ClassVar[float] = 0.1

    def __init__(self) -> None:
        self._validate_input_schema()

    def _validate_input_schema(self) -> None:
        """input_schema 必须是「对象 + properties」，否则在实例化期就报错。

        无参工具也合法：properties 为空 dict、required 为空列表。
        """
        schema = self.input_schema
        if not (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
        ):
            raise ValueError(
                f"工具 {self.name!r} 的 input_schema 必须是 type='object' 且含 "
                f"properties 的 JSON Schema，收到：{schema!r}"
            )

    @abstractmethod
    def call(self, **kwargs: Any) -> ToolResponse:
        """业务逻辑。入参以关键字参数传入（即 input_schema 声明的字段）。

        可写同步 `def`（框架自动丢线程池）或 `async def`。
        """

    async def __call__(self, **kwargs: Any) -> ToolResponse:
        """框架统一入口：sync/async 归一、超时、异常兜底（见 _executor.execute_tool）。"""
        return await execute_tool(self, kwargs, timeout=self.timeout)

    def get_function_schema(self) -> dict[str, Any]:
        """产出 OpenAI function 形式的 schema（不依赖 model 层，纯 dict）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
