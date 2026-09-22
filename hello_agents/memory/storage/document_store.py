"""SQLite 文档存储

记忆条目的结构化持久化：CRUD、元数据过滤、时间范围过滤与子串检索。
零外部依赖（标准库 sqlite3），是情景/感知记忆的持久化底座。
"""

import json
import sqlite3
from datetime import datetime
from typing import Any

from ..base import MemoryItem, MemoryType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_items (memory_type);
CREATE INDEX IF NOT EXISTS idx_created_at ON memory_items (created_at);
"""

_SPECIAL_FILTER_KEYS = {"after", "before"}


class DocumentStore:
    """SQLite 文档存储"""

    def __init__(self, sqlite_path: str = ":memory:"):
        self._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add(self, item: MemoryItem) -> str:
        """写入或覆盖一条记忆"""
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_items "
            "(id, content, memory_type, metadata, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.content,
                item.memory_type.value,
                json.dumps(item.metadata, ensure_ascii=False),
                item.created_at.isoformat(),
                item.expires_at.isoformat() if item.expires_at else None,
            ),
        )
        self._conn.commit()
        return item.id

    def get(self, item_id: str) -> MemoryItem | None:
        """按 id 读取"""
        row = self._conn.execute(
            "SELECT id, content, memory_type, metadata, created_at, expires_at "
            "FROM memory_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return self._to_item(row) if row else None

    def search(
        self,
        query: str = "",
        *,
        memory_type: MemoryType | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """子串检索 + metadata 等值过滤 + 时间范围过滤"""
        clauses = ["1 = 1"]
        params: list[Any] = []

        if query:
            escaped = (
                query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            )
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(memory_type.value)

        for key, value in (filters or {}).items():
            if key in _SPECIAL_FILTER_KEYS or key.startswith("_"):
                continue
            clauses.append(f"json_extract(metadata, '$.{key}') = ?")
            params.append(str(value))

        if filters and "after" in filters:
            clauses.append("created_at >= ?")
            params.append(str(filters["after"]))
        if filters and "before" in filters:
            clauses.append("created_at <= ?")
            params.append(str(filters["before"]))

        params.append(max(limit, 0))
        rows = self._conn.execute(
            "SELECT id, content, memory_type, metadata, created_at, expires_at "
            f"FROM memory_items WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._to_item(row) for row in rows]

    def delete(self, item_id: str) -> bool:
        """删除一条记忆"""
        cursor = self._conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def clear(self, memory_type: MemoryType | None = None) -> int:
        """清空记忆（可按类型限定），返回删除条数"""
        if memory_type is None:
            cursor = self._conn.execute("DELETE FROM memory_items")
        else:
            cursor = self._conn.execute(
                "DELETE FROM memory_items WHERE memory_type = ?", (memory_type.value,)
            )
        self._conn.commit()
        return cursor.rowcount

    def count(self, memory_type: MemoryType | None = None) -> int:
        """统计条数（可按类型限定）"""
        if memory_type is None:
            row = self._conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memory_items WHERE memory_type = ?",
                (memory_type.value,),
            ).fetchone()
        return int(row[0])

    def close(self) -> None:
        """关闭连接"""
        self._conn.close()

    @staticmethod
    def _to_item(row: tuple) -> MemoryItem:
        return MemoryItem(
            id=row[0],
            content=row[1],
            memory_type=MemoryType(row[2]),
            metadata=json.loads(row[3]),
            created_at=datetime.fromisoformat(row[4]),
            expires_at=datetime.fromisoformat(row[5]) if row[5] else None,
        )
