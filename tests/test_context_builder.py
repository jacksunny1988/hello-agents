"""ContextBuilder 测试：数据类型 / 四阶段全链路 / 缺陷回归"""

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from hello_agents.context.base import (
    BuildResult,
    BuildStats,
    ContextConfig,
    ContextPacket,
    ContextSection,
)
from hello_agents.context.budget import BudgetInfo
from hello_agents.core import ConfigError


def test_packet_clamps_relevance_score():
    packet = ContextPacket(
        content="x", timestamp=datetime.now(tz=UTC), relevance_score=1.7
    )
    assert packet.relevance_score == 1.0


def test_packet_keeps_none_relevance_score():
    """修复 B8：None 表示待计算，不再用 0.5 哨兵"""
    packet = ContextPacket(content="x", timestamp=datetime.now(tz=UTC))
    assert packet.relevance_score is None
    assert packet.token_count == 0
    assert packet.metadata == {}


def test_packet_keeps_explicit_half_score():
    packet = ContextPacket(
        content="x", timestamp=datetime.now(tz=UTC), relevance_score=0.5
    )
    assert packet.relevance_score == 0.5


def test_packet_clamps_negative_relevance_score():
    packet = ContextPacket(
        content="x", timestamp=datetime.now(tz=UTC), relevance_score=-0.4
    )
    assert packet.relevance_score == 0.0


def test_config_defaults():
    config = ContextConfig()
    assert config.max_tokens == 3000
    assert config.reserve_ratio == 0.2
    assert config.min_budget_ratio == 0.5
    assert config.max_budget_ratio == 1.0
    assert config.memory_limit == 10
    assert config.rag_limit == 5
    assert config.history_window == 5
    assert config.cache_max_size == 256
    assert config.log_stats is True
    assert config.experiment is None


def test_config_rejects_bad_weights():
    """修复 B12：抛 ConfigError 而非 AssertionError"""
    with pytest.raises(ConfigError):
        ContextConfig(relevance_weight=0.5, recency_weight=0.5 + 0.01)


def test_config_rejects_out_of_range_ratio():
    with pytest.raises(ConfigError):
        ContextConfig(reserve_ratio=1.5)


def test_config_rejects_negative_limits():
    with pytest.raises(ConfigError):
        ContextConfig(history_window=-1)


def test_config_rejects_inverted_budget_ratios():
    with pytest.raises(ConfigError):
        ContextConfig(min_budget_ratio=0.9, max_budget_ratio=0.5)


def test_config_rejects_non_positive_max_tokens():
    with pytest.raises(ConfigError):
        ContextConfig(max_tokens=0)


def test_config_rejects_out_of_range_min_relevance():
    with pytest.raises(ConfigError):
        ContextConfig(min_relevance=-0.1)
    with pytest.raises(ConfigError):
        ContextConfig(min_relevance=1.5)


def test_config_rejects_out_of_range_budget_ratio_bounds():
    with pytest.raises(ConfigError):
        ContextConfig(min_budget_ratio=0.2, max_budget_ratio=1.5)
    with pytest.raises(ConfigError):
        ContextConfig(min_budget_ratio=-0.1, max_budget_ratio=0.5)


@pytest.mark.parametrize(
    "field_name", ["memory_limit", "rag_limit", "history_window", "cache_max_size"]
)
def test_config_rejects_negative_limit_fields(field_name):
    with pytest.raises(ConfigError):
        ContextConfig(**{field_name: -1})


def test_config_rejects_non_positive_cache_ttl():
    with pytest.raises(ConfigError):
        ContextConfig(cache_ttl_seconds=0)


def test_config_rejects_out_of_range_weights():
    with pytest.raises(ConfigError):
        ContextConfig(relevance_weight=1.5, recency_weight=-0.5)
    with pytest.raises(ConfigError):
        ContextConfig(relevance_weight=-0.2, recency_weight=1.2)
    # 分支级钉扎：两个权重必然同时越界（和为 1.0），只断言抛出会被
    # 另一条范围检查兜住；此处断言消息，让任一条检查都能单独被变异杀掉。
    with pytest.raises(ConfigError, match="relevance_weight 必须在"):
        ContextConfig(relevance_weight=1.5, recency_weight=-0.5)
    with pytest.raises(ConfigError, match="recency_weight 必须在"):
        ContextConfig(relevance_weight=0.5, recency_weight=1.2)


