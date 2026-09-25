"""统一嵌入服务

提供三个可互换的后端：
- DashScopeEmbedding：阿里云百炼文本向量（可选依赖 dashscope）
- LocalEmbedding：本地句向量模型（可选依赖 sentence-transformers）
- TFIDFEmbedding：纯 Python 哈希 TF-IDF，零依赖兜底

create_embedding() 按 backend 选择实现；backend=auto 时逐级探测降级。
"""

import hashlib
import math
import re
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"[a-zA-Z]+|[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """英文按词、中文按字切分，全部小写"""
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _stable_hash(token: str) -> int:
    """稳定哈希（避免 Python 内置 hash 的随机化，保证跨进程可复现）"""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度，任一方为零向量时返回 0"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class BaseEmbedding(ABC):
    """嵌入服务抽象基类"""

    dim: int

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量文本转向量"""

    def embed(self, text: str) -> list[float]:
        """单条文本转向量"""
        return self.embed_texts([text])[0]


class TFIDFEmbedding(BaseEmbedding):
    """纯 Python 哈希 TF-IDF 嵌入

    使用特征哈希映射到固定维度，词频按 IDF 加权。
    进程内累积文档频次，适合零依赖兜底与测试。
    """

    def __init__(self, dim: int = 512):
        self.dim = dim
        self._doc_freq: dict[str, int] = {}
        self._doc_count = 0

    def _idf(self, token: str) -> float:
        df = self._doc_freq.get(token, 0)
        return math.log((1 + self._doc_count) / (1 + df)) + 1.0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        tokenized = [_tokenize(text) for text in texts]

        # 先更新文档频次，保证 IDF 语料自适应
        for tokens in tokenized:
            self._doc_count += 1
            for token in set(tokens):
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

        vectors: list[list[float]] = []
        for tokens in tokenized:
            vec = [0.0] * self.dim
            if not tokens:
                vectors.append(vec)
                continue
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            for token, count in tf.items():
                weight = (count / len(tokens)) * self._idf(token)
                vec[_stable_hash(token) % self.dim] += weight
            norm = math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


class DashScopeEmbedding(BaseEmbedding):
    """DashScope 文本向量服务（需要 DASHSCOPE_API_KEY 与 dashscope 包）"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-v3",
        dim: int = 1024,
    ):
        from dashscope import TextEmbedding  # 惰性导入

        self._TextEmbedding = TextEmbedding
        self._api_key = api_key
        self.model = model
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self._TextEmbedding.call(
            model=self.model,
            input=texts,
            api_key=self._api_key,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope 嵌入失败: {response.code} {response.message}"
            )
        embeddings = [
            output["embedding"]
            for output in sorted(
                response.output["embeddings"], key=lambda item: item["text_index"]
            )
        ]
        self.dim = len(embeddings[0]) if embeddings else self.dim
        return embeddings


class LocalEmbedding(BaseEmbedding):
    """本地句向量模型（需要 sentence-transformers 包）"""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # 惰性导入

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, vector)) for vector in vectors]


def create_embedding(backend: str = "auto", config=None) -> BaseEmbedding:
    """创建嵌入服务

    Args:
        backend: ``auto`` / ``dashscope`` / ``local`` / ``tfidf``
        config: MemoryConfig，用于读取 API key、模型名与维度

    backend=auto 时按 dashscope → local → tfidf 顺序探测，前两者任一环节
    失败（缺包、缺 key、运行异常）即继续下一级，最终必返回 TFIDFEmbedding。
    显式指定 backend 时失败会抛出异常，不做静默降级。
    """
    from .base import MemoryConfig

    cfg = config or MemoryConfig()
    candidates = [backend] if backend != "auto" else ["dashscope", "local", "tfidf"]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            if candidate == "tfidf":
                return TFIDFEmbedding(dim=cfg.embedding_dim)
            if candidate == "dashscope":
                if not cfg.dashscope_api_key and backend == "auto":
                    raise RuntimeError("DASHSCOPE_API_KEY 未配置")
                return DashScopeEmbedding(
                    api_key=cfg.dashscope_api_key,
                    model=cfg.dashscope_model,
                    dim=cfg.embedding_dim,
                )
            if candidate == "local":
                return LocalEmbedding()
            raise ValueError(f"未知嵌入后端: {candidate}")
        except Exception as error:  # 惰性导入失败 / 服务不可用
            last_error = error
            if backend != "auto":
                raise
    raise RuntimeError(f"所有嵌入后端均不可用: {last_error}")
