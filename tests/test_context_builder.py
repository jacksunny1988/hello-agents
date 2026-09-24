"""ContextBuilder 测试：数据类型 / 四阶段全链路 / 缺陷回归"""

from dataclasses import fields
from datetime import UTC, datetime, timedelta

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


# --- Task 8: Gather ---

from hello_agents.core import Message, MessageRole
from hello_agents.tools.response import ToolResponse, ToolStatus


class _FakeTool:
    """工具替身：记录 payload 并返回预设响应"""

    def __init__(self, data=None, status=ToolStatus.SUCCESS, raises=None) -> None:
        self.data = data or {}
        self.status = status
        self.raises = raises
        self.calls: list[dict] = []

    def run(self, input_data, **kwargs):
        self.calls.append(input_data)
        if self.raises is not None:
            raise self.raises
        return ToolResponse(status=self.status, text="", data=self.data)


def _memory_hits(*contents, score=0.8, importance=None):
    hits = []
    for index, content in enumerate(contents):
        metadata = {} if importance is None else {"importance": importance}
        hits.append(
            {
                "id": f"m{index}",
                "content": content,
                "memory_type": "semantic",
                "metadata": metadata,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "expires_at": None,
                "score": score,
            }
        )
    return hits


def test_gather_builds_system_instruction_packet():
    builder = ContextBuilder()
    packets = builder._gather("查询", [], "你是助手", [], builder.config)
    assert len(packets) == 1
    assert packets[0].metadata["type"] == "system_instruction"
    # B6c：priority 属包形状契约，整 dict 钉死防丢字段
    assert packets[0].metadata == {"type": "system_instruction", "priority": "high"}
    assert packets[0].relevance_score == 1.0
    assert packets[0].token_count == builder._count_tokens("你是助手")
    # B5 等长对照①：cl100k_base 实测 tokens=5、len=4（「你是助手」4 个 CJK 字符）
    assert packets[0].token_count == 5
    assert packets[0].token_count != len(packets[0].content)


def test_gather_reuses_cached_system_instruction_packet():
    """B4：缓存键必须绑定指令本体 —— 同指令复用、异指令不串包

    变异（sha256 指令哈希 → 常量键）会让后一条查询吃到前一条的系统指令，
    只断言「同指令 is」钉不住串包，故三段缺一不可。
    """
    builder = ContextBuilder()
    first = builder._gather("查询", [], "你是助手", [], builder.config)
    second = builder._gather("另一查询", [], "你是助手", [], builder.config)
    # ① 同指令：同一对象，命中缓存
    assert first[0] is second[0]
    third = builder._gather("第三查询", [], "你是严格的审校员", [], builder.config)
    # ② 异指令：不得复用
    assert first[0] is not third[0]
    # ③ 异指令 content 各自正确（常量键下会串成同一条）
    assert first[0].content == "你是助手"
    assert third[0].content == "你是严格的审校员"


def test_gather_skips_empty_system_instructions():
    """G-k：空串是 falsy，不得产出系统指令包"""
    builder = ContextBuilder()
    assert builder._gather("查询", [], "", [], builder.config) == []


def test_gather_calls_memory_tool_with_real_contract():
    """修复 B13：action 必须是 recall，且不得传记忆层不识别的参数

    B1：limit 必须接到 config.memory_limit —— 故用非默认 3 并断言字面量，
    自指式 `== builder.config.memory_limit` 在默认配置下与硬编码 10 不可区分。
    """
    tool = _FakeTool(data={"hits": _memory_hits("用户喜欢爬山")})
    builder = ContextBuilder(memory_tool=tool, config=ContextConfig(memory_limit=3))
    packets = builder._gather("爬山", [], None, [], builder.config)
    payload = tool.calls[0]
    assert payload["action"] == "recall"
    assert payload["query"] == "爬山"
    assert payload["limit"] == 3
    assert "min_importance" not in payload
    assert "min_score" not in payload
    assert packets[0].metadata["type"] == "memory"
    assert packets[0].relevance_score == 0.8
    # B5：命中包 token_count 必须是 tiktoken 口径
    assert packets[0].token_count == builder._count_tokens("用户喜欢爬山")
    # B5 等长对照①：cl100k_base 实测 tokens=8、len=6（6 个 CJK 字符）
    assert packets[0].token_count == 8
    assert packets[0].token_count != len(packets[0].content)


def test_gather_calls_rag_tool_with_real_contract():
    """修复 B13：action 必须是 query，条数参数是 top_k

    B1：top_k 必须接到 config.rag_limit —— 故用非默认 2 并断言字面量，
    自指式 `== builder.config.rag_limit` 在默认配置下与硬编码 5 不可区分。
    """
    tool = _FakeTool(data={"chunks": _memory_hits("向量库配置说明", score=0.9)})
    builder = ContextBuilder(rag_tool=tool, config=ContextConfig(rag_limit=2))
    packets = builder._gather("向量库", [], None, [], builder.config)
    payload = tool.calls[0]
    assert payload["action"] == "query"
    assert payload["question"] == "向量库"
    assert payload["top_k"] == 2
    assert packets[0].metadata["type"] == "rag"
    # G-h：与 memory 侧 relevance_score == 0.8 对称，钉住分数透传
    assert packets[0].relevance_score == 0.9


def test_gather_skips_missing_tools():
    """修复 B2：未注入工具时必须静默跳过而非 AttributeError"""
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [], builder.config)
    assert packets == []


def test_gather_skips_tool_error_response():
    # G-a：data 必须带非空 hits，否则删掉 ERROR 检查后 hits 仍为空、断言照样过
    tool = _FakeTool(data={"hits": _memory_hits("x")}, status=ToolStatus.ERROR)
    builder = ContextBuilder(memory_tool=tool)
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_skips_tool_exception():
    tool = _FakeTool(raises=RuntimeError("boom"))
    builder = ContextBuilder(memory_tool=tool)
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_skips_rag_tool_error_response():
    # G-b：RAG 侧 ERROR 分支原本零覆盖，且 chunks 必须非空才钉得住
    tool = _FakeTool(
        data={"chunks": _memory_hits("向量库配置说明", score=0.9)},
        status=ToolStatus.ERROR,
    )
    builder = ContextBuilder(rag_tool=tool)
    assert builder._gather("向量库", [], None, [], builder.config) == []