def test_section_holds_title_and_body():
    section = ContextSection(title="Task", body="做什么")
    assert section.title == "Task"
    assert section.body == "做什么"


def test_stats_to_dict_is_serializable():
    stats = BuildStats(
        candidates_total=3,
        candidates_by_source={"system_instruction": 1, "memory": 2},
        selected_total=2,
        selected_by_source={"system_instruction": 1, "memory": 1},
        dropped_by_relevance=1,
        dropped_by_budget=0,
        structured_tokens=100,
        final_tokens=90,
        selected_tokens=60,
        budget=BudgetInfo(
            policy="heuristic",
            complexity=0.40123,
            requested_max_tokens=3000,
            scaled_max_tokens=2100,
            reserved_tokens=420,
            available_tokens=1680,
        ),
        token_utilization=0.042857,
        compressed=True,
        compression_ratio=0.98765,
        cache_hits=1,
        cache_misses=2,
        duration_ms=1.2345,
        experiment="scoring_v1",
        variant="control",
    )
    payload = stats.to_dict()
    assert set(payload) == {f.name for f in fields(BuildStats)}
    assert payload["budget"]["scaled_max_tokens"] == 2100
    assert payload["experiment"] == "scoring_v1"
    assert payload["duration_ms"] == 1.23
    assert payload["token_utilization"] == 0.0429
    assert payload["compression_ratio"] == 0.9877
    assert payload["budget"]["complexity"] == 0.4012
    assert "candidates=3" in stats.summary()
    assert "scoring_v1/control" in stats.summary()


def test_build_result_bundles_context_and_stats():
    stats = BuildStats(
        candidates_total=1,
        candidates_by_source={"system_instruction": 1},
        selected_total=1,
        selected_by_source={"system_instruction": 1},
        dropped_by_relevance=0,
        dropped_by_budget=0,
        structured_tokens=10,
        final_tokens=10,
        selected_tokens=0,
        budget=BudgetInfo(
            policy="heuristic",
            complexity=0.0,
            requested_max_tokens=3000,
            scaled_max_tokens=1500,
            reserved_tokens=300,
            available_tokens=1200,
        ),
        token_utilization=0.0033,
        compressed=False,
        compression_ratio=1.0,
        cache_hits=0,
        cache_misses=1,
        duration_ms=0.5,
        experiment=None,
        variant=None,
    )
    result = BuildResult(context="ctx", stats=stats)
    assert result.context == "ctx"
    assert result.stats is stats


# --- Task 7: 骨架 / token 计数 / 预算 ---

from hello_agents.context.budget import HeuristicBudgetPolicy
from hello_agents.context.builder import (
    ContextBuilder,
    _count_by_source,
    _parse_timestamp,
)
from hello_agents.context.scoring import KeywordOverlapScorer


class _FixedPolicy:
    """恒定返回指定复杂度的策略替身"""

    name = "fixed"

    def __init__(self, complexity: float) -> None:
        self.complexity = complexity

    def estimate(self, query, *, history, system_instructions) -> float:
        return self.complexity


class _DictCacheScorer:
    """打分器替身：cache 不是 TTLCache，钉 _cache_counters 的跳过分支"""

    def __init__(self) -> None:
        self.cache: dict[str, int] = {"hits": 99, "misses": 99}

    def score(self, content: str, query: str) -> float:
        return 0.5

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return [0.5] * len(contents)


def test_count_tokens_uses_tiktoken():
    """修复 B10：统一走 tiktoken，不再用中英文字符启发式"""
    builder = ContextBuilder()
    text = "用户喜欢深蓝色 hello world"
    assert builder._count_tokens(text) == len(builder.encoder.encode(text))
    # F9 加强：钉住 cl100k_base 对固定串的已知 token 数，杀死 len(text) 启发式
    assert builder._count_tokens(text) == 13
    assert builder._count_tokens("") == 0


def test_budget_scales_with_complexity():
    config = ContextConfig(
        max_tokens=1000, min_budget_ratio=0.5, max_budget_ratio=1.0, reserve_ratio=0.2
    )
    builder = ContextBuilder(config)
    simple = builder._compute_budget(config, "你好", [], None)
    complex_query = "如何根据文档配置向量库" + "详" * 300
    complex_budget = builder._compute_budget(config, complex_query, [], None)
    assert simple.scaled_max_tokens < complex_budget.scaled_max_tokens
    assert simple.requested_max_tokens == 1000
    assert simple.policy == "heuristic"
    assert simple.complexity < complex_budget.complexity


