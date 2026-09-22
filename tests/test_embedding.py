"""嵌入服务测试：TF-IDF 向量性质 + 降级工厂"""

import math

import pytest

from hello_agents.memory import MemoryConfig, create_embedding
from hello_agents.memory.embedding import (
    TFIDFEmbedding,
    cosine_similarity,
)


def test_dimension_is_fixed():
    emb = TFIDFEmbedding(dim=64)
    vectors = emb.embed_texts(["hello world", "你好世界", ""])
    assert len(vectors) == 3
    assert all(len(vector) == 64 for vector in vectors)


def test_vectors_are_l2_normalized():
    emb = TFIDFEmbedding(dim=64)
    vector = emb.embed("机器学习与深度学习")
    norm = math.sqrt(sum(x * x for x in vector))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_similar_texts_score_higher():
    emb = TFIDFEmbedding(dim=128)
    base, near, far = emb.embed_texts(
        ["机器学习模型训练", "机器学习模型调优", "今天天气晴朗适合出门"]
    )
    assert cosine_similarity(base, near) > cosine_similarity(base, far)


def test_empty_text_yields_zero_vector():
    emb = TFIDFEmbedding(dim=32)
    vector = emb.embed("")
    assert all(x == 0.0 for x in vector)
    assert cosine_similarity(vector, vector) == 0.0


def test_factory_tfidi_is_always_available():
    emb = create_embedding("tfidf", MemoryConfig(embedding_dim=32))
    assert isinstance(emb, TFIDFEmbedding)
    assert emb.dim == 32


def test_factory_auto_falls_back_to_tfidi():
    # 测试环境不装 dashscope / sentence-transformers，auto 必须兜底成功
    emb = create_embedding(
        "auto", MemoryConfig(embedding_backend="auto", embedding_dim=32)
    )
    assert isinstance(emb, TFIDFEmbedding)


def test_factory_explicit_backend_raises_when_missing():
    config = MemoryConfig(embedding_backend="dashscope", dashscope_api_key=None)
    with pytest.raises((ImportError, RuntimeError)):
        create_embedding("dashscope", config)


def test_factory_unknown_backend_rejected():
    with pytest.raises(ValueError, match="未知嵌入后端"):
        create_embedding("quantum", MemoryConfig())
