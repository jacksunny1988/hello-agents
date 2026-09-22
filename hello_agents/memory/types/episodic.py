"""情景记忆

按时间顺序记录的事件序列（"发生了什么、何时发生"）。
SQLite 持久化全量事件，Qdrant（或内存向量）支撑语义检索，
支持 session_id 归组与时间范围过滤。
"""

from typing import Any

from ..base import BaseMemory, MemoryItem, MemoryType
from ..embedding import BaseEmbedding, create_embedding
from ..storage.document_store import DocumentStore
from ..storage.qdrant_store import create_vector_store


class EpisodicMemory(BaseMemory):
    """情景记忆（SQLite + 向量检索）"""

    memory_type = MemoryType.EPISODIC

    def __init__(
        self,
        embedding: BaseEmbedding | None = None,
        document_store: DocumentStore | None = None,
        vector_store=None,
        config=None,
    ):
        self._embedding = embedding or create_embedding(config=config)
        self._docs = document_store or DocumentStore(
            sqlite_path=config.sqlite_path if config else ":memory:"
        )
        if vector_store is not None:
            self._vectors = vector_store
        elif config is not None:
            self._vectors = create_vector_store(config)
        else:
            self._vectors = _default_vector_store(self._embedding)
        self._owns_stores = document_store is None and vector_store is None

    async def add(self, item: MemoryItem) -> str:
        """写入一条事件：补全向量后同时落 SQLite 与向量库"""
        item.memory_type = MemoryType.EPISODIC
        if item.embedding is None:
            item.embedding = self._embedding.embed(item.content)
        self._docs.add(item)
        await self._vectors.upsert([item])
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """按 id 读取事件"""
        return self._docs.get(item_id)

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """向量近邻检索 + 时间范围过滤"""
        filters = self._normalize_filters(filters)
        query_vector = self._embedding.embed(query)
        hits = await self._vectors.search(query_vector, limit=limit, filters=filters)
        results = []
        for item_id, score in hits:
            item = self._docs.get(item_id)
            if item is None:
                continue
            item.score = score
            if self._in_time_range(item, filters):
                results.append(item)
        return results[: max(limit, 0)]

    async def delete(self, item_id: str) -> bool:
        """删除事件（文档 + 向量）"""
        deleted = self._docs.delete(item_id)
        await self._vectors.delete([item_id])
        return deleted

    async def clear(self) -> int:
        """清空全部情景记忆"""
        total = self._docs.clear(MemoryType.EPISODIC)
        await self._vectors.clear()
        return total

    async def count(self) -> int:
        """返回事件条数"""
        return self._docs.count(MemoryType.EPISODIC)

    def close(self) -> None:
        """释放自有存储资源"""
        if self._owns_stores and isinstance(self._docs, DocumentStore):
            self._docs.close()

    @staticmethod
    def _normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
        normalized = dict(filters or {})
        normalized["memory_type"] = MemoryType.EPISODIC.value
        return normalized

    @staticmethod
    def _in_time_range(item: MemoryItem, filters: dict[str, Any]) -> bool:
        after = filters.get("after")
        before = filters.get("before")
        created = item.created_at.isoformat()
        return not (
            (after and created < str(after)) or (before and created > str(before))
        )


def _default_vector_store(embedding: BaseEmbedding):
    from ..base import MemoryConfig

    return create_vector_store(MemoryConfig(embedding_dim=embedding.dim))
