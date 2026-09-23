"""笔记存储 —— 文件与索引的协同

.md 文件的原子读写、CRUD，以及索引漂移的检测与修复。
文件是真相源，索引是派生缓存：任何自动修复只改索引，绝不改写 .md。
"""

import logging
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .base import (
    Note,
    NoteConfig,
    NoteError,
    NoteMeta,
    NoteNotFoundError,
    NoteType,
    utcnow,
)
from .index import NoteIndex

logger = logging.getLogger(__name__)


class NoteStore:
    """结构化笔记存储

    每个公开操作都先 ``_sync()``（L1 集合级对齐），因此索引不常驻内存，
    多进程与人工编辑即时可见。写 ``.md`` 只发生在 ``create`` / ``update``。

    ``read`` 之外的检索类方法返回的 ``NoteMeta`` 直接取自刚读到的文件
    （它本就是 ``Note``，可能带 ``body``）；``list`` 只走索引，不带正文。
    """

    def __init__(self, config: NoteConfig | None = None) -> None:
        self._config = config or NoteConfig()
        self._index = NoteIndex(self._config.index_path)

    @property
    def config(self) -> NoteConfig:
        """当前配置"""
        return self._config

    def create(
        self,
        title: str,
        body: str = "",
        *,
        type: str = NoteType.GENERAL,
        tags: list[str] | None = None,
        note_id: str | None = None,
    ) -> Note:
        """新建笔记并落盘

        ``note_id`` 可显式指定（迁移 / 幂等写入）；已存在时抛 NoteError。
        未指定时按 ``note_YYYYMMDD_HHMMSS_N`` 生成，同秒内序号递增。
        """
        if not title.strip():
            raise NoteError("title 不能为空")
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        self._sync()
        now = utcnow()
        new_id = note_id or self._next_id(now)
        if (
            self._index.get(new_id) is not None
            or (self._config.notes_dir / f"{new_id}.md").exists()
        ):
            raise NoteError(f"笔记 id 已存在: {new_id}")
        note = Note(
            id=new_id,
            title=title,
            type=str(type),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
            file_path=f"{new_id}.md",
            body=body,
        )
        self._write(note)
        return note

    def read(self, note_id: str) -> Note:
        """读取单条笔记；不存在抛 NoteNotFoundError

        顺带做 L2 单条级漂移检测：frontmatter 与索引条目不一致时以文件为准修正索引。
        """
        self._sync()
        note = self._read_note(note_id)
        if self._repair_one(note):
            self._index.save()
        return note

    def update(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
    ) -> Note:
        """按字段更新；全部为 None 时是 no-op（不刷新 updated_at）"""
        if title is not None and not title.strip():
            raise NoteError("title 不能为空")
        current = self.read(note_id)
        if title is None and body is None and type is None and tags is None:
            return current
        updated = replace(
            current,
            title=current.title if title is None else title,
            body=current.body if body is None else body,
            type=current.type if type is None else str(type),
            tags=current.tags if tags is None else list(tags),
            updated_at=utcnow(),
        )
        self._write(updated)
        return updated

    def delete(self, note_id: str) -> bool:
        """删除笔记文件与索引条目；不存在返回 False"""
        self._sync()
        entry = self._index.get(note_id)
        path = self._config.notes_dir / (
            str(entry["file_path"]) if entry else f"{note_id}.md"
        )
        if entry is None and not path.is_file():
            return False
        path.unlink(missing_ok=True)
        self._index.remove(note_id)
        self._index.save()
        return True

    def exists(self, note_id: str) -> bool:
        """笔记是否存在（先做 L1 对齐，再以索引为准）"""
        self._sync()
        return self._index.get(note_id) is not None

    def _sync(self) -> None:
        """L1 集合级：目录 ↔ 索引对齐（缺失条目删除、孤儿文件补录）"""
        self._index.load()
        on_disk = self._markdown_names()
        indexed = {str(entry["file_path"]) for entry in self._index.all()}
        changed = False
        for entry in self._index.all():
            if entry["file_path"] not in on_disk:
                self._index.remove(str(entry["id"]))
                changed = True
        for file_name in sorted(on_disk - indexed):
            self._index.upsert(self._read_file(file_name).to_dict())
            changed = True
        if changed:
            self._index.save()
            logger.warning(
                "笔记索引与目录不一致，已按目录修正（现 %d 条）", len(self._index)
            )

    def _write(self, note: Note) -> None:
        """原子写 .md 并同步索引"""
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self._config.notes_dir / note.file_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(note.to_markdown(), encoding="utf-8")
        os.replace(tmp, path)
        self._index.upsert(note.to_dict())
        self._index.save()

    def _read_note(self, note_id: str) -> Note:
        """按 id 读文件；不存在抛 NoteNotFoundError"""
        entry = self._index.get(note_id)
        file_name = str(entry["file_path"]) if entry else f"{note_id}.md"
        if not (self._config.notes_dir / file_name).is_file():
            raise NoteNotFoundError(f"笔记不存在: {note_id}")
        return self._read_file(file_name)

    def _read_file(self, file_name: str) -> Note:
        """读并解析一个笔记文件（BOM 由 utf-8-sig 处理）"""
        path = self._config.notes_dir / file_name
        return Note.from_markdown(
            path.read_text(encoding="utf-8-sig"),
            file_path=file_name,
            fallback_time=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )

    def _repair_one(self, note: Note) -> bool:
        """L2 单条级：以文件 frontmatter 为准修正索引条目，返回是否改动（不落盘）"""
        wanted = note.to_dict()
        changed = self._index.get(note.id) != wanted
        for entry in self._index.all():
            if entry["id"] != note.id and entry["file_path"] == note.file_path:
                self._index.remove(str(entry["id"]))
                changed = True
        if changed:
            self._index.upsert(wanted)
            logger.warning(
                "笔记 %s 的索引条目与文件不一致，已按文件修正", note.file_path
            )
        return changed

    def _markdown_names(self) -> set[str]:
        """目录下的 .md 文件名集合（目录不存在时为空集）"""
        if not self._config.notes_dir.is_dir():
            return set()
        return {path.name for path in self._config.notes_dir.glob("*.md")}

    def _next_id(self, now: datetime) -> str:
        """生成 ``note_YYYYMMDD_HHMMSS_N``，同时避开目录与索引里的既有 id"""
        prefix = f"note_{now:%Y%m%d_%H%M%S}_"
        used = {path.stem for path in self._config.notes_dir.glob(f"{prefix}*.md")}
        used |= {
            str(entry["id"])
            for entry in self._index.all()
            if str(entry["id"]).startswith(prefix)
        }
        serial = 0
        while f"{prefix}{serial}" in used:
            serial += 1
        return f"{prefix}{serial}"