def test_gather_skips_rag_tool_exception():
    # G-b：RAG 侧吞异常分支原本零覆盖
    tool = _FakeTool(raises=RuntimeError("boom"))
    builder = ContextBuilder(rag_tool=tool)
    assert builder._gather("向量库", [], None, [], builder.config) == []


def test_gather_filters_by_min_source_score():
    tool = _FakeTool(data={"hits": _memory_hits("低分命中", score=0.05)})
    builder = ContextBuilder(
        memory_tool=tool, config=ContextConfig(min_source_score=0.5)
    )
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_keeps_hit_at_exact_min_source_score():
    """G-d②：score == min_source_score 是保留（严格小于才丢），钉 < 而非 <="""
    tool = _FakeTool(data={"hits": _memory_hits("边界命中", score=0.5)})
    builder = ContextBuilder(
        memory_tool=tool, config=ContextConfig(min_source_score=0.5)
    )
    assert len(builder._gather("查询", [], None, [], builder.config)) == 1


def test_gather_filters_by_min_importance():
    tool = _FakeTool(data={"hits": _memory_hits("低重要度", importance=0.1)})
    builder = ContextBuilder(memory_tool=tool, config=ContextConfig(min_importance=0.5))
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_keeps_hit_at_exact_min_importance():
    """G-d③：importance == min_importance 是保留，钉 < 而非 <="""
    tool = _FakeTool(data={"hits": _memory_hits("边界重要度", importance=0.5)})
    builder = ContextBuilder(memory_tool=tool, config=ContextConfig(min_importance=0.5))
    assert len(builder._gather("查询", [], None, [], builder.config)) == 1


def test_gather_keeps_hits_without_importance_metadata():
    tool = _FakeTool(data={"hits": _memory_hits("无重要度字段")})
    builder = ContextBuilder(memory_tool=tool, config=ContextConfig(min_importance=0.5))
    assert len(builder._gather("查询", [], None, [], builder.config)) == 1


def test_gather_defaults_missing_content_to_empty_string():
    """B6b：命中缺 content 键时正文是空串，不是占位文本

    变异 `hit.get("content", "")` → `hit.get("content", "占位")` /
    `hit.get("content") or "占位"` 都会在缺键时给出占位文本。
    """
    hit = _memory_hits("有正文")[0]
    del hit["content"]
    tool = _FakeTool(data={"hits": [hit]})
    builder = ContextBuilder(memory_tool=tool)
    packets = builder._gather("查询", [], None, [], builder.config)
    assert len(packets) == 1
    assert packets[0].content == ""


def test_gather_leaves_scoreless_hits_unscored():
    """G-c：MemoryItem.to_dict() 不含 score，Task 12 前 RAG 在线路径全是缺分命中

    缺 score 时 source_score 记 0.0，relevance_score 留 None 给 Task 9 计算。
    这是当下主路而非边角，不得被防御式编程省略。
    """
    hits = _memory_hits("缺分命中")
    del hits[0]["score"]
    tool = _FakeTool(data={"chunks": hits})
    builder = ContextBuilder(rag_tool=tool)
    packets = builder._gather("向量库", [], None, [], builder.config)
    assert len(packets) == 1
    assert packets[0].relevance_score is None
    assert packets[0].metadata["source_score"] == 0.0


def test_gather_keeps_explicit_zero_score():
    """B2：显式 0 分不得被 falsy 判断抬成满分

    变异 `float(raw_score)` → `float(raw_score or 1.0)` 会把 0.0 抬成 1.0，
    因为 0.0 是 falsy。既有 score 取值只覆盖 0.8/0.9/0.05 与「缺键」（走 None
    分支、不受此变异影响），唯独缺显式 0.0 —— 这里补上。
    """
    tool = _FakeTool(data={"hits": _memory_hits("零分命中", score=0.0)})
    builder = ContextBuilder(memory_tool=tool)
    packets = builder._gather("查询", [], None, [], builder.config)
    assert len(packets) == 1
    assert packets[0].relevance_score == 0.0
    assert packets[0].metadata["source_score"] == 0.0


def test_gather_drops_scoreless_hits_when_min_source_score_positive():
    """G-d①：缺 score 按 0.0 过滤，min_source_score>0 时整体丢光（spec 语义）"""
    hits = _memory_hits("缺分命中")
    del hits[0]["score"]
    tool = _FakeTool(data={"chunks": hits})
    builder = ContextBuilder(rag_tool=tool, config=ContextConfig(min_source_score=0.1))
    assert builder._gather("向量库", [], None, [], builder.config) == []


def test_gather_skips_history_when_window_is_zero():
    """B3：history_window=0 是合法配置，语义为「不纳入历史」

    `window <= 0` 是承重护栏：`__post_init__` 只拒负数、0 合法。删掉护栏后
    `history[-window:]` == `history[-0:]` == `history[0:]` == 全量，语义倒置。
    """
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(3)]
    builder = ContextBuilder(config=ContextConfig(history_window=0))
    assert builder._gather("查询", history, None, [], builder.config) == []


def test_gather_trims_history_to_window():
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(10)]
    builder = ContextBuilder(config=ContextConfig(history_window=3))
    packets = builder._gather("查询", history, None, [], builder.config)
    assert len(packets) == 3
    assert "第7轮" in packets[0].content
    assert "第9轮" in packets[2].content
    # G-e：position 是窗口相对下标（0=最旧），不是全局下标 [7,8,9]
    assert [p.metadata["position"] for p in packets] == [0, 1, 2]


def test_gather_marks_history_position_and_type():
    """修复 B14：历史消息用 position 承载新近性，不读 msg.timestamp

    B6a：正文格式是 `{{role}}: {{content}}`，role 前缀必须在串里。
    """
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(3)]
    builder = ContextBuilder()
    packets = builder._gather("查询", history, None, [], builder.config)
    assert [p.metadata["position"] for p in packets] == [0, 1, 2]
    assert all(p.metadata["type"] == "history" for p in packets)
    assert packets[0].relevance_score is None
    # B6a：完整串钉死，防 role 前缀被去掉
    assert packets[0].content == "user: 第0轮"
    # B5：历史包 token_count 必须是 tiktoken 口径
    assert packets[0].token_count == builder._count_tokens(packets[0].content)
    # B5 等长对照①：cl100k_base 实测 tokens=6、len=9（"user: 第0轮" 9 个字符）
    assert packets[0].token_count == 6
    assert packets[0].token_count != len(packets[0].content)


