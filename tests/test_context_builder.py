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
