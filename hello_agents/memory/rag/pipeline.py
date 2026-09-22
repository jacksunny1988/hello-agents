"""RAG 管道

端到端检索增强生成：
ingest: 文档 → 解析分块 → 向量化 → 写入情景记忆
query:  问题 → 向量检索 → 拼上下文 → 可选 LLM 生成

llm 为 None 时退化为纯检索模式，直接返回上下文。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..base import MemoryItem, MemoryType
from ..manager import MemoryManager
from .document import DocumentChunk, DocumentProcessor


@dataclass
class RAGResult:
    """RAG 查询结果"""

    question: str
    answer: str
    chunks: list[MemoryItem] = field(default_factory=list)
    generated: bool = False


class RAGPipeline:
    """RAG 管道

    Args:
        memory_manager: 记忆管理器，负责向量化与存储；不传则用默认配置自建
        llm: 可选生成器，接受 prompt 返回文本（如 HelloAgentsLLM）
        chunk_size / chunk_overlap: 分块参数
        top_k: 默认检索条数
    """

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        llm: Any | None = None,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        top_k: int = 5,
    ):
        self.memory = memory_manager or MemoryManager()
        self._owns_memory = memory_manager is None
        self.llm = llm
        self.top_k = top_k
        self.processor = DocumentProcessor(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    def ingest_file(self, path: str | Path) -> int:
        """导入本地文档，返回写入的分块数"""
        chunks = self.processor.process(path)
        return self._store_chunks(chunks)

    def ingest_text(self, text: str, source: str = "inline") -> int:
        """导入纯文本，返回写入的分块数"""
        chunks = self.processor.split(text, source=source)
        return self._store_chunks(chunks)

    def query(
        self, question: str, *, top_k: int | None = None, generate: bool = True
    ) -> RAGResult:
        """检索并（可选）生成答案

        generate=True 且提供了 llm 时走生成路径；
        否则返回拼接好的上下文作为 answer。
        """
        import asyncio

        limit = top_k or self.top_k
        chunks = asyncio.run(
            self.memory.recall(
                question,
                memory_types=[MemoryType.EPISODIC],
                limit=limit,
                filters={"source": "rag"},
            )
        )
        context = self._build_context(chunks)
        if generate and self.llm is not None and chunks:
            prompt = self._build_prompt(question, context)
            answer = self.llm(prompt)
            return RAGResult(
                question=question, answer=answer, chunks=chunks, generated=True
            )
        return RAGResult(question=question, answer=context, chunks=chunks)

    def close(self) -> None:
        """释放自建的记忆管理器资源"""
        if self._owns_memory:
            self.memory.close()

    def _store_chunks(self, chunks: list[DocumentChunk]) -> int:
        """把分块批量写入情景记忆（标记 source=rag 供检索过滤）"""
        import asyncio

        items = [
            MemoryItem(
                content=chunk.content,
                memory_type=MemoryType.EPISODIC,
                metadata={
                    "source": "rag",
                    "doc_source": chunk.source,
                    "chunk_index": chunk.index,
                    **chunk.metadata,
                },
            )
            for chunk in chunks
        ]
        asyncio.run(self.memory.episodic.add_batch(items))
        return len(items)

    @staticmethod
    def _build_context(chunks: list[MemoryItem]) -> str:
        """把检索结果拼成上下文，未命中时给出提示"""
        if not chunks:
            return "未检索到相关文档内容。"
        return "\n\n".join(
            f"[{index}] (来自 {chunk.metadata.get('doc_source', '未知')}) {chunk.content}"
            for index, chunk in enumerate(chunks, 1)
        )

    @staticmethod
    def _build_prompt(question: str, context: str) -> str:
        return (
            "请严格依据以下上下文回答问题。若上下文不足以回答，"
            "请明确说明「根据已有资料无法回答」。\n\n"
            f"## 上下文\n{context}\n\n## 问题\n{question}\n\n## 回答"
        )
