"""打分器测试：关键词重叠 / 向量相似度 / 缓存"""

import logging

import pytest

import hello_agents.core as core_pkg
import hello_agents.memory as memory_pkg
from hello_agents.context.cache import TTLCache
from hello_agents.context.scoring import (
    EmbeddingSimilarityScorer,
    KeywordOverlapScorer,
    create_relevance_scorer,
)
from hello_agents.core import ConfigError
from hello_agents.memory import TFIDFEmbedding, cosine_similarity


class _CountingEmbedding(TFIDFEmbedding):
    """记录 embed_texts 调用次数，用于验证批量与缓存行为"""

    def __init__(self, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self.calls = 0
        self.texts_seen = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts_seen += len(texts)
        return super().embed_texts(texts)


class _ShortBatchEmbedding(TFIDFEmbedding):
    """返回不足额向量的后端，用于验证对齐防护（C7）"""

    def __init__(self, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self.calls = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = super().embed_texts(texts)
        if not vectors:
            return vectors
        # 故意少返回一个向量，模拟后端契约被破坏
        return vectors[:-1]


class _EmptyBatchEmbedding(TFIDFEmbedding):
    """总是返回空列表的后端，用于验证错位场景（C7 变异演示）"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return []


class _OverBatchEmbedding(TFIDFEmbedding):
    """返回超额向量的后端，用于验证数量契约的对称防护（R2）"""

    def __init__(self, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self.calls = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        vectors = super().embed_texts(texts)
        # 故意多还一个向量，模拟后端把缓存命中项也一并返回
        extra = vectors[0] if vectors else [0.0] * self.dim
        return [*vectors, extra]


class _FixedEmbedding(TFIDFEmbedding):
    """按文本返回固定向量的后端，用于验证余弦钳位到 [0, 1]"""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        super().__init__(dim=len(next(iter(mapping.values()))))
        self.mapping = mapping

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.mapping[text] for text in texts]


def test_cosine_similarity_is_exported_from_memory_package():
    """cosine_similarity 既要可导入，也要出现在 memory 包的 __all__ 中"""
    assert "cosine_similarity" in memory_pkg.__all__
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_config_error_is_exported_from_core_package():
    """ConfigError 既要可导入，也要出现在 core 包的 __all__ 中"""
    assert "ConfigError" in core_pkg.__all__
    assert issubclass(ConfigError, Exception)


def test_keyword_scorer_matches_identical_text():
    scorer = KeywordOverlapScorer()
    assert scorer.score("用户喜欢爬山", "用户喜欢爬山") == pytest.approx(1.0)


def test_keyword_scorer_returns_zero_for_empty_query():
    # 契约文档：空查询恒 0.0。
    # 注意：早退分支与 Jaccard 分母路径在此输入下都返回 0.0，
    # 删掉早退分支结果不变——本用例钉不住早退分支本身。
    scorer = KeywordOverlapScorer()
    assert scorer.score("任意内容", "") == 0.0


def test_keyword_scorer_both_empty_inputs_return_zero():
    """双空输入恒 0.0；与空 query 用例合看，可杀掉「两道防护全删」（R3）

    单独删 `if not query_tokens` 或 `if not union` 结果都不变；
    两道全删时本输入会除零，本用例因此可观测防护整体被拆掉。
    """
    assert KeywordOverlapScorer().score("", "") == 0.0


def test_keyword_scorer_scores_english_overlap():
    """等长对照 + Jaccard 真值钉住，避免被长度型假实现满足

    query="alpha beta gamma delta"（4 token）
    related="alpha beta black white"（22 字符，4 token）：
        交集 2、并集 6 → Jaccard = 1/3
        （若误用 交集/query=0.5 或 交集/content=0.5 会被 approx 钉死）
    unrelated="kappa lambda mu nu phi"（22 字符，5 token）：交集 0 → Jaccard = 0
    两条 content 等长，长度型 score 无法拉开差距。
    """
    scorer = KeywordOverlapScorer()
    query = "alpha beta gamma delta"
    related = scorer.score("alpha beta black white", query)
    unrelated = scorer.score("kappa lambda mu nu phi", query)
    assert related == pytest.approx(1.0 / 3.0)
    assert unrelated == pytest.approx(0.0)
    assert related - unrelated == pytest.approx(1.0 / 3.0)


def test_keyword_scorer_tokenizes_cjk_characters():
    """中文无空格，按单字切分才能产生重叠"""
    scorer = KeywordOverlapScorer()
    assert scorer.score("用户偏好深蓝色", "用户喜欢什么颜色") > 0.0


def test_score_many_matches_individual_scores():
    # 契约文档：score_many 与逐条 score 等价。
    # 批量行为由 test_embedding_scorer_batches_into_single_call 单独钉住，
    # 本用例不区分是否批量计算。
    scorer = KeywordOverlapScorer()
    contents = ["用户喜欢爬山", "磁盘告警", ""]
    assert scorer.score_many(contents, "用户喜欢") == [
        scorer.score(content, "用户喜欢") for content in contents
    ]


def test_embedding_scorer_batches_into_single_call():
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    scorer.score_many(["用户喜欢爬山", "磁盘告警", "网络延迟"], "用户喜欢什么")
    assert embedding.calls == 1
    assert embedding.texts_seen == 4  # 1 条查询 + 3 条内容


def test_embedding_scorer_uses_cache_on_repeat():
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    scorer.score_many(["用户喜欢爬山"], "用户喜欢什么")
    scorer.score_many(["用户喜欢爬山"], "用户喜欢什么")
    assert embedding.calls == 1


def test_embedding_scorer_ranks_identical_text_highest():
    """同文余弦必为 1.0；等长对照钉差值，避免长度型假实现蒙混过关

    same 与 unrelated 的 content 均为 8 字 CJK，长度型 score 会给出相同值。
    dim=64 的 TF-IDF 下等长无关串与查询无共享字，余弦为 0。
    """
    embedding = TFIDFEmbedding(dim=64)
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    query = "用户喜欢什么颜色"
    same = scorer.score(query, query)
    # 等长（8 字）不同串
    unrelated = scorer.score("服务器磁盘告警了", query)
    assert same == pytest.approx(1.0)
    assert unrelated == pytest.approx(0.0)
    assert same - unrelated == pytest.approx(1.0)


def test_embedding_scorer_returns_zero_for_empty_query():
    """空查询早退：不打 embedding 点，也不返回相似度

    若删掉早退分支，TF-IDF 对空串是零向量、余弦恒 0.0，结果断言照样过；
    因此必须钉 embedding.calls == 0（空输入不打点），而非只看结果是 [0.0]。
    """
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    assert scorer.score_many(["内容"], "") == [0.0]
    assert embedding.calls == 0


def test_embedding_scorer_handles_empty_contents():
    """空内容列表早退：不打 embedding 点

    若删掉早退分支，_embed_many(["查询"]) 后 vectors[1:] 仍是 []，
    结果断言照样过；必须钉 embedding.calls == 0（空输入不打点）。
    """
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    assert scorer.score_many([], "查询") == []
    assert embedding.calls == 0


def test_embedding_scorer_accepts_external_cache():
    cache: TTLCache[str, list[float]] = TTLCache(max_size=8, ttl_seconds=60)
    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64), cache=cache)
    scorer.score("用户喜欢爬山", "用户喜欢")
    assert cache.stats()["size"] > 0


def test_embedding_scorer_clamps_negative_cosine_to_zero():
    """规格 4.3：score = max(0, min(1, cosine))，负余弦必须钳到 0.0

    反方向向量余弦为 -1.0；若去掉 _clamp01，分数会溢出 [0,1] 契约。
    """
    embedding = _FixedEmbedding(
        {
            "查询": [1.0, 0.0],
            "反向内容": [-1.0, 0.0],
            "同向内容": [2.0, 0.0],
        }
    )
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    assert scorer.score("反向内容", "查询") == pytest.approx(0.0)
    assert scorer.score("同向内容", "查询") == pytest.approx(1.0)


def test_embedding_scorer_clamps_unity_cosine_rounding_overflow():
    """浮点自比余弦可略大于 1（如 1.0000000000000002），必须被 min(1.0, …) 钳住

    不能用 pytest.approx(1.0) —— 默认 abs=1e-12 会吞掉 2ulp，杀不掉「删 min(1.0)」。
    """
    embedding = _FixedEmbedding({"查询": [1.0, 1.0, 1.0], "同": [1.0, 1.0, 1.0]})
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    assert scorer.score("同", "查询") == 1.0


def test_embedding_scorer_rejects_short_embedding_batch():
    """后端返回不足额向量时必须抛错，绝不静默截断/错位（C7）"""
    embedding = _ShortBatchEmbedding(dim=64)
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    with pytest.raises(RuntimeError, match="向量数量不足"):
        scorer.score_many(["内容甲", "内容乙"], "查询")


def test_embedding_scorer_rejects_empty_embedding_batch_with_partial_cache():
    """缓存部分命中 + 后端返回空批次时，query/contents 会错位，必须抛错（C7）"""
    embedding = _EmptyBatchEmbedding(dim=64)
    cache: TTLCache[str, list[float]] = TTLCache(max_size=8, ttl_seconds=60)
    scorer = EmbeddingSimilarityScorer(embedding=embedding, cache=cache)
    # 先用正常后端把「内容甲」的向量写入缓存，再换坏后端只缺 query/内容乙
    scorer.embedding = TFIDFEmbedding(dim=64)
    scorer.score("内容甲", "无关查询")
    scorer.embedding = embedding
    with pytest.raises(RuntimeError, match="向量数量不足"):
        scorer.score_many(["内容甲", "内容乙"], "查询")


def test_embedding_scorer_rejects_over_batch_embedding():
    """后端多还向量时同样必须响亮失败（R2）

    若 zip 静默截断，多余向量会把错误向量配给 missing 下标，分数错位无声。
    """
    embedding = _OverBatchEmbedding(dim=64)
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    with pytest.raises(RuntimeError, match="向量数量超额"):
        scorer.score_many(["内容甲", "内容乙"], "查询")


def test_create_relevance_scorer_keyword():
    assert isinstance(create_relevance_scorer("keyword"), KeywordOverlapScorer)


def test_create_relevance_scorer_embedding():
    assert isinstance(create_relevance_scorer("embedding"), EmbeddingSimilarityScorer)


def test_create_relevance_scorer_auto_returns_usable_scorer():
    """向量可用时 auto 必须优先给出 EmbeddingSimilarityScorer（C5）"""
    scorer = create_relevance_scorer("auto")
    assert isinstance(scorer, EmbeddingSimilarityScorer)
    assert 0.0 <= scorer.score("用户喜欢爬山", "用户喜欢") <= 1.0


def test_create_relevance_scorer_auto_falls_back_on_construction_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """构造失败必须降级为关键词重叠并记录 warning（C6）"""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("模拟向量后端构造失败")

    monkeypatch.setattr(
        "hello_agents.context.scoring.EmbeddingSimilarityScorer",
        _boom,
    )
    with caplog.at_level(logging.WARNING, logger="hello_agents.context.scoring"):
        scorer = create_relevance_scorer("auto")
    assert isinstance(scorer, KeywordOverlapScorer)
    assert "降级" in caplog.text


def test_create_relevance_scorer_rejects_unknown_kind():
    with pytest.raises(ConfigError):
        create_relevance_scorer("bogus")
