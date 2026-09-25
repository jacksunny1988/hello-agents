"""hello_agents.context 公开导出面的契约测试。

钉住三件事：
1. 每个公开名既出现在 ``__all__``，也能在模块命名空间上取到（两个独立事实）；
2. ``__all__`` 与 16 个期望名双向等价——既无遗漏，也无多余内部符号被误导出；
3. ``__all__`` 保持 ASCII 升序，且包 docstring 不夸大默认行为。

L-a 要点：Task 3 评审当初就是从 ``__all__`` 成员资格缺口提出来的——只查
「期望名都在 __all__」、不查「__all__ 没有多余名」，内部符号一旦被误导出
仍然全绿。本文件是同一缺口的收口：集合等价必须双向。
L-b 要点：for-循环里首个失败即中断，后续名字的缺失不会被报告，故按名字
parametrize、每个名字独立用例；集合等价断言另立一条、只留一份。
"""

import pytest

# 16 个公开名，ASCII 升序（大写在小写前，TTLCache 在 create_relevance_scorer 前）
EXPECTED_PUBLIC_API = (
    "BudgetInfo",
    "BudgetPolicy",
    "BuildResult",
    "BuildStats",
    "ContextBuilder",
    "ContextConfig",
    "ContextPacket",
    "ContextSection",
    "EmbeddingSimilarityScorer",
    "ExperimentAssigner",
    "ExperimentSpec",
    "HeuristicBudgetPolicy",
    "KeywordOverlapScorer",
    "RelevanceScorer",
    "TTLCache",
    "create_relevance_scorer",
)


@pytest.mark.parametrize("name", EXPECTED_PUBLIC_API)
def test_public_api_is_importable_from_package_root(name):
    # 每个名字两个独立事实：在 __all__ 里 + 在模块命名空间上取得到。
    # 只查前者时「import 被删、名仍留在 __all__」漏网；只查后者时
    # 「__all__ 里拼错名字」漏网。
    from hello_agents import context

    assert name in context.__all__, f"{name} 未导出"
    assert hasattr(context, name), f"{name} 不存在"


def test_public_api_all_matches_expected_set_exactly():
    # L-a：双向等价。只查「期望名都在 __all__」时，多出来的内部符号钉不住。
    from hello_agents import context

    assert set(context.__all__) == set(EXPECTED_PUBLIC_API)


def test_public_api_all_is_sorted():
    # L-c：__all__ 保持 ASCII 升序
    from hello_agents import context

    assert context.__all__ == sorted(context.__all__)


def test_docstring_typical_usage_imports_and_constructs():
    # L-d：docstring「典型用法」的导入与构造可跑通
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    assert isinstance(builder, ContextBuilder)
    assert builder.config.max_tokens == 4096


def test_init_docstring_does_not_overstate_auto_upgrade():
    # L-e 红线：默认打分器恒为 KeywordOverlapScorer，不会自动升级为向量相关性
    # 打分。那句是 create_relevance_scorer("auto") 的行为，不是本包默认行为。
    from hello_agents import context

    doc = context.__doc__ or ""
    assert "自动升级为向量相关性打分" not in doc
    # 如实措辞必须在场：默认关键词重叠 + 显式传入向量打分器 + 显式 auto
    assert "关键词重叠" in doc
    assert "EmbeddingSimilarityScorer" in doc
    assert "create_relevance_scorer" in doc