def test_budget_reserves_ratio_for_system_instructions():
    """修复 B11：reserve_ratio 必须真正生效"""
    config = ContextConfig(max_tokens=2000, reserve_ratio=0.3)
    builder = ContextBuilder(config)
    info = builder._compute_budget(config, "你好", [], "系统指令")
    assert info.reserved_tokens == int(info.scaled_max_tokens * 0.3)
    assert info.available_tokens == info.scaled_max_tokens - info.reserved_tokens


def test_budget_uses_injected_policy():
    config = ContextConfig(max_tokens=1000)
    builder = ContextBuilder(config, budget_policy=_FixedPolicy(1.0))
    info = builder._compute_budget(config, "任意查询", [], None)
    assert info.policy == "fixed"
    assert info.scaled_max_tokens == 1000
    assert info.complexity == 1.0


def test_budget_formula_pinned_at_low_and_mid_complexity():
    """修复 F5：钉住缩放公式低端 0.0 与中点 0.5，防退化为 max_ratio * complexity"""
    config = ContextConfig(
        max_tokens=1000, min_budget_ratio=0.5, max_budget_ratio=1.0, reserve_ratio=0.2
    )
    low = ContextBuilder(config, budget_policy=_FixedPolicy(0.0))
    low_info = low._compute_budget(config, "任意查询", [], None)
    assert low_info.scaled_max_tokens == 500
    # T4：定点自洽，防 B11 用例被单独改动时失守
    assert low_info.reserved_tokens + low_info.available_tokens == 500
    assert low_info.reserved_tokens == 100
    assert low_info.available_tokens == 400
    mid = ContextBuilder(config, budget_policy=_FixedPolicy(0.5))
    mid_info = mid._compute_budget(config, "任意查询", [], None)
    assert mid_info.scaled_max_tokens == 750


def test_budget_clamps_complexity_out_of_range():
    """修复 F7：复杂度钳制到 [0, 1]，scaled 跟钳后值走"""
    config = ContextConfig(
        max_tokens=1000, min_budget_ratio=0.5, max_budget_ratio=1.0, reserve_ratio=0.2
    )
    over = ContextBuilder(config, budget_policy=_FixedPolicy(2.0))
    over_info = over._compute_budget(config, "任意查询", [], None)
    assert over_info.complexity == 1.0
    assert over_info.scaled_max_tokens == 1000
    under = ContextBuilder(config, budget_policy=_FixedPolicy(-1.0))
    under_info = under._compute_budget(config, "任意查询", [], None)
    assert under_info.complexity == 0.0
    assert under_info.scaled_max_tokens == 500


def test_budget_scaled_max_tokens_is_at_least_one():
    config = ContextConfig(max_tokens=1, min_budget_ratio=0.1, max_budget_ratio=0.1)
    builder = ContextBuilder(config)
    assert builder._compute_budget(config, "你好", [], None).scaled_max_tokens >= 1


def test_config_budget_policy_is_used_when_not_injected():
    config = ContextConfig(budget_policy=_FixedPolicy(0.0))
    builder = ContextBuilder(config)
    assert builder.budget_policy.name == "fixed"


def test_explicit_policy_beats_config_policy():
    config = ContextConfig(budget_policy=_FixedPolicy(0.0))
    builder = ContextBuilder(config, budget_policy=HeuristicBudgetPolicy())
    assert builder.budget_policy.name == "heuristic"


def test_parse_timestamp_accepts_iso_and_datetime():
    now = datetime.now(tz=UTC)
    assert _parse_timestamp(now) is now
    # F2：精确比对而非 year == 2026（今天正是 2026 年，钉不住 fromisoformat）
    parsed = _parse_timestamp("2026-09-23T10:00:00")
    assert parsed == datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)


def test_parse_timestamp_keeps_offset_aware_iso_string():
    """T2：带时区偏移的 ISO 串必须原样返回，不得 replace 成 UTC 壁钟改写"""
    from datetime import timedelta, timezone

    parsed = _parse_timestamp("2026-09-23T12:00:00+02:00")
    assert parsed == datetime(
        2026, 9, 23, 12, 0, 0, tzinfo=timezone(timedelta(hours=2))
    )
    assert parsed.utcoffset() == timedelta(hours=2)


