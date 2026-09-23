"""笔记工具

让 Agent 在长时程任务中读写结构化笔记：create / read / update / delete /
list / search / summary 七个动作，底层是 NoteStore。
"""

import json
import re
from typing import Any

from ...notes import NoteError, NoteNotFoundError, NoteStore, NoteType
from ..base import BaseTool, ToolParameter
from ..response import ToolResponse

_ACTIONS = {"create", "read", "update", "delete", "list", "search", "summary"}
_UPDATE_FIELDS = ("title", "body", "type", "tags")


def _invalid(message: str) -> ToolResponse:
    """参数错误响应"""
    return ToolResponse.error(code="INVALID_PARAM", message=message)


def _as_tags(value: Any) -> list[str] | None:
    """把入参 tags 规整为列表：字符串按逗号/空白切分（LLM 常这么传）"""
    if value is None:
        return None
    if isinstance(value, str):
        return [item for item in re.split(r"[,，\s]+", value) if item]
    return [str(item) for item in value]


class NoteTool(BaseTool):
    """结构化笔记工具（长时程任务的外部记忆）"""

    def __init__(self, store: NoteStore | None = None):
        super().__init__(
            name="note",
            description=(
                "笔记工具：读写结构化笔记。action=create 新建；read 读取；"
                "update 更新；delete 删除；list 列出；search 检索；summary 取全库摘要。"
            ),
        )
        self.store = store or NoteStore()

    def run(self, input_data: Any = None, **kwargs) -> ToolResponse:
        """执行笔记操作

        input_data 支持三种形式：dict、JSON 字符串、纯文本
        （纯文本默认按 action=search 检索）。
        """
        params = self._parse_input(input_data, kwargs)
        action = params.get("action", "search")
        if action not in _ACTIONS:
            return _invalid(f"未知 action: {action}，可选 {sorted(_ACTIONS)}")
        try:
            return getattr(self, f"_do_{action}")(params)
        except NoteNotFoundError as error:
            return ToolResponse.error(code="NOT_FOUND", message=str(error))
        except Exception as error:  # 文件系统故障不应击穿 Agent 循环
            return ToolResponse.error(code="NOTE_ERROR", message=str(error))

    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="action",
                type="str",
                description="操作类型: create / read / update / delete / list / search / summary",
            ),
            ToolParameter(
                name="id",
                type="str",
                description="笔记 id（read / update / delete 必填）",
                required=False,
            ),
            ToolParameter(
                name="title",
                type="str",
                description="标题（create 必填）",
                required=False,
            ),
            ToolParameter(
                name="body",
                type="str",
                description="Markdown 正文",
                required=False,
            ),
            ToolParameter(
                name="type",
                type="str",
                description="笔记类型: task_state / decision / blocker / finding / general",
                required=False,
            ),
            ToolParameter(
                name="tags",
                type="list",
                description="标签列表，也接受逗号分隔的字符串",
                required=False,
            ),
            ToolParameter(
                name="query",
                type="str",
                description="检索关键词（search 必填）",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="int",
                description="返回条数上限",
                required=False,
                default=10,
            ),
        ]

    def _do_create(self, params: dict[str, Any]) -> ToolResponse:
        title = params.get("title")
        if not title:
            return _invalid("action=create 需要提供 title")
        note = self.store.create(
            title=str(title),
            body=str(params.get("body") or ""),
            type=str(params.get("type") or NoteType.GENERAL),
            tags=_as_tags(params.get("tags")),
        )
        return ToolResponse.success(
            text=f"已创建笔记 {note.id}：《{note.title}》", data=note.to_dict()
        )

    def _do_read(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=read 需要提供 id")
        note = self.store.read(str(note_id))
        header = f"[{note.type}] {note.title} ({note.id}) 更新于 {note.updated_at.isoformat()}"
        return ToolResponse.success(
            text=f"{header}\n\n{note.body}", data=note.to_dict() | {"body": note.body}
        )

    def _do_update(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=update 需要提供 id")
        fields: dict[str, Any] = {}
        for name in _UPDATE_FIELDS:
            value = params.get(name)
            if value is None:
                continue
            fields[name] = _as_tags(value) if name == "tags" else value
        if not fields:
            return _invalid(f"action=update 至少需要提供 {list(_UPDATE_FIELDS)} 之一")
        note = self.store.update(str(note_id), **fields)
        return ToolResponse.success(text=f"已更新笔记 {note.id}", data=note.to_dict())

    def _do_delete(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=delete 需要提供 id")
        deleted = self.store.delete(str(note_id))
        text = f"已删除笔记 {note_id}" if deleted else f"笔记 {note_id} 不存在"
        return ToolResponse.success(
            text=text, data={"id": str(note_id), "deleted": deleted}
        )

    def _do_list(self, params: dict[str, Any]) -> ToolResponse:
        notes = self.store.list(
            type=params.get("type"),
            tags=_as_tags(params.get("tags")),
            limit=params.get("limit"),
        )
        if not notes:
            return ToolResponse.success(text="暂无笔记。", data={"notes": []})
        lines = [
            f"[{meta.type}] {meta.title} ({meta.id}, {meta.updated_at.isoformat()})"
            for meta in notes
        ]
        return ToolResponse.success(
            text="\n".join(lines), data={"notes": [meta.to_dict() for meta in notes]}
        )

    def _do_search(self, params: dict[str, Any]) -> ToolResponse:
        query = params.get("query") or params.get("content")
        if not query:
            return _invalid("action=search 需要提供 query")
        hits = self.store.search(str(query), limit=int(params.get("limit") or 10))
        if not hits:
            return ToolResponse.success(
                text=f"未检索到与「{query}」相关的笔记。", data={"hits": []}
            )
        lines = [
            f"[{meta.type}] {meta.title} ({meta.id}, {value:.2f})"
            for meta, value in hits
        ]
        return ToolResponse.success(
            text="\n".join(lines),
            data={"hits": [meta.to_dict() | {"score": value} for meta, value in hits]},
        )

    def _do_summary(self, params: dict[str, Any]) -> ToolResponse:
        summaries = self.store.summary(
            type=params.get("type"),
            tags=_as_tags(params.get("tags")),
            limit=params.get("limit"),
        )
        if not summaries:
            return ToolResponse.success(text="暂无笔记。", data={"notes": []})
        lines: list[str] = []
        for item in summaries:
            lines.append(f"[{item.meta.type}] {item.meta.title} ({item.meta.id})")
            for section in item.sections:
                prefix = f"{section.heading}: " if section.heading else ""
                lines.append(f"  - {prefix}{section.preview}")
        return ToolResponse.success(
            text="\n".join(lines),
            data={"notes": [item.to_dict() for item in summaries]},
        )

    @staticmethod
    def _parse_input(input_data: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """把 input_data 规整为参数字典（dict / JSON 字符串 / 纯文本）"""
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
                loaded = None
            if isinstance(loaded, dict):
                params.update(loaded)
                return params
        params.setdefault("action", "search")
        params.setdefault("query", text)
        return params
