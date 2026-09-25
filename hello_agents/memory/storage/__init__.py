"""存储后端实现

- DocumentStore：SQLite 结构化持久化（零依赖）
- QdrantVectorStore / InMemoryVectorStore：向量检索（可降级）
- Neo4jStore：知识图谱（可选）
"""

from .document_store import DocumentStore
from .neo4j_store import Neo4jStore, create_neo4j_store
from .qdrant_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    create_vector_store,
)

__all__ = [
    "DocumentStore",
    "InMemoryVectorStore",
    "Neo4jStore",
    "QdrantVectorStore",
    "create_neo4j_store",
    "create_vector_store",
]