def test_gather_fills_token_count_for_custom_packets():
    custom = ContextPacket(content="自定义信息", timestamp=datetime.now(tz=UTC))
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [custom], builder.config)
    assert packets[0].token_count == builder._count_tokens("自定义信息")
    # B5 等长对照①：cl100k_base 实测 tokens=3、len=5（5 个 CJK 字符）
    assert packets[0].token_count == 3
    assert packets[0].token_count != len(packets[0].content)


def test_gather_keeps_explicit_token_count_on_custom_packets():
    """G-f：显式非零 token_count 是调用方契约，不得被无条件重算覆盖"""
    custom = ContextPacket(
        content="自定义信息", timestamp=datetime.now(tz=UTC), token_count=7
    )
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [custom], builder.config)
    assert packets[0].token_count == 7


def test_gather_keeps_explicit_relevance_score_on_custom_packets():
    """修复 B8：预置 0.5 不得被当作「未评分」

    G-g：本例钉的是 absence-of-mangling（_gather 不碰自定义包的
    relevance_score）；真正的 B8 钉扎（预置值不被重算）在 Task 9 选择阶段。
    """
    custom = ContextPacket(
        content="自定义信息", timestamp=datetime.now(tz=UTC), relevance_score=0.5
    )
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [custom], builder.config)
    assert packets[0].relevance_score == 0.5


def test_gather_orders_all_five_sources():
    """G-i：五来源齐全时的追加顺序，Task 9 贪心填充对顺序敏感"""
    memory_tool = _FakeTool(data={"hits": _memory_hits("用户喜欢爬山")})
    rag_tool = _FakeTool(data={"chunks": _memory_hits("向量库配置说明", score=0.9)})
    history = [Message(role=MessageRole.USER, content="第0轮")]
    custom = ContextPacket(content="自定义信息", timestamp=datetime.now(tz=UTC))
    builder = ContextBuilder(memory_tool=memory_tool, rag_tool=rag_tool)
    packets = builder._gather("查询", history, "你是助手", [custom], builder.config)
    assert [p.metadata.get("type", "custom") for p in packets] == [
        "system_instruction",
        "memory",
        "rag",
        "history",
        "custom",
    ]


# --- Task 9: Select ---


class _SpyScorer:
    """记录被打了分的文本，用于验证「未评分才重算」"""

    def __init__(self, value: float = 0.1) -> None:
        self.value = value
        self.scored: list[str] = []

    def score(self, content: str, query: str) -> float:
        return self.value

    def score_many(self, contents: list[str], query: str) -> list[float]:
        self.scored.extend(contents)
        return [self.value] * len(contents)


class _RaisingScorer:
    """打分器替身：score_many 抛异常，验证 _select 的降级防护（H-g）"""

    def score(self, content: str, query: str) -> float:
        raise RuntimeError("打分器故障")

    def score_many(self, contents: list[str], query: str) -> list[float]:
        raise RuntimeError("打分器故障")


class _ShortListScorer:
    """打分器替身：返回比请求数更少的分数，验证不静默 zip 截断（H-i）"""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def score(self, content: str, query: str) -> float:
        return self.values[0] if self.values else 0.0

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return list(self.values)


class _LongListScorer:
    """打分器替身：返回比请求数更多的分数，验证超额不设防会被抓（F5）"""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def score(self, content: str, query: str) -> float:
        return self.values[0] if self.values else 0.0

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return list(self.values)


class _OutOfRangeScorer:
    """打分器替身：返回越界分数，验证写入前钳位到 [0,1]（F7）"""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def score(self, content: str, query: str) -> float:
        return self.values[0] if self.values else 0.0

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return list(self.values)


def _select_config(**overrides) -> ContextConfig:
    base = {"relevance_weight": 1.0, "recency_weight": 0.0, "min_relevance": 0.0}
    base.update(overrides)
    return ContextConfig(**base)


def test_select_does_not_rescore_explicit_half_score():
    """修复 B8：预置分数不得被重算，None 才重算

    F4：补预置 0.0 的 other 包 —— `is None` 若误写成 falsy 判断，0.0 会被
    误判待算并被 spy 重算，`scored` 会多出 "zero"、zero 分会被改写。
    """
    scorer = _SpyScorer(0.1)
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=scorer)
    explicit = ContextPacket(
        content="explicit",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=0.5,
    )
    zero = ContextPacket(
        content="zero",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=0.0,
    )
    unscored = ContextPacket(
        content="unscored", timestamp=datetime.now(tz=UTC), token_count=1
    )
    builder._select([explicit, zero, unscored], "q", 100, 100, config)
    assert scorer.scored == ["unscored"]  # 不含 "zero"：0.0 是预置值而非待算
    assert explicit.relevance_score == 0.5
    assert zero.relevance_score == 0.0
    assert unscored.relevance_score == 0.1


def test_select_keeps_small_packet_when_large_one_does_not_fit():
    """修复 B9：放不下的大包应跳过而非终止选择"""
    config = _select_config()
    builder = ContextBuilder(config)
    big = ContextPacket(
        content="big",
        timestamp=datetime.now(tz=UTC),
        token_count=20,
        relevance_score=1.0,
    )
    small = ContextPacket(
        content="small",
        timestamp=datetime.now(tz=UTC),
        token_count=5,
        relevance_score=0.5,
    )
    selected, _, dropped_by_budget = builder._select([big, small], "q", 10, 100, config)
    assert [packet.content for packet in selected] == ["small"]
    assert dropped_by_budget == 1


def test_select_drops_packets_below_min_relevance():
    config = _select_config(min_relevance=0.5)
    builder = ContextBuilder(config)
    weak = ContextPacket(
        content="weak",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=0.2,
    )
    selected, dropped_by_relevance, _ = builder._select([weak], "q", 100, 100, config)
    assert selected == []
    assert dropped_by_relevance == 1


def test_select_always_keeps_system_instructions():
    """H-a：系统指令不参与评分，也永不被相关性阈值淘汰

    system 包 relevance_score=0.0（低于阈值）：有拆分则恒保留；
    若拆分被删掉、system 被当 other，0.0 < 0.99 会被淘汰 → selected == []。
    """
    config = _select_config(min_relevance=0.99)
    builder = ContextBuilder(config)
    system = ContextPacket(
        content="你是助手",
        timestamp=datetime.now(tz=UTC),
        token_count=4,
        relevance_score=0.0,
        metadata={"type": "system_instruction"},
    )
    selected, dropped_by_relevance, _ = builder._select([system], "q", 100, 100, config)
    assert selected == [system]
    assert dropped_by_relevance == 0


