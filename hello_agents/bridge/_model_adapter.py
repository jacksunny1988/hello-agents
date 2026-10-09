from collections.abc import Sequence

from hello_agents.model._tool import Tool as ModelTool
from hello_agents.tool._base import ToolBase
from hello_agents.tool._toolkit import Toolkit


class ToolkitModelTool(ModelTool):
    """把 tool 子系统的 ToolBase 包装成 model 层 Tool。

    仅用于 function_spec() 向模型导出 schema；执行走 bridge.run_tool_call，
    以保留审批/重试/超时/Hook/MCP 等治理。
    """

    def __init__(self, tool: ToolBase) -> None:
        self.name = tool.name
        self.description = tool.description
        self.parameters = tool.input_schema
        self._tool = tool

    async def run(self, arguments: dict) -> str:
        # 正常主循环不经过这里（执行用 run_tool_call）；保留转发避免误用。
        response = await self._tool(**arguments)
        return response.get_text()


def model_tools_for(toolkit: Toolkit) -> Sequence[ToolkitModelTool]:
    """导出 Toolkit 中全部工具的 model 层描述壳。"""
    return [ToolkitModelTool(tool) for tool in toolkit.list_tools()]
