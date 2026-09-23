"""相关性打分

把候选内容与用户查询的相关性量化为 [0,1]，支持关键词重叠与向量相似度两种实现。

典型用法：
    from hello_agents.context.scoring import create_relevance_scorer

    scorer = create_relevance_scorer("keyword")
    scorer.score("用户偏好深蓝色", "用户喜欢什么颜色")
"""

import hashlib
import logging
import re
from typing import Literal, Protocol

from ..core.exceptions import ConfigError
from ..memory.embedding import BaseEmbedding, cosine_similarity, create_embedding
from .cache import TTLCache

logger = logging.getLogger(__name__)

__all__ = [
    "EmbeddingSimilarityScorer",
    "KeywordOverlapScorer",
    "RelevanceScorer",
    "create_relevance_scorer",
]

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_CHAR_RE = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> set[str]:
    """切词：ASCII 按单词、CJK 按单字（中文无空格，按字切分才有重叠）"""
    tokens = set(_ASCII_WORD_RE.findall(text.lower()))
    tokens.update(_CJK_CHAR_RE.findall(text))
    return tokens


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RelevanceScorer(Protocol):
    """相关性打分策略，返回值恒在 [0.0, 1.0]"""

    def score(self, content: str, query: str) -> float: ...

    def score_many(self, contents: list[str], query: str) -> list[float]: ...


class KeywordOverlapScorer:
    """Jaccard 词重叠，零依赖"""

    name = "keyword"

    def score(self, content: str, query: str) -> float:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return 0.0
        content_tokens = _tokenize(content)
        union = content_tokens | query_tokens
        if not union:
            return 0.0
        return len(content_tokens & query_tokens) / len(union)

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return [self.score(content, query) for content in contents]


class EmbeddingSimilarityScorer:
    """向量余弦相似度，复用 memory 的 embedding 后端"""

    name = "embedding"

    def __init__(
        self,
        embedding: BaseEmbedding | None = None,
        cache: TTLCache[str, list[float]] | None = None,
    ) -> None:
        self.embedding = (
            embedding if embedding is not None else create_embedding("auto")
        )
        self.cache: TTLCache[str, list[float]] = (
            cache if cache is not None else TTLCache()
        )

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """批量取向量，命中缓存的文本不重复嵌入

        后端必须为每条文本返回一个向量；数量不足时抛 RuntimeError，
        绝不静默过滤——否则 score_many 的 query/contents 会错位，
        分数张冠李戴比抛异常更糟。
        """
        keys = [_cache_key(text) for text in texts]
        vectors: list[list[float] | None] = [self.cache.get(key) for key in keys]
        missing = [index for index, vector in enumerate(vectors) if vector is None]
        if missing:
            computed = self.embedding.embed_texts([texts[index] for index in missing])
            for index, vector in zip(missing, computed):
                vectors[index] = vector
                self.cache.put(keys[index], vector)
        resolved: list[list[float]] = []
        for position, vector in enumerate(vectors):
            if vector is None:
                raise RuntimeError(
                    f"embedding 后端返回向量数量不足："
                    f"文本 {position + 1}/{len(texts)} 缺向量（期望 {len(missing)} 个新向量）"
                )
            resolved.append(vector)
        return resolved

    def score(self, content: str, query: str) -> float:
        return self.score_many([content], query)[0]

    def score_many(self, contents: list[str], query: str) -> list[float]:
        if not contents:
            return []
        if not query:
            return [0.0] * len(contents)
        vectors = self._embed_many([query, *contents])
        query_vector = vectors[0]
        return [
            _clamp01(cosine_similarity(query_vector, vector)) for vector in vectors[1:]
        ]


def create_relevance_scorer(
    kind: Literal["keyword", "embedding", "auto"] = "keyword",
) -> RelevanceScorer:
    """按名称创建打分器

    Args:
        kind: "keyword" 关键词重叠；"embedding" 向量相似度；
            "auto" 优先向量、构造失败时降级关键词

    Returns:
        RelevanceScorer: 打分器实例
    """
    if kind == "keyword":
        return KeywordOverlapScorer()
    if kind == "embedding":
        return EmbeddingSimilarityScorer()
    if kind == "auto":
        try:
            return EmbeddingSimilarityScorer()
        except Exception as exc:  # noqa: BLE001 - 降级需吞掉任意后端构造错误
            logger.warning("向量打分器构造失败，降级为关键词重叠: %s", exc)
            return KeywordOverlapScorer()
    raise ConfigError(f"未知的打分器类型: {kind}")