def test_select_skips_scoring_when_system_instructions_exceed_budget():
    """H-b：系统指令超 scaled_max_tokens 时早退，跳过打分

    available_tokens=100：若不早退，other（1 token）必然入选、断言会翻；
    spy 断言 scorer.scored == [] 直接观测「跳过打分」。
    """
    scorer = _SpyScorer(0.9)
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=scorer)
    system = ContextPacket(
        content="很长的系统指令",
        timestamp=datetime.now(tz=UTC),
        token_count=50,
        relevance_score=1.0,
        metadata={"type": "system_instruction"},
    )
    other = ContextPacket(content="候选", timestamp=datetime.now(tz=UTC), token_count=1)
    selected, _, dropped_by_budget = builder._select(
        [system, other], "q", 100, 10, config
    )
    assert selected == [system]
    assert dropped_by_budget == 1
    assert scorer.scored == []


def test_select_ranks_history_by_position():
    """修复 B14：历史消息按 position 而非时间戳计算新近性

    H-f：补端点断言，钉住 span = max(history_count - 1, 1)。
    """
    config = _select_config(relevance_weight=0.0, recency_weight=1.0)
    builder = ContextBuilder(config)
    older = ContextPacket(
        content="旧",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 0},
    )
    newer = ContextPacket(
        content="新",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 1},
    )
    selected, _, _ = builder._select([older, newer], "q", 100, 100, config)
    assert [packet.content for packet in selected] == ["新", "旧"]
    # H-f 端点：count=2 → span=1 → position=1 得 1.0、position=0 得 0.5
    # 若误写 span = history_count（=2），position=1 只会得 0.75
    assert builder._recency_of(newer, 2) == 1.0  # 严格 ==，钳位精确
    assert builder._recency_of(older, 2) == 0.5


def test_recency_decays_with_age():
    builder = ContextBuilder()
    fresh = builder._calculate_recency(datetime.now(tz=UTC))
    stale = builder._calculate_recency(datetime.now(tz=UTC) - timedelta(days=30))
    assert fresh > stale
    assert stale == 0.1  # 30 天 exp(-3)≈0.0498 必触底，具体值而非区间
    assert 0.1 <= fresh <= 1.0


def test_recency_decay_rate_pinned_at_day_boundary():
    """H-c：钉住衰减速率系数 -0.1 —— age=24h 精确命中 exp(-0.1)、age=0 命中 1.0"""
    import math

    builder = ContextBuilder()
    at_day = builder._calculate_recency(datetime.now(tz=UTC) - timedelta(hours=24))
    assert at_day == pytest.approx(math.exp(-0.1))
    at_zero = builder._calculate_recency(datetime.now(tz=UTC) + timedelta(seconds=1))
    assert at_zero == 1.0  # 严格 ==，age=0 时 exp(0)=1.0 精确


def test_recency_handles_timezone_aware_timestamp():
    builder = ContextBuilder()
    aware = datetime.now(tz=UTC) - timedelta(hours=1)
    score = builder._calculate_recency(aware)
    assert 0.1 <= score <= 1.0


def test_recency_matches_naive_and_aware_via_parse():
    """H-d：naive 与等值 aware 的归一发生在 _parse_timestamp（解析处归一）

    _calculate_recency 直接消费 _parse_timestamp 输出，不再二次归一。
    两条入口（datetime 对象 / ISO 字符串）都必须归一，等值输入的 recency 才相等。
    """
    builder = ContextBuilder()
    naive = datetime(2026, 9, 23, 10, 0, 0)  # noqa: DTZ001 - 故意构造 naive
    aware = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
    p_naive = _parse_timestamp(naive)
    p_aware = _parse_timestamp(aware)
    assert naive.tzinfo is None
    assert p_naive == p_aware
    assert p_naive == datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
    assert builder._calculate_recency(p_naive) == pytest.approx(
        builder._calculate_recency(p_aware)
    )
    # ISO 字符串入口的同一规则：naive 串与等值 aware 串归一后必须相等
    s_naive = _parse_timestamp("2026-09-23T10:00:00")
    s_aware = _parse_timestamp("2026-09-23T10:00:00+00:00")
    assert s_naive.tzinfo is not None
    assert s_naive == s_aware
    assert s_naive == datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
    assert builder._calculate_recency(s_naive) == pytest.approx(
        builder._calculate_recency(s_aware)
    )


def test_recency_uses_true_instant_of_offset_aware_timestamp():
    """H-d 红线钉扎：offset-aware 时间戳必须按绝对时刻求差，不得 replace 成 UTC 壁钟

    12:00+02:00 与 10:00+00:00 是同一绝对时刻，recency 必须相等。
    若 _calculate_recency 对 aware 时间戳做 replace(tzinfo=UTC)，+02:00 的壁钟
    会被当成 UTC 壁钟（改写绝对时刻，差 2 小时）→ recency 不等。
    时间锚点取「5 小时前」，保证落在衰减曲线非钳位区，避免两端钳位掩盖差异。
    """
    from datetime import timedelta as _td
    from datetime import timezone

    builder = ContextBuilder()
    base = datetime.now(tz=UTC) - _td(hours=5)
    offset = _parse_timestamp(base.astimezone(timezone(_td(hours=2))).isoformat())
    utc = _parse_timestamp(base.astimezone(UTC).isoformat())
    assert offset == utc
    assert offset.utcoffset() is not None
    assert builder._calculate_recency(offset) == pytest.approx(
        builder._calculate_recency(utc)
    )


def test_select_combines_weighted_sum_not_product():
    """H-e：加权和公式（w_rel*rel + w_rec*rec），不是乘积

    交叉数值（w=0.5/0.5）：
      A: rel=1.0, rec=0.1 → 加权和 0.550 / 乘积 0.100
      B: rel=0.5, rec=0.5 → 加权和 0.500 / 乘积 0.250
    加权和排序 A>B，乘积排序 B>A —— 正好相反，改写成 rel*rec 会被杀。
    """
    config = _select_config(relevance_weight=0.5, recency_weight=0.5)
    builder = ContextBuilder(config)
    packet_a = ContextPacket(
        content="A",
        timestamp=datetime.now(tz=UTC) - timedelta(days=30),
        token_count=1,
        relevance_score=1.0,
    )
    packet_b = ContextPacket(
        content="B",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=0.5,
        metadata={"type": "history", "position": 0},
    )
    # 分量锚点：A 的 30 天前时间戳 exp(-3)≈0.05 被钳到 0.1；B 的 position=0 精确 0.5
    assert builder._calculate_recency(packet_a.timestamp) == 0.1  # 下钳位精确
    assert builder._recency_of(packet_b, 1) == 0.5
    # 插入序与期望序相反，「忘排序」变异会被杀
    selected, _, _ = builder._select([packet_b, packet_a], "q", 100, 100, config)
    assert [packet.content for packet in selected] == ["A", "B"]


