"""ContextBuilder 测试：数据类型 / 四阶段全链路 / 缺陷回归"""

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
            complexity=0.4,
            requested_max_tokens=3000,
            scaled_max_tokens=2100,
            reserved_tokens=420,
            available_tokens=1680,
        ),
        token_utilization=0.0429,
        compressed=True,
        compression_ratio=0.9,
        cache_hits=1,
        cache_misses=2,
        duration_ms=1.23,
        experiment="scoring_v1",
        variant="control",
    )
    payload = stats.to_dict()
    assert payload["budget"]["scaled_max_tokens"] == 2100
    assert payload["experiment"] == "scoring_v1"
    assert "candidates=3" in stats.summary()
    assert "scoring_v1/control" in stats.summary()


def test_build_result_bundles_context_and_stats():
    result = BuildResult(context="ctx", stats=None)
    assert result.context == "ctx"
