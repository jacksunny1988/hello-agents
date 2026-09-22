"""向量存储

三级可用性：
1. Qdrant 服务（配置了 qdrant_url 且安装了 qdrant-client）
2. Qdrant 本地内存模式（安装了 qdrant-client，未配置服务地址）
3. 纯 Python 余弦检索（未安装 qdrant-client，InMemoryVectorStore 兜底）

调用方通过 create_vector_store() 获取统一接口实例，无感切换。
"""

import math
from typing import Any

from ..base import MemoryItem


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """纯 Python 内存向量检索（qdrant-client 缺失时的兜底实现）"""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self._rows: dict[str, tuple[list[float], dict[str, Any]]] = {}

    async def upsert(self, items: list[MemoryItem]) -> None:
        """写入或覆盖向量与载荷"""
        for item in items:
            if item.embedding is None:
                raise ValueError(f"记忆 {item.id} 缺少 embedding，无法写入向量库")
            self._rows[item.id] = (item.embedding, self._payload_of(item))

    async def search(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """向量近邻检索，返回 (id, 得分) 列表"""
        results = []
        for item_id, (stored, payload) in self._rows.items():
            if not self._match(payload, filters):
                continue
            results.append((item_id, _cosine(vector, stored)))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results[: max(limit, 0)]

    async def delete(self, item_ids: list[str]) -> int:
        """删除向量，返回删除条数"""
        deleted = 0
        for item_id in item_ids:
            if self._rows.pop(item_id, None) is not None:
                deleted += 1
        return deleted

    async def clear(self) -> int:
        """清空全部向量"""
        size = len(self._rows)
        self._rows.clear()
        return size

    async def count(self) -> int:
        """返回向量条数"""
        return len(self._rows)

    @staticmethod
    def _payload_of(item: MemoryItem) -> dict[str, Any]:
        payload = dict(item.metadata)
        payload["memory_type"] = item.memory_type.value
        return payload

    @staticmethod
    def _match(payload: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        for key, value in (filters or {}).items():
            if key.startswith("_"):
                continue
            if str(payload.get(key)) != str(value):
                return False
        return True


class QdrantVectorStore:
    """Qdrant 向量存储（远程服务或 :memory: 本地模式）"""

    def __init__(
        self,
        collection: str,
        dim: int,
        url: str | None = None,
        api_key: str | None = None,
    ):
        from qdrant_client import QdrantClient  # 惰性导入
        from qdrant_client.models import Distance, VectorParams

        self._client = (
            QdrantClient(url=url, api_key=api_key) if url else QdrantClient(":memory:")
        )
        self.collection = collection
        self.dim = dim

        existing = {item.name for item in self._client.get_collections().collections}
        if collection not in existing:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    async def upsert(self, items: list[MemoryItem]) -> None:
        """写入或覆盖向量与载荷"""
        from qdrant_client.models import PointStruct

        points = []
        for item in items:
            if item.embedding is None:
                raise ValueError(f"记忆 {item.id} 缺少 embedding，无法写入向量库")
            payload = dict(item.metadata)
            payload["memory_type"] = item.memory_type.value
            points.append(
                PointStruct(id=item.id, vector=item.embedding, payload=payload)
            )
        if points:
            self._client.upsert(collection_name=self.collection, points=points)

    async def search(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """向量近邻检索，返回 (id, 得分) 列表"""
        query_filter = self._build_filter(filters)
        hits = self._client.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=max(limit, 0),
            query_filter=query_filter,
        )
        return [(str(hit.id), float(hit.score)) for hit in hits]

    async def delete(self, item_ids: list[str]) -> int:
        """删除向量，返回删除条数"""
        if not item_ids:
            return 0
        self._client.delete(
            collection_name=self.collection,
            points_selector=item_ids,
        )
        return len(item_ids)

    async def clear(self) -> int:
        """清空整个 collection"""
        total = await self.count()
        self._client.delete_collection(collection_name=self.collection)
        from qdrant_client.models import Distance, VectorParams

        self._client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
        )
        return total

    async def count(self) -> int:
        """返回向量条数"""
        info = self._client.get_collection(collection_name=self.collection)
        return int(info.points_count or 0)

    @staticmethod
    def _build_filter(filters: dict[str, Any] | None):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = []
        for key, value in (filters or {}).items():
            if key.startswith("_"):
                continue
            conditions.append(
                FieldCondition(key=key, match=MatchValue(value=str(value)))
            )
        return Filter(must=conditions) if conditions else None


def create_vector_store(config) -> InMemoryVectorStore | QdrantVectorStore:
    """创建向量存储

    优先尝试 Qdrant（服务端或 :memory:），qdrant-client 不可用时
    静默降级为纯内存实现，保证调用方无需感知依赖情况。
    """
    try:
        return QdrantVectorStore(
            collection=config.qdrant_collection,
            dim=config.embedding_dim,
            url=config.qdrant_url,
            api_key=config.qdrant_api_key,
        )
    except ImportError:
        return InMemoryVectorStore(dim=config.embedding_dim)
