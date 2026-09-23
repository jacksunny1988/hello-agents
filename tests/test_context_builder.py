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
