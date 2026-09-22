"""记忆系统基础数据结构

定义记忆条目（MemoryItem）、记忆配置（MemoryConfig）与四类记忆的统一抽象接口（BaseMemory）。
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MemoryType(StrEnum):
    """记忆类型枚举"""

    WORKING = "working"  # 工作记忆：短时、高频、TTL 过期
    EPISODIC = "episodic"  # 情景记忆：事件序列、带时间线
    SEMANTIC = "semantic"  # 语义记忆：知识图谱、可合并去重
    PERCEPTUAL = "perceptual"  # 感知记忆：多模态、带资源引用


def utcnow() -> datetime:
    """返回带时区的当前 UTC 时间"""
    return datetime.now(UTC)


@dataclass
class MemoryItem:
    """单条记忆

    Attributes:
        content: 文本内容（多模态记忆为可读描述）
        memory_type: 所属记忆类型
        id: 唯一标识
        embedding: 向量表示，可为 None（写入时由嵌入服务补全）
        metadata: 自定义元数据（来源、模态、importance 等）
        created_at: 创建时间
        expires_at: 过期时间，None 表示永不过期
        score: 检索时的相似度得分，仅在查询结果中填充
    """

    content: str
    memory_type: MemoryType = MemoryType.WORKING
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    embedding: list[float] | None = field(default=None, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    score: float | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """是否已过期"""
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（不含 embedding，向量由向量库持有）"""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
        """从字典反序列化"""
        expires_at = data.get("expires_at")
        return cls(
            content=data["content"],
            memory_type=MemoryType(data.get("memory_type", MemoryType.WORKING)),
            id=data.get("id") or uuid.uuid4().hex,
            metadata=data.get("metadata") or {},
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else utcnow(),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        )


@dataclass
class MemoryConfig:
    """记忆系统配置

    零配置即可运行（SQLite + TF-IDF + 纯内存向量检索）；
    填入外部服务地址后自动升级为 Qdrant / Neo4j / DashScope。
    """

    # SQLite 文档存储
    sqlite_path: str = ":memory:"

    # 向量存储（qdrant_url 为空且 qdrant-client 未安装时降级为纯内存检索）
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "hello_agents_memory"

    # 图存储（未配置或 neo4j 未安装时跳过图谱写入/扩展）
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None

    # 嵌入服务（backend=auto 时按 dashscope → local → tfidf 顺序自动选择）
    embedding_backend: str = "auto"
    embedding_dim: int = 512
    dashscope_api_key: str | None = None
    dashscope_model: str = "text-embedding-v3"

    # 行为参数
    working_ttl_seconds: float = 600.0
    working_max_items: int = 50
    semantic_dedup_threshold: float = 0.95

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """从环境变量构造配置（缺失项回落到默认值）"""
        import os

        def _opt(name: str) -> str | None:
            value = os.getenv(name)
            return value or None

        return cls(
            sqlite_path=os.getenv("MEMORY_SQLITE_PATH", ":memory:"),
            qdrant_url=_opt("QDRANT_URL"),
            qdrant_api_key=_opt("QDRANT_API_KEY"),
            qdrant_collection=os.getenv("QDRANT_COLLECTION", "hello_agents_memory"),
            neo4j_uri=_opt("NEO4J_URI"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=_opt("NEO4J_PASSWORD"),
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "auto"),
            dashscope_api_key=_opt("DASHSCOPE_API_KEY"),
            dashscope_model=os.getenv("DASHSCOPE_MODEL", "text-embedding-v3"),
        )


class BaseMemory(ABC):
    """四类记忆的统一抽象接口

    所有方法均为异步实现，便于在 Agent 事件循环中并发调度。
    search 的 filters 支持 metadata 键值等值过滤，并保留两个特殊键：
    ``after`` / ``before``（ISO 时间字符串），用于时间范围过滤。
    """

    memory_type: MemoryType

    @abstractmethod
    async def add(self, item: MemoryItem) -> str:
        """写入一条记忆，返回记忆 id"""

    async def add_batch(self, items: list[MemoryItem]) -> list[str]:
        """批量写入，默认逐条调用 add"""
        return [await self.add(item) for item in items]

    @abstractmethod
    async def get(self, item_id: str) -> MemoryItem | None:
        """按 id 读取一条记忆"""

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """检索最相关的记忆，按得分降序"""

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """删除一条记忆，返回是否命中"""

    @abstractmethod
    async def clear(self) -> int:
        """清空该类记忆，返回删除条数"""

    @abstractmethod
    async def count(self) -> int:
        """返回该类记忆的当前条数"""
