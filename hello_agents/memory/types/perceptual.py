"""感知记忆

多模态输入（图像/音频/视频/文本）的原始感知记录。
SQLite 存描述与资源引用（URI），向量库存描述文本的嵌入，
支持按模态过滤检索。二进制大对象不入库，仅记录引用。
"""

from typing import Any

from ..base import BaseMemory, MemoryItem, MemoryType
from ..embedding import BaseEmbedding, create_embedding
from ..storage.document_store import DocumentStore
from ..storage.qdrant_store import create_vector_store

_SUPPORTED_MODALITIES = {"text", "image", "audio", "video", "file"}


class PerceptualMemory(BaseMemory):
    """感知记忆（SQLite + 向量检索，多模态）"""

    memory_type = MemoryType.PERCEPTUAL

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
        """写入一条感知记录

        metadata 约定键：
        - modality: text / image / audio / video / file
        - uri: 资源引用（路径或 URL），二进制内容不入库
        """
        item.memory_type = MemoryType.PERCEPTUAL
        modality = str(item.metadata.get("modality", "text")).lower()
        if modality not in _SUPPORTED_MODALITIES:
            raise ValueError(
                f"不支持的模态: {modality}，可选 {sorted(_SUPPORTED_MODALITIES)}"
            )
        item.metadata["modality"] = modality

        if item.embedding is None:
            # 用可读描述（content）做嵌入；纯二进制资源需提供描述文本
            item.embedding = self._embedding.embed(item.content)
        self._docs.add(item)
        await self._vectors.upsert([item])
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """按 id 读取感知记录"""
        return self._docs.get(item_id)

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """向量近邻检索，支持 modality 等 metadata 过滤"""
        normalized = dict(filters or {})
        normalized["memory_type"] = MemoryType.PERCEPTUAL.value

        query_vector = self._embedding.embed(query)
        hits = await self._vectors.search(query_vector, limit=limit, filters=normalized)
        results = []
        for item_id, score in hits:
            item = self._docs.get(item_id)
            if item is None:
                continue
            item.score = score
            results.append(item)
        return results[: max(limit, 0)]

    async def delete(self, item_id: str) -> bool:
        """删除感知记录（文档 + 向量）"""
        deleted = self._docs.delete(item_id)
        await self._vectors.delete([item_id])
        return deleted

    async def clear(self) -> int:
        """清空全部感知记忆"""
        total = self._docs.clear(MemoryType.PERCEPTUAL)
        await self._vectors.clear()
        return total

    async def count(self) -> int:
        """返回感知记录条数"""
        return self._docs.count(MemoryType.PERCEPTUAL)

    def close(self) -> None:
        """释放自有存储资源"""
        if self._owns_stores and isinstance(self._docs, DocumentStore):
            self._docs.close()


def _default_vector_store(embedding: BaseEmbedding):
    from ..base import MemoryConfig

    return create_vector_store(MemoryConfig(embedding_dim=embedding.dim))
