"""预算策略测试：复杂度启发式与缩放公式"""

from hello_agents.context.budget import BudgetInfo, HeuristicBudgetPolicy
from hello_agents.core import Message, MessageRole


def _policy() -> HeuristicBudgetPolicy:
    return HeuristicBudgetPolicy()


def test_short_plain_query_has_low_complexity():
    complexity = _policy().estimate("你好", history=[], system_instructions=None)
    assert 0.0 <= complexity < 0.2


def test_long_query_raises_complexity():
    short = _policy().estimate("你好", history=[], system_instructions=None)
    long = _policy().estimate("请" * 200, history=[], system_instructions=None)
    assert long > short
    assert long >= 0.4


def test_interrogative_marker_raises_complexity():
    plain = _policy().estimate("配置向量库", history=[], system_instructions=None)
    asking = _policy().estimate("如何配置向量库", history=[], system_instructions=None)
    assert asking > plain


def test_english_question_word_matches_as_whole_word():
    """`how` 不应在 `show` 中误命中"""
    hit = _policy().estimate("how to configure", history=[], system_instructions=None)
    miss = _policy().estimate("show the config", history=[], system_instructions=None)
    assert hit > miss


def test_history_size_raises_complexity():
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(10)]
    empty = _policy().estimate("配置向量库", history=[], system_instructions=None)
    full = _policy().estimate("配置向量库", history=history, system_instructions=None)
    assert full > empty


def test_retrieval_cue_raises_complexity():
    plain = _policy().estimate("配置向量库", history=[], system_instructions=None)
    cued = _policy().estimate(
        "根据文档配置向量库", history=[], system_instructions=None
    )
    assert cued > plain


def test_complexity_is_clamped_to_unit_interval():
    huge = _policy().estimate("如何" + "请" * 500, history=[], system_instructions=None)
    assert 0.0 <= huge <= 1.0


def test_budget_info_fields_are_consistent():
    info = BudgetInfo(
        policy="heuristic",
        complexity=0.5,
        requested_max_tokens=3000,
        scaled_max_tokens=2250,
        reserved_tokens=450,
        available_tokens=1800,
    )
    assert info.available_tokens == info.scaled_max_tokens - info.reserved_tokens
