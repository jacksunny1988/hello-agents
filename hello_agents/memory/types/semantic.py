"""语义记忆

知识三元组（主体-关系-客体）形式的长期知识。
Qdrant 存向量用于语义去重与检索，Neo4j 存图结构用于邻居扩展；
Neo4j 不可用时自动跳过图谱写入，仅保留向量路径。
"""

from typing import Any

from ..base import BaseMemory, MemoryItem, MemoryType
from ..embedding import BaseEmbedding, create_embedding
from ..storage.document_store import DocumentStore
from ..storage.neo4j_store import Neo4jStore, create_neo4j_store
from ..storage.qdrant_store import create_vector_store


class SemanticMemory(BaseMemory):
    """语义记忆（知识图谱 + 向量去重）"""

    memory_type = MemoryType.SEMANTIC

    def __init__(
        self,
        embedding: BaseEmbedding | None = None,
        document_store: DocumentStore | None = None,
        vector_store=None,
        graph_store: Neo4jStore | None = None,
        config=None,
    ):
        self._dedup_threshold = (
            config.semantic_dedup_threshold if config is not None else 0.95
        )
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
        if graph_store is not None:
            self._graph: Neo4jStore | None = graph_store
        elif config is not None:
            self._graph = create_neo4j_store(config)
        else:
            self._graph = None
        self._owns_stores = (
            document_store is None and vector_store is None and graph_store is None
        )

    async def add(self, item: MemoryItem) -> str:
        """写入知识；语义近重复（>= 阈值）时返回已有 id，不再膨胀图谱"""
        item.memory_type = MemoryType.SEMANTIC
        if item.embedding is None:
            item.embedding = self._embedding.embed(item.content)

        existing = await self._find_duplicate(item.embedding)
        if existing is not None:
            return existing

        self._docs.add(item)
        await self._vectors.upsert([item])
        self._write_graph(item)
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """按 id 读取知识"""
        return self._docs.get(item_id)

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """向量近邻检索；命中实体时经 Neo4j 扩展一跳邻居作为补充结果"""
        normalized = dict(filters or {})
        normalized["memory_type"] = MemoryType.SEMANTIC.value

        query_vector = self._embedding.embed(query)
        hits = await self._vectors.search(query_vector, limit=limit, filters=normalized)
        results = []
        for item_id, score in hits:
            item = self._docs.get(item_id)
            if item is None:
                continue
            item.score = score
            results.append(item)

        # 图扩展：用查询词与命中实体名补齐一跳关联
        if self._graph is not None and len(results) < limit:
            results.extend(self._expand_graph(query, results, limit - len(results)))
        return results[: max(limit, 0)]

    async def delete(self, item_id: str) -> bool:
        """删除知识（文档 + 向量；图节点保留，避免误删共享实体）"""
        deleted = self._docs.delete(item_id)
        await self._vectors.delete([item_id])
        return deleted

    async def clear(self) -> int:
        """清空全部语义记忆"""
        total = self._docs.clear(MemoryType.SEMANTIC)
        await self._vectors.clear()
        if self._graph is not None:
            self._graph.clear()
        return total

    async def count(self) -> int:
        """返回知识条数"""
        return self._docs.count(MemoryType.SEMANTIC)

    def close(self) -> None:
        """释放自有存储资源"""
        if not self._owns_stores:
            return
        if isinstance(self._docs, DocumentStore):
            self._docs.close()
        if self._graph is not None:
            self._graph.close()

    async def _find_duplicate(self, embedding: list[float]) -> str | None:
        """向量查重，相似度达到阈值即视为同一知识"""
        hits = await self._vectors.search(
            embedding, limit=1, filters={"memory_type": MemoryType.SEMANTIC.value}
        )
        for item_id, score in hits:
            if score >= self._dedup_threshold:
                return item_id
        return None

    def _write_graph(self, item: MemoryItem) -> None:
        """把三元组写入图谱；非三元组内容只建实体节点"""
        if self._graph is None:
            return
        subject = item.metadata.get("subject")
        relation = item.metadata.get("relation")
        target = item.metadata.get("target")
        if subject and relation and target:
            self._graph.upsert_entity(subject)
            self._graph.upsert_entity(target)
            self._graph.upsert_relation(subject, target, relation)
        elif subject:
            self._graph.upsert_entity(subject)

    def _expand_graph(
        self, query: str, hits: list[MemoryItem], limit: int
    ) -> list[MemoryItem]:
        """对查询词与命中实体做一跳邻居扩展"""
        seeds = [query] + [
            str(item.metadata.get("subject"))
            for item in hits
            if item.metadata.get("subject")
        ]
        extra: list[MemoryItem] = []
        seen = {item.content for item in hits}
        for seed in seeds:
            for edge in self._graph.neighbors(seed, limit=limit):
                content = f"{seed} -> {edge['relation']} -> {edge['neighbor']}"
                if content in seen:
                    continue
                seen.add(content)
                extra.append(
                    MemoryItem(
                        content=content,
                        memory_type=MemoryType.SEMANTIC,
                        metadata={
                            "subject": seed,
                            "relation": edge["relation"],
                            "target": edge["neighbor"],
                            "source": "graph_expansion",
                        },
                        score=0.5,
                    )
                )
                if len(extra) >= limit:
                    return extra
        return extra


def _default_vector_store(embedding: BaseEmbedding):
    from ..base import MemoryConfig

    return create_vector_store(MemoryConfig(embedding_dim=embedding.dim))
