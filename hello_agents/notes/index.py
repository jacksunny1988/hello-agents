"""笔记索引 —— notes_index.json 的纯算术

只认字典，不认识文件系统：加载、保存、增删改查、字段过滤、整体替换。
文件与索引是否一致由 NoteStore 负责。
"""

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import NoteError, parse_datetime

logger = logging.getLogger(__name__)


class NoteIndex:
    """notes_index.json 的内存视图与落盘

    条目是含 ``id`` 与 ``file_path`` 的字典，以 ``id`` 为键。索引是派生物：
    损坏或畸形的条目会被丢弃并告警，随后由 NoteStore 按目录重建。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._entries: dict[str, dict[str, Any]] = {}

    @property
    def path(self) -> Path:
        """索引文件路径"""
        return self._path

    def load(self) -> None:
        """从磁盘读入内存

        文件不存在 → 空索引；JSON 损坏、顶层不是对象、条目缺 id/file_path
        → 丢弃该部分并告警（索引是派生物，可重建）。
        """
        self._entries = {}
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("索引文件 %s 不可读（%s），按空索引处理", self._path, error)
            return
        if not isinstance(raw, dict):
            logger.warning("索引文件 %s 顶层不是对象，按空索引处理", self._path)
            return
        for entry in raw.values():
            if (
                not isinstance(entry, dict)
                or not entry.get("id")
                or not entry.get("file_path")
            ):
                logger.warning(
                    "索引文件 %s 存在缺 id/file_path 的条目，已丢弃", self._path
                )
                continue
            self._entries[str(entry["id"])] = dict(entry)

    def save(self) -> None:
        """原子落盘：先写同目录临时文件，再 os.replace"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: self._entries[key] for key in sorted(self._entries)}
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self._path)

    def get(self, note_id: str) -> dict[str, Any] | None:
        """取单条副本，不存在返回 None"""
        entry = self._entries.get(note_id)
        return dict(entry) if entry else None

    def all(self) -> list[dict[str, Any]]:
        """全部条目，按 updated_at 降序、同值按 id 升序"""
        return self._ordered(self._entries.values())

    def filter(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """按元数据过滤

        ``type`` 等值；``tags`` 为全部包含（AND）；``since`` / ``until``
        比较 ``updated_at``（闭区间）。
        """
        return self._ordered(
            entry
            for entry in self._entries.values()
            if self._matches(entry, type, tags, since, until)
        )

    def upsert(self, entry: dict[str, Any]) -> None:
        """按 ``entry["id"]`` 写入或覆盖；缺 id/file_path 抛 NoteError"""
        note_id = entry.get("id")
        if not note_id or not entry.get("file_path"):
            raise NoteError("索引条目必须包含 id 与 file_path")
        self._entries[str(note_id)] = dict(entry)

    def remove(self, note_id: str) -> bool:
        """删除条目，返回是否命中"""
        return self._entries.pop(note_id, None) is not None

    def replace_all(self, entries: list[dict[str, Any]]) -> None:
        """清空后按给定条目重建"""
        self._entries = {}
        for entry in entries:
            self.upsert(entry)

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _ordered(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 updated_at 降序、同值按 id 升序（两趟稳定排序）"""
        by_id = sorted(
            (dict(entry) for entry in entries), key=lambda e: str(e.get("id") or "")
        )
        return sorted(by_id, key=lambda e: str(e.get("updated_at") or ""), reverse=True)

    @staticmethod
    def _matches(
        entry: dict[str, Any],
        type: str | None,
        tags: list[str] | None,
        since: datetime | None,
        until: datetime | None,
    ) -> bool:
        """单个条目是否满足全部过滤条件"""
        if type is not None and entry.get("type") != type:
            return False
        entry_tags = entry.get("tags")
        if tags and not set(tags).issubset(
            entry_tags if isinstance(entry_tags, list) else []
        ):
            return False
        stamp = parse_datetime(entry.get("updated_at"))
        if since is not None and (stamp is None or stamp < since):
            return False
        if until is not None and (stamp is None or stamp > until):  # noqa: SIM103
            return False
        return True
