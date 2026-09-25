"""记忆管理器

统一协调四类记忆：写入分流、跨类型检索、过期工作记忆固化（记忆巩固）。
Agent 侧一般只与 MemoryManager 打交道，不直接触碰底层存储。
"""

import asyncio
from typing import Any

from .base import BaseMemory, MemoryConfig, MemoryItem, MemoryType
from .embedding import BaseEmbedding, create_embedding
from .types import EpisodicMemory, PerceptualMemory, SemanticMemory, WorkingMemory


class MemoryManager:
    """记忆管理器

    - remember(): 按 memory_type 写入对应记忆，高重要度工作记忆可固化到情景记忆
    - recall(): 跨类型并发检索并按得分归并
    - forget()/clear(): 按 id 或按类型删除
    """

    def __init__(
        self,
        config: MemoryConfig | None = None,
        embedding: BaseEmbedding | None = None,
        working: WorkingMemory | None = None,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        perceptual: PerceptualMemory | None = None,
    ):
        self.config = config or MemoryConfig()
        self.embedding = embedding or create_embedding(
            self.config.embedding_backend, self.config
        )

        shared = {
            "embedding": self.embedding,
            "document_store": None,
            "vector_store": None,
            "config": self.config,
        }
        self.working = working or WorkingMemory(
            ttl_seconds=self.config.working_ttl_seconds,
            max_items=self.config.working_max_items,
        )
        self.episodic = episodic or EpisodicMemory(**shared)
        self.semantic = semantic or SemanticMemory(**shared)
        self.perceptual = perceptual or PerceptualMemory(**shared)

    def memory_of(self, memory_type: MemoryType | str) -> BaseMemory:
        """按类型取出对应记忆实例"""
        resolved = MemoryType(memory_type)
        return {
            MemoryType.WORKING: self.working,
            MemoryType.EPISODIC: self.episodic,
            MemoryType.SEMANTIC: self.semantic,
            MemoryType.PERCEPTUAL: self.perceptual,
        }[resolved]

    async def remember(
        self,
        content: str,
        memory_type: MemoryType | str = MemoryType.WORKING,
        *,
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
        consolidate: bool | None = None,
    ) -> str:
        """写入一条记忆

        Args:
            content: 记忆文本内容
            memory_type: 目标记忆类型
            metadata: 自定义元数据
            ttl_seconds: 覆盖默认 TTL（仅工作记忆生效）
            consolidate: 是否在写入工作记忆的同时固化副本到情景记忆；
                None 时按 metadata['importance'] >= 0.8 自动判断
        """
        resolved = MemoryType(memory_type)
        item = MemoryItem(
            content=content, memory_type=resolved, metadata=dict(metadata or {})
        )
        if ttl_seconds is not None and resolved == MemoryType.WORKING:
            from datetime import timedelta

            from .base import utcnow

            item.expires_at = utcnow() + timedelta(seconds=ttl_seconds)

        item_id = await self.memory_of(resolved).add(item)

        if resolved == MemoryType.WORKING and self._should_consolidate(
            item, consolidate
        ):
            await self.episodic.add(
                MemoryItem(
                    content=content,
                    memory_type=MemoryType.EPISODIC,
                    metadata={**item.metadata, "source": "consolidation"},
                )
            )
        return item_id

    async def recall(
        self,
        query: str,
        *,
        memory_types: list[MemoryType | str] | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """跨类型并发检索，按相似度得分降序归并

        memory_types 为空时检索全部四类；每类取回 limit 条再全局排序截断。
        """
        types = (
            [MemoryType(t) for t in memory_types] if memory_types else list(MemoryType)
        )
        per_type = await asyncio.gather(
            *[
                self.memory_of(t).search(query, limit=limit, filters=filters)
                for t in types
            ]
        )
        merged = [item for batch in per_type for item in batch]
        merged.sort(key=lambda item: item.score or 0.0, reverse=True)
        return merged[: max(limit, 0)]

    async def forget(self, item_id: str) -> bool:
        """按 id 删除记忆，未知类型时逐类尝试删除

        记忆 id 无类型前缀，这里对四类依次尝试删除，命中即返回。
        """
        results = await asyncio.gather(
            self.working.delete(item_id),
            self.episodic.delete(item_id),
            self.semantic.delete(item_id),
            self.perceptual.delete(item_id),
        )
        return any(results)

    async def clear(self, memory_type: MemoryType | str | None = None) -> int:
        """清空记忆；memory_type 为空时清空全部四类"""
        if memory_type is not None:
            return await self.memory_of(memory_type).clear()
        results = await asyncio.gather(
            self.working.clear(),
            self.episodic.clear(),
            self.semantic.clear(),
            self.perceptual.clear(),
        )
        return sum(results)

    async def count(
        self, memory_type: MemoryType | str | None = None
    ) -> dict[str, int]:
        """统计各类记忆条数"""
        if memory_type is not None:
            resolved = MemoryType(memory_type)
            return {resolved.value: await self.memory_of(resolved).count()}
        counts = await asyncio.gather(
            self.working.count(),
            self.episodic.count(),
            self.semantic.count(),
            self.perceptual.count(),
        )
        return {
            MemoryType.WORKING.value: counts[0],
            MemoryType.EPISODIC.value: counts[1],
            MemoryType.SEMANTIC.value: counts[2],
            MemoryType.PERCEPTUAL.value: counts[3],
        }

    def close(self) -> None:
        """释放各记忆占用的存储资源"""
        for memory in (self.working, self.episodic, self.semantic, self.perceptual):
            closer = getattr(memory, "close", None)
            if callable(closer):
                closer()

    @staticmethod
    def _should_consolidate(item: MemoryItem, consolidate: bool | None) -> bool:
        """判断工作记忆是否需要固化到情景记忆"""
        if consolidate is not None:
            return consolidate
        return float(item.metadata.get("importance", 0.0)) >= 0.8