def test_parse_timestamp_fallback_is_tz_aware_now():
    """F3：退回值必须是「当前时刻」的 tz-aware UTC，而非任意 datetime"""
    for raw in (None, "not-a-date"):
        result = _parse_timestamp(raw)
        assert isinstance(result, datetime)
        assert result.tzinfo is UTC
        delta = abs((result - datetime.now(tz=UTC)).total_seconds())
        assert delta < 1


def test_parse_timestamp_normalizes_naive_to_utc():
    """F3b：解析处归一，naive 入参出口带 UTC，与 aware 入参结果相等"""
    naive = datetime.fromisoformat("2026-09-23T10:00:00")
    aware = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
    assert naive.tzinfo is None
    assert _parse_timestamp(naive) == _parse_timestamp(aware)
    assert _parse_timestamp(naive) == aware
    assert _parse_timestamp(naive).tzinfo is UTC


def test_count_by_source_always_lists_all_five_types():
    packets = [
        ContextPacket(
            content="a", timestamp=datetime.now(tz=UTC), metadata={"type": "rag"}
        ),
        ContextPacket(
            content="b", timestamp=datetime.now(tz=UTC), metadata={"type": "rag"}
        ),
        ContextPacket(content="c", timestamp=datetime.now(tz=UTC)),
    ]
    counts = _count_by_source(packets)
    assert counts == {
        "system_instruction": 0,
        "memory": 0,
        "rag": 2,
        "history": 0,
        "custom": 1,
    }


def test_count_by_source_unknown_type_falls_back_to_custom():
    """F4：未知 type 归入 custom，不得泄漏第六个键"""
    packets = [
        ContextPacket(
            content="a", timestamp=datetime.now(tz=UTC), metadata={"type": "bogus"}
        ),
    ]
    counts = _count_by_source(packets)
    assert counts == {
        "system_instruction": 0,
        "memory": 0,
        "rag": 0,
        "history": 0,
        "custom": 1,
    }
    assert len(counts) == 5


def test_cache_counters_include_scorer_cache():
    from hello_agents.context.scoring import EmbeddingSimilarityScorer
    from hello_agents.memory import TFIDFEmbedding

    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
    builder = ContextBuilder(relevance_scorer=scorer)
    scorer.score("用户喜欢爬山", "用户喜欢")
    hits, misses = builder._cache_counters()
    assert hits + misses > 0


def test_cache_counters_include_builder_cache():
    """F6：builder 自己的 self.cache 计数必须纳入，精确值而非 > 0"""
    builder = ContextBuilder()
    builder.cache.put("k", "v")
    assert builder.cache.get("k") == "v"
    assert builder.cache.get("missing") is None
    hits, misses = builder._cache_counters()
    assert (hits, misses) == (1, 1)


def test_cache_counters_skip_scorer_without_cache():
    """F8②：scorer 无 cache 属性时跳过，不抛且只含 builder 自己的计数"""
    builder = ContextBuilder(relevance_scorer=KeywordOverlapScorer())
    builder.cache.put("k", "v")
    builder.cache.get("k")
    hits, misses = builder._cache_counters()
    assert (hits, misses) == (1, 0)


def test_cache_counters_skip_non_ttl_scorer_cache():
    """F8②加强：scorer.cache 不是 TTLCache 时必须跳过，不得当 TTLCache 用"""
    builder = ContextBuilder(relevance_scorer=_DictCacheScorer())
    builder.cache.put("k", "v")
    builder.cache.get("k")
    hits, misses = builder._cache_counters()
    assert (hits, misses) == (1, 0)


def test_init_reads_cache_settings_from_config():
    """T1：cache_max_size / cache_ttl_seconds 必须接到 TTLCache，而非吃默认值"""
    builder = ContextBuilder(ContextConfig(cache_max_size=7, cache_ttl_seconds=12.5))
    assert builder.cache.max_size == 7
    assert builder.cache.ttl_seconds == 12.5


def test_init_raises_config_error_when_tiktoken_unavailable(monkeypatch):
    """F8①：tiktoken 编码器缺失一律转 ConfigError"""
    import tiktoken

    def _boom(_name: str):
        raise RuntimeError("encoder missing")

    monkeypatch.setattr(tiktoken, "get_encoding", _boom)
    with pytest.raises(ConfigError):
        ContextBuilder()
