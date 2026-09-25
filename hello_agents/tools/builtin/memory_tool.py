"""记忆工具

让 Agent 具备读写记忆的能力：remember / recall / forget 三个动作，
底层由 MemoryManager 统一调度四类记忆。
"""

import asyncio
import json
from typing import Any, Self

from ...memory.base import MemoryType
from ...memory.manager import MemoryManager
from ..base import BaseTool, ToolParameter
from ..response import ToolResponse

_ACTIONS = {"remember", "recall", "forget"}


class MemoryTool(BaseTool):
    """记忆工具（Agent 记忆能力）"""

    def __init__(self, manager: MemoryManager | None = None):
        super().__init__(
            name="memory",
            description=(
                "记忆工具：读写 Agent 的长期记忆。"
                "action=remember 保存内容；action=recall 检索相关记忆；"
                "action=forget 删除指定记忆。"
            ),
        )
        self.manager = manager or MemoryManager()
        self._owns_manager = manager is None

    def run(self, input_data: Any = None, **kwargs) -> ToolResponse:
        """执行记忆操作

        input_data 支持三种形式：dict、JSON 字符串、纯文本
        （纯文本默认按 action=recall 检索）。
        """
        params = self._parse_input(input_data, kwargs)
        action = params.get("action", "recall")

        if action not in _ACTIONS:
            return ToolResponse.error(
                code="INVALID_PARAM",
                message=f"未知 action: {action}，可选 {sorted(_ACTIONS)}",
            )

        try:
            if action == "remember":
                return self._do_remember(params)
            if action == "recall":
                return self._do_recall(params)
            return self._do_forget(params)
        except Exception as error:  # 记忆后端异常不应击穿 Agent 循环
            return ToolResponse.error(code="MEMORY_ERROR", message=str(error))

    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="action",
                type="str",
                description="操作类型: remember / recall / forget",
                required=True,
            ),
            ToolParameter(
                name="content",
                type="str",
                description="要记住的内容（action=remember 时必填）",
                required=False,
            ),
            ToolParameter(
                name="query",
                type="str",
                description="检索关键词（action=recall 时使用，缺省用 content）",
                required=False,
            ),
            ToolParameter(
                name="memory_type",
                type="str",
                description="记忆类型: working / episodic / semantic / perceptual",
                required=False,
                default="working",
            ),
            ToolParameter(
                name="limit",
                type="int",
                description="返回条数上限",
                required=False,
                default=5,
            ),
        ]

    def _do_remember(self, params: dict[str, Any]) -> ToolResponse:
        content = params.get("content")
        if not content:
            return ToolResponse.error(
                code="INVALID_PARAM", message="action=remember 需要提供 content"
            )
        memory_type = self._memory_type_of(params)
        item_id = asyncio.run(
            self.manager.remember(
                content,
                memory_type=memory_type,
                metadata=params.get("metadata"),
                ttl_seconds=params.get("ttl_seconds"),
            )
        )
        return ToolResponse.success(
            text=f"已记住（{memory_type.value}）: {item_id}",
            data={"id": item_id, "memory_type": memory_type.value, "content": content},
        )

    def _do_recall(self, params: dict[str, Any]) -> ToolResponse:
        query = params.get("query") or params.get("content") or ""
        limit = int(params.get("limit", 5))
        memory_types = params.get("memory_types")
        hits = asyncio.run(
            self.manager.recall(
                query,
                memory_types=memory_types,
                limit=limit,
                filters=params.get("filters"),
            )
        )
        if not hits:
            return ToolResponse.success(
                text=f"未检索到与「{query}」相关的记忆。", data={"hits": []}
            )
        lines = [
            f"[{i}] ({item.memory_type.value}) {item.content}"
            for i, item in enumerate(hits, 1)
        ]
        return ToolResponse.success(
            text="\n".join(lines),
            data={"hits": [item.to_dict() | {"score": item.score} for item in hits]},
        )

    def _do_forget(self, params: dict[str, Any]) -> ToolResponse:
        item_id = params.get("id") or params.get("content")
        if not item_id:
            return ToolResponse.error(
                code="INVALID_PARAM", message="action=forget 需要提供 id"
            )
        deleted = asyncio.run(self.manager.forget(str(item_id)))
        return ToolResponse.success(
            text=f"已删除记忆 {item_id}" if deleted else f"记忆 {item_id} 不存在",
            data={"id": item_id, "deleted": deleted},
        )

    @staticmethod
    def _memory_type_of(params: dict[str, Any]) -> MemoryType:
        raw = params.get("memory_type", "working")
        try:
            return MemoryType(raw)
        except ValueError:
            raise ValueError(
                f"未知记忆类型: {raw}，可选 {[t.value for t in MemoryType]}"
            )

    @staticmethod
    def _parse_input(input_data: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """把 input_data 规整为参数字典（dict / JSON / 纯文本）"""
        params: dict[str, Any] = dict(kwargs)
        if input_data is None:
            return params
        if isinstance(input_data, dict):
            params.update(input_data)
            return params
        text = str(input_data).strip()
        if text.startswith("{"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                params.setdefault("content", text)
            else:
                if not isinstance(loaded, dict):
                    params.setdefault("content", text)
                else:
                    params.update(loaded)
            return params
        params.setdefault("action", "recall")
        params.setdefault("content", text)
        return params

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_manager:
            self.manager.close()