def test_select_degrades_when_scorer_raises():
    """H-g：score_many 抛异常不得穿透 _select，该批按 0.0 分降级参与后续流程"""
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=_RaisingScorer())
    candidate = ContextPacket(
        content="候选", timestamp=datetime.now(tz=UTC), token_count=1
    )
    selected, dropped_by_relevance, dropped_by_budget = builder._select(
        [candidate], "q", 100, 100, config
    )
    assert selected == [candidate]
    assert dropped_by_relevance == 0
    assert dropped_by_budget == 0
    assert candidate.relevance_score == 0.0


def test_select_degraded_packets_hit_relevance_threshold():
    """H-g：降级分 0.0 必须参与阈值判断，min_relevance>0 时被淘汰"""
    config = _select_config(min_relevance=0.3)
    builder = ContextBuilder(config, relevance_scorer=_RaisingScorer())
    candidate = ContextPacket(
        content="候选", timestamp=datetime.now(tz=UTC), token_count=1
    )
    selected, dropped_by_relevance, _ = builder._select(
        [candidate], "q", 100, 100, config
    )
    assert selected == []
    assert dropped_by_relevance == 1
    assert candidate.relevance_score == 0.0


def test_select_degrades_when_scorer_returns_short_list():
    """H-i：score_many 返回短列表不得静默 zip 截断，长度不等整批按 0.0 降级

    静默截断会让 first=0.9、second 仍为 None；整批降级则两者都是 0.0。
    """
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=_ShortListScorer([0.9]))
    first = ContextPacket(content="甲", timestamp=datetime.now(tz=UTC), token_count=1)
    second = ContextPacket(content="乙", timestamp=datetime.now(tz=UTC), token_count=1)
    selected, _, _ = builder._select([first, second], "q", 100, 100, config)
    assert first.relevance_score == 0.0
    assert second.relevance_score == 0.0
    assert [packet.content for packet in selected] == ["甲", "乙"]


def test_select_tolerates_naive_custom_packet_timestamp():
    """spec §6：naive 时间戳不得让 TypeError 穿透 _select（S1-1 / F1）

    custom 包的 timestamp 是调用方给的裸 datetime，可能 naive。_recency_of
    必须走 _parse_timestamp 归一（解析处归一），不得裸做 naive/aware 减法。
    """
    config = _select_config()
    builder = ContextBuilder(config)
    naive_packet = ContextPacket(
        content="naive",
        timestamp=datetime(2026, 9, 23, 10, 0, 0),  # noqa: DTZ001
        token_count=1,
        relevance_score=0.9,
    )
    aware_packet = ContextPacket(
        content="aware",
        timestamp=datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC),
        token_count=1,
        relevance_score=0.9,
    )
    selected, _, _ = builder._select(
        [naive_packet, aware_packet], "q", 100, 100, config
    )
    assert len(selected) == 2
    # 等值时刻 → recency 相等（走 _parse_timestamp 归一）。
    # 两次 _calculate_recency 各自采样 datetime.now()，严格 == 结构性不可达
    # （实测 200 次 0 命中、最大差 2.6e-11），曲线区按房规用 approx 交叉比对。
    assert builder._recency_of(naive_packet, 0) == pytest.approx(
        builder._recency_of(aware_packet, 0)
    )
    # 触底区两包同钳到 0.1，严格 == 可确定性命中，钉住「naive 归一后与 aware 同值」
    old_naive = ContextPacket(
        content="old-naive",
        timestamp=datetime(2020, 1, 1),  # noqa: DTZ001
        token_count=1,
        relevance_score=0.9,
    )
    old_aware = ContextPacket(
        content="old-aware",
        timestamp=datetime(2020, 1, 1, tzinfo=UTC),
        token_count=1,
        relevance_score=0.9,
    )
    assert builder._recency_of(old_naive, 0) == builder._recency_of(old_aware, 0)
    assert builder._recency_of(old_naive, 0) == 0.1


def test_recency_history_span_midpoint():
    """H-f 补强：n=3 中点钉住 span 分母（S1-2 / S2-1）

    n≤2 时「position/span」「省略 span」「span=history_count」三者同值，
    端点断言结构性杀不掉分母；n=3 中点三者互异：0.75 / 1.0 / 0.667。
    """
    builder = ContextBuilder()
    oldest = ContextPacket(
        content="旧",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 0},
    )
    mid = ContextPacket(
        content="中",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 1},
    )
    newest = ContextPacket(
        content="新",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 2},
    )
    # span = max(3-1, 1) = 2 → 中点 0.5+0.5*(1/2) = 0.75（二进制精确）
    assert builder._recency_of(mid, 3) == 0.75
    assert builder._recency_of(oldest, 3) == 0.5
    assert builder._recency_of(newest, 3) == 1.0


def test_select_weighted_sum_not_max():
    """H-e 补强：加权和公式不是 max（S1-3 / F3）

    不等权 w=(0.7, 0.3)：
      A: rel=0.3, rec=1.0 → 加权和 0.510 / max(0.21, 0.30) = 0.300
      B: rel=0.6, rec=0.2 → 加权和 0.480 / max(0.42, 0.06) = 0.420
    加权和 A>B，max 则 B>A —— 排序正好相反。插入序与期望序相反。
    """
    config = _select_config(relevance_weight=0.7, recency_weight=0.3)
    builder = ContextBuilder(config)
    packet_a = ContextPacket(
        content="A",
        timestamp=datetime.now(tz=UTC)
        + timedelta(hours=1),  # age 钳到 0 → rec 精确 1.0
        token_count=1,
        relevance_score=0.3,
    )
    packet_b = ContextPacket(
        content="B",
        timestamp=datetime.now(tz=UTC) - timedelta(days=16),  # exp(-1.6)≈0.202
        token_count=1,
        relevance_score=0.6,
    )
    assert builder._calculate_recency(packet_a.timestamp) == 1.0
    assert builder._calculate_recency(packet_b.timestamp) == pytest.approx(
        0.2, abs=0.01
    )
    selected, _, _ = builder._select([packet_b, packet_a], "q", 100, 100, config)
    assert [packet.content for packet in selected] == ["A", "B"]


