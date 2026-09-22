"""记忆系统

为 Agent 提供四类认知记忆（工作/情景/语义/感知）与 RAG 问答能力。
零外部依赖即可运行，配置 Qdrant / Neo4j / DashScope 后自动升级。

典型用法：
    from hello_agents.memory import MemoryManager

    manager = MemoryManager()
    await manager.remember("用户偏好蓝色", memory_type="semantic")
    hits = await manager.recall("用户喜欢什么颜色")
"""

from .base import BaseMemory, MemoryConfig, MemoryItem, MemoryType
from .embedding import BaseEmbedding, TFIDFEmbedding, create_embedding
from .manager import MemoryManager
from .rag import DocumentChunk, DocumentProcessor, RAGPipeline, RAGResult
from .types import EpisodicMemory, PerceptualMemory, SemanticMemory, WorkingMemory

__all__ = [
    "BaseEmbedding",
    "BaseMemory",
    "DocumentChunk",
    "DocumentProcessor",
    "EpisodicMemory",
    "MemoryConfig",
    "MemoryItem",
    "MemoryManager",
    "MemoryType",
    "PerceptualMemory",
    "RAGPipeline",
    "RAGResult",
    "SemanticMemory",
    "TFIDFEmbedding",
    "WorkingMemory",
    "create_embedding",
]
