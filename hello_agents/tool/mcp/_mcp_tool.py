import re
from typing import Any

import mcp.types

from .._base import ToolBase
from .._response import ToolResponse


class MCPTool(ToolBase):
    """把一个 MCP 远程工具适配成 hello-agents 的 ToolBase。

    name 命名空间化（`mcp__<server>__<tool>`）以避免多个 server 之间的重名冲突；
    注意 `super().__init__()` 必须在属性赋值之后——它会校验 `input_schema`。

    另注意：mcp SDK 的模型字段是 **snake_case**（`input_schema` / `read_only_hint`
    / `is_error`），camelCase 只是 JSON 序列化别名。按 wire 名取属性会 AttributeError。
    """

    def __init__(
        self,
        server_name: str,
        raw_tool: mcp.types.Tool,
        session: Any,
    ) -> None:
        self._raw_name = raw_tool.name
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "x", raw_tool.name)
        self.name = f"mcp__{server_name}__{sanitized}"
        self.description = raw_tool.description or ""

        schema = dict(raw_tool.input_schema) if raw_tool.input_schema else {}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        self.input_schema = schema

        annotations = raw_tool.annotations
        self.is_read_only = bool(
            annotations is not None and getattr(annotations, "read_only_hint", False)
        )

        self._session = session
        super().__init__()

    async def call(self, **kwargs: Any) -> ToolResponse:
        result = await self._session.call_tool(self._raw_name, arguments=kwargs)

        texts = [
            block.text
            for block in result.content
            if isinstance(block, mcp.types.TextContent)
        ]
        text = "\n".join(texts)

        if result.is_error:
            return ToolResponse.fail(text or "MCP 工具返回错误")
        return ToolResponse.succeed(text or "(无输出)")