def test_select_weighted_sum_weights_matter():
    """H-e 补强：权重必须生效，公式不是 rel+rec（S1-3 / F3）

    不等权 w=(0.9, 0.1)：
      A: rel=0.5, rec=0.1 → 加权和 0.460 / rel+rec = 0.600
      B: rel=0.2, rec=0.9 → 加权和 0.270 / rel+rec = 1.100
    加权和 A>B，rel+rec 则 B>A —— 排序正好相反。插入序与期望序相反。
    """
    config = _select_config(relevance_weight=0.9, recency_weight=0.1)
    builder = ContextBuilder(config)
    packet_a = ContextPacket(
        content="A",
        timestamp=datetime.now(tz=UTC) - timedelta(days=30),  # exp(-3) 钳到 0.1
        token_count=1,
        relevance_score=0.5,
    )
    packet_b = ContextPacket(
        content="B",
        timestamp=datetime.now(tz=UTC) - timedelta(hours=25),  # exp(-0.104)≈0.901
        token_count=1,
        relevance_score=0.2,
    )
    assert builder._calculate_recency(packet_a.timestamp) == 0.1
    assert builder._calculate_recency(packet_b.timestamp) == pytest.approx(
        0.9, abs=0.01
    )
    selected, _, _ = builder._select([packet_b, packet_a], "q", 100, 100, config)
    assert [packet.content for packet in selected] == ["A", "B"]


def test_select_degrades_when_scorer_returns_long_list():
    """F5：score_many 返回长列表不得静默 zip 丢弃多余分，长度不等整批降级

    静默配对会让 first=0.9、second=0.8（多余的 0.7 被忽略）；整批降级则两者都是 0.0。
    """
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=_LongListScorer([0.9, 0.8, 0.7]))
    first = ContextPacket(content="甲", timestamp=datetime.now(tz=UTC), token_count=1)
    second = ContextPacket(content="乙", timestamp=datetime.now(tz=UTC), token_count=1)
    selected, _, _ = builder._select([first, second], "q", 100, 100, config)
    assert first.relevance_score == 0.0
    assert second.relevance_score == 0.0
    assert [packet.content for packet in selected] == ["甲", "乙"]


def test_select_exact_fit_keeps_all_packets():
    """F6：恰好装满边界 —— 合计 token 等于 available_tokens 必须全部入选

    2+3 == 5：`<=` 成立；若误写 `<`，后包被挤出。
    """
    config = _select_config()
    builder = ContextBuilder(config)
    first = ContextPacket(
        content="p2",
        timestamp=datetime.now(tz=UTC),
        token_count=2,
        relevance_score=0.9,
    )
    second = ContextPacket(
        content="p3",
        timestamp=datetime.now(tz=UTC),
        token_count=3,
        relevance_score=0.8,
    )
    selected, _, dropped_by_budget = builder._select(
        [first, second], "q", 5, 100, config
    )
    assert [packet.content for packet in selected] == ["p2", "p3"]
    assert dropped_by_budget == 0


def test_select_clamps_out_of_range_scores():
    """F7：打分越界必须钳到 [0,1] 再落库，保持 relevance_score 不变量"""
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=_OutOfRangeScorer([1.5, -0.2]))
    high = ContextPacket(content="high", timestamp=datetime.now(tz=UTC), token_count=1)
    low = ContextPacket(content="low", timestamp=datetime.now(tz=UTC), token_count=1)
    builder._select([high, low], "q", 100, 100, config)
    assert high.relevance_score == 1.0  # 严格 ==，勿 approx（会吞 2ulp）
    assert low.relevance_score == 0.0


def test_select_does_not_score_system_instructions():
    """F12：system 包不参与评分，spy 只应看到 other 包"""
    scorer = _SpyScorer(0.9)
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=scorer)
    system = ContextPacket(
        content="sys",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        metadata={"type": "system_instruction"},
    )
    other = ContextPacket(
        content="other", timestamp=datetime.now(tz=UTC), token_count=1
    )
    builder._select([system, other], "q", 100, 100, config)
    assert scorer.scored == ["other"]


def test_select_system_tokens_do_not_consume_available_budget():
    """F13：system 包 token 不参与 available_tokens 竞争 —— 50+60>100 仍双双入选"""
    config = _select_config()
    builder = ContextBuilder(config)
    system = ContextPacket(
        content="sys",
        timestamp=datetime.now(tz=UTC),
        token_count=50,
        relevance_score=1.0,
        metadata={"type": "system_instruction"},
    )
    other = ContextPacket(
        content="other",
        timestamp=datetime.now(tz=UTC),
        token_count=60,
        relevance_score=1.0,
    )
    selected, _, dropped_by_budget = builder._select(
        [system, other], "q", 100, 100, config
    )
    assert [packet.content for packet in selected] == ["sys", "other"]
    assert dropped_by_budget == 0


# --- Task 10: Structure / Compress ---


def _packet(content: str, packet_type: str, tokens: int = 1) -> ContextPacket:
    return ContextPacket(
        content=content,
        timestamp=datetime.now(tz=UTC),
        token_count=tokens,
        relevance_score=1.0,
        metadata={"type": packet_type},
    )


def test_structure_routes_sources_to_sections():
    builder = ContextBuilder()
    selected = [
        _packet("你是助手", "system_instruction"),
        _packet("知识片段", "rag"),
        _packet("记忆命中", "memory"),
    ]
    sections = builder._structure(selected, "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Role & Policies"] == "你是助手"
    assert by_title["Evidence"] == "知识片段"
    assert by_title["Context"] == "记忆命中"
    assert by_title["Task"] == "问题"


def test_structure_keeps_template_order():
    builder = ContextBuilder()
    # 三包乱序传入：既钉模板顺序，也钉「不许按输入顺序吐出」。
    # 必须含 system_instruction 包 —— Role & Policies 段是 `if policies:` 有条件添加，
    # 少了它实际产出只有 4 段（Task/Evidence/Context/Output），期望的 5 段列表会直接红。
    selected = [
        _packet("记忆", "memory"),
        _packet("知识", "rag"),
        _packet("你是助手", "system_instruction"),
    ]
    sections = builder._structure(selected, "问题")
    assert [section.title for section in sections] == [
        "Role & Policies",
        "Task",
        "Evidence",
        "Context",
        "Output",
    ]


def test_structure_honours_evidence_section_override():
    builder = ContextBuilder()
    packet = ContextPacket(
        content="自定义证据",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "custom", "section": "evidence"},
    )
    sections = builder._structure([packet], "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Evidence"] == "自定义证据"
    assert "Context" not in by_title


def test_structure_always_includes_task_and_output():
    builder = ContextBuilder()
    sections = builder._structure([], "唯一的问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Task"] == "唯一的问题"
    assert by_title["Output"] == "请基于以上信息，提供准确、有据的回答。"
    assert "Role & Policies" not in by_title


def test_structure_joins_multi_hit_bodies():
    """I-7：多命中段的拼接分隔符逐字钉死，换分隔符/漏拼接都可辨"""
    builder = ContextBuilder()
    selected = [
        _packet("指令一", "system_instruction"),
        _packet("指令二", "system_instruction"),
        _packet("证据一", "rag"),
        _packet("证据二", "rag"),
        _packet("记忆一", "memory"),
        _packet("记忆二", "memory"),
    ]
    sections = builder._structure(selected, "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Role & Policies"] == "指令一\n指令二"
    assert by_title["Evidence"] == "证据一\n---\n证据二"
    assert by_title["Context"] == "记忆一\n记忆二"


def test_order_restores_template_order():
    """I-6：_order 直接钉模板序；dict 插入序刻意不等于模板序，「忘排序」可辨"""
    builder = ContextBuilder()
    shuffled = {
        "Output": ContextSection("Output", "回答"),
        "Context": ContextSection("Context", "内容"),
        "Task": ContextSection("Task", "任务"),
        "Evidence": ContextSection("Evidence", "证据"),
        "Role & Policies": ContextSection("Role & Policies", "指令"),
    }
    ordered = builder._order(shuffled)
    assert [section.title for section in ordered] == [
        "Role & Policies",
        "Task",
        "Evidence",
        "Context",
        "Output",
    ]


def test_render_uses_bracketed_titles():
    builder = ContextBuilder()
    rendered = builder._render(
        [ContextSection("Task", "做什么"), ContextSection("Output", "回答")]
    )
    assert rendered == "[Task]\n做什么\n\n[Output]\n回答"


def test_compress_skips_when_under_budget():
    builder = ContextBuilder()
    sections = [ContextSection("Task", "简短"), ContextSection("Output", "回答")]
    kept, compressed = builder._compress(sections, 1000, builder.config)
    assert compressed is False
    assert kept == sections


def test_compress_respects_disabled_flag():
    """修复 B11：enable_compression=False 时不得压缩"""
    config = ContextConfig(enable_compression=False)
    builder = ContextBuilder(config)
    sections = [ContextSection("Task", "很长" * 500), ContextSection("Output", "回答")]
    kept, compressed = builder._compress(sections, 10, config)
    assert compressed is False
    assert kept == sections


def test_compress_truncates_oversized_elastic_section():
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Context", "内容" * 2000),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 100, builder.config)
    assert compressed is True
    assert builder._count_tokens(builder._render(kept)) <= 100
    assert [section.title for section in kept] == ["Task", "Context", "Output"]
    assert "内容已压缩" in kept[1].body


def test_compress_drops_elastic_section_when_no_room():
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Context", "内容" * 2000),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 50, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]


def test_compress_never_truncates_task_or_output():
    """I-3：只看标题对「恒不截断」是零信息量 —— 标题在截断前后完全一样

    Task body 是 "任务"*300，截不截一眼可辨；同时钉死压缩标记不得上身。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务" * 300),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 10, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]
    assert kept[0].body == "任务" * 300
    assert kept[1].body == "回答"
    assert "内容已压缩" not in kept[0].body
    assert "内容已压缩" not in kept[1].body


def test_compress_never_truncates_task_under_modest_budget():
    """I-3 配套：max_tokens=200 > 50 时 _truncate_section 会「截断保段」

    预算高于整段丢弃阈值，违规实现把 Task 放进 _truncate_section 后段仍在、
    标题不变，只有 body 断言能击杀（max_tokens=10 那条会被整段丢弃、先被标题拦下）。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务" * 300),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 200, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]
    assert kept[0].body == "任务" * 300
    assert kept[1].body == "回答"
    assert "内容已压缩" not in kept[0].body


def test_compress_break_skips_later_elastic_section():
    """I-4：首个放不下的弹性段处理完必须 break，后续弹性段跳过

    夹具 B（实测校准后选用）：Evidence 大到 remaining=35 <= 50 被整段丢弃，
    Context 小到 render=18 本可装进 max_tokens=50 —— 去掉 break 则 Context 被纳入、
    标题列表多出 Context，本用例即红。
    夹具 A（Evidence 截断保段 + 压缩标记）结构性杀不掉 break：_truncate_section
    截断恒吃满 remaining，去掉 break 后 Context 也装不下，kept 无差异。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Evidence", "证据" * 2000),
        ContextSection("Context", "上下文"),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 50, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]


def test_compress_truncates_role_and_policies_when_constants_overflow():
    builder = ContextBuilder()
    sections = [
        ContextSection("Role & Policies", "指令" * 500),
        ContextSection("Task", "任务"),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 200, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == [
        "Role & Policies",
        "Task",
        "Output",
    ]
    assert "内容已压缩" in kept[0].body
    assert builder._count_tokens(builder._render(kept)) <= 200


def test_truncate_text_is_exact_in_tokens():
    """修复 B10：按 token 精确截断，不再按字符比例猜测

    I-2：原断言 `<= 10` 对「只还 1 个 token」的偷懒实现恒真，改为严格等值
    （本串 encode 截 10 -> decode -> 再 encode = 10，无 BPE 边界回涨，实测安全）。
    I-2b：`max_tokens=0` 杀不掉「删掉 <=0 守卫」—— tokens[:0] 解码恒为空串；
    负数切片 tokens[:-1] 会截出非空串，这才可辨。
    """
    builder = ContextBuilder()
    text = "用户喜欢深蓝色 hello world " * 20
    truncated = builder._truncate_text(text, 10)
    assert builder._count_tokens(truncated) == 10
    assert builder._truncate_text("短文本", 100) == "短文本"
    assert builder._truncate_text("任意", 0) == ""
    assert builder._truncate_text(text, -1) == ""


def test_truncate_section_drops_at_threshold_boundary():
    """I-8：budget <= 50 整段丢弃阈值 + _SEPARATOR_MARGIN 余量，都按字面量钉死

    余量不能靠 `render(kept) <= max_tokens` 间接钉 —— 实测 margin=0 时 render
    恰好等于 max_tokens（100/200 点位都落在 <= 边界上），该断言结构性杀不掉。
    """
    from hello_agents.context.builder import _SEPARATOR_MARGIN

    assert _SEPARATOR_MARGIN == 4
    builder = ContextBuilder()
    section = ContextSection("Evidence", "证据" * 100)
    assert builder._truncate_section(section, 50) is None
    kept = builder._truncate_section(section, 51)
    assert kept is not None
    assert kept.title == "Evidence"
    assert "内容已压缩" in kept.body
    assert kept.body.endswith("\n[... 内容已压缩 ...]")


def test_structure_output_uses_fixed_closing_text():
    """R2：spec §4.6 明文固定收尾指令，完整字面量逐字钉死

    标题-only（`"Output" in by_title`）对「内容为固定文案」零信息量，
    换成任意别的收尾语都照过 —— MT-X-1 换成「回答完毕。」后全套件仍绿。
    """
    builder = ContextBuilder()
    sections = builder._structure([], "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Output"] == "请基于以上信息，提供准确、有据的回答。"


def test_compress_keeps_small_elastic_sections_whole():
    """R1：RP「优先全额保留」+ 弹性段「贪心全额纳入」主路径必须被点亮

    原套件 8 个 _compress 用例经 settrace 逐行追踪，L511（RP 全额保留）
    与 L526-527（弹性段全额纳入后 continue）执行次数均为 0 —— 规格 §5.5.1
    「优先全额保留」与 §5.5.2「贪心全额纳入」的主路径从未执行。删掉 L525-527
    改成「弹性段一律截断/丢弃」，或删掉 L508-517 改成「RP 一律进
    _truncate_section」，全部用例仍绿（均已实测）。

    实测校准 max_tokens=400（mandatory(Task+Output)=310，RP 预算 86）：
    RP「指令」(3 tokens) 与 Evidence「证据」(2 tokens) 均走全额纳入且无标记，
    Context「上下文」*2000 (6000 tokens) 放不下，截断保段并带完整压缩标记。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Role & Policies", "指令"),
        ContextSection("Task", "任务" * 300),
        ContextSection("Evidence", "证据"),
        ContextSection("Context", "上下文" * 2000),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 400, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == [
        "Role & Policies",
        "Task",
        "Evidence",
        "Context",
        "Output",
    ]
    # RP 与 Evidence 走全额纳入：body 原样、不带压缩标记
    assert kept[0].body == "指令"
    assert kept[2].body == "证据"
    # Context 首个放不下，截断保段并带完整标记（顺带钉 R3 字面量）
    assert kept[3].body.endswith("\n[... 内容已压缩 ...]")
    assert builder._count_tokens(builder._render(kept)) <= 400


def test_truncate_section_appends_exact_marker():
    """R3：压缩标记完整字面量（含 \\n 前缀与 [...] 装饰）逐字钉死

    子串断言 `"内容已压缩" in body` 只钉 5 字核心：去掉 \\n 前缀（MT-X-5）
    或去掉方括号装饰（MT-R3b）都照过。spec §5.5 明文写的是追加
    `[... 内容已压缩 ...]`，必须按完整字面量钉，不能只钉子串。
    """
    builder = ContextBuilder()
    section = ContextSection("Evidence", "证据" * 100)
    kept = builder._truncate_section(section, 200)
    assert kept is not None
    assert kept.body.endswith("\n[... 内容已压缩 ...]")


def test_compress_skips_at_exact_fit():
    """R4：恰好装满是边界不动点 —— count(render) == max_tokens 时不得压缩

    MT-X-9 把超限判断 `<=` 改成 `<` 后，等值点会被误判为超限而进入压缩。
    对照：差 1 token 即触发压缩，钉住 `<=` 的左邻域（阶段 2 的
    test_select_exact_fit_keeps_all_packets 在压缩侧没有对应点，此处补齐）。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Context", "内容" * 100),
        ContextSection("Output", "回答"),
    ]
    exact = builder._count_tokens(builder._render(sections))
    kept, compressed = builder._compress(sections, exact, builder.config)
    assert compressed is False
    assert kept == sections
    _, compressed2 = builder._compress(sections, exact - 1, builder.config)
    assert compressed2 is True


def test_truncate_text_identity_at_unit_length():
    """R5 单位元：max_tokens == token 数时原样返回，不得走 decode 截断路径

    方法论 3（不动点盲区）：普通文本上 decode(tokens[:n]) 与早退在**内容上
    等价**（BPE 全量解码必还原原文），单位元是不动点，`==` 杀不掉
    `len <= max` → `len < max`（MT-X-11）。改用含孤立代理项 U+D800 的文本：
    encode 走 surrogatepass 得到 token，decode 默认 errors='replace' 把代理项
    换成 U+FFFD，两条路径才可辨。

    方法论 1（re-encode 实测）：目标 n 先实测 `len(encode(text)) == n`
    （本串 n=31），不从抽样点外推。
    """
    builder = ContextBuilder()
    text = "hello \ud800 world " * 10
    n = builder._count_tokens(text)
    assert n == len(builder.encoder.encode(text))
    assert builder._truncate_text(text, n) == text


def test_truncate_text_identity_one_past_unit_length():
    """R5 对照：max_tokens = n+1 仍在早退区，单位元两侧行为一致

    与单位元断言配套，钉住「不截断」区间是 n 与 n+1 两点，而非只有一点。
    """
    builder = ContextBuilder()
    text = "hello \ud800 world " * 10
    n = builder._count_tokens(text)
    assert builder._truncate_text(text, n + 1) == text


def test_compress_drops_role_and_policies_when_mandates_overflow():
    """R6：Task+Output 合计已超预算时 RP 截断预算 ≤ 50，整段丢弃

    builder.py L516 的 False 分支（truncated is None → RP 不入 kept）在原套件
    中求值 0 次。触发条件：Task+Output 的 mandatory_tokens 已逼近/超过
    max_tokens，使 RP 预算 ≤ 50。规格 §5.5.1 只说截断 RP、未说丢弃；
    实现与 §4.6「空段省略」相容，本用例把它钉成契约。
    """
    builder = ContextBuilder()
    sections = [
        ContextSection("Role & Policies", "指令" * 50),
        ContextSection("Task", "任务" * 300),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 10, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]
    assert kept[0].body == "任务" * 300
    assert kept[1].body == "回答"
