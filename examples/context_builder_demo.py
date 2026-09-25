"""ContextBuilder 演示：离线可跑，无需任何 API Key

覆盖五节：动态预算、两种打分器、缓存命中、构建统计、A/B 稳定分流。
末尾 ``self_check()`` 自动判定期望 —— 示例脚本不是 pytest 用例，
没有 harness 帮你判，不加自检就等于没验。

运行：
    uv run python examples/context_builder_demo.py
"""

from datetime import UTC, datetime

from hello_agents.context import (
    ContextBuilder,
    ContextConfig,
    ContextPacket,
    EmbeddingSimilarityScorer,
    ExperimentAssigner,
    ExperimentSpec,
    KeywordOverlapScorer,
)
from hello_agents.core import Message, MessageRole
from hello_agents.memory import TFIDFEmbedding


def banner(title: str) -> None:
    """打印小节分隔横幅"""
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def _knowledge_packets() -> list[ContextPacket]:
    """两条离线知识/记忆包，供打分与统计演示复用"""
    return [
        ContextPacket(
            content="Qdrant 是向量数据库，支持本地内存模式与远程服务模式。",
            timestamp=datetime.now(tz=UTC),
            metadata={"type": "rag"},
        ),
        ContextPacket(
            content="用户偏好深蓝色主题。",
            timestamp=datetime.now(tz=UTC),
            metadata={"type": "memory"},
        ),
    ]


def demo_dynamic_budget() -> tuple[int, int]:
    """第 1 节：动态 token 预算，返回 (简单 scaled, 复杂 scaled)"""
    banner("1. 动态 token 预算")
    builder = ContextBuilder(ContextConfig(max_tokens=4000))
    simple = builder.build_result("你好")
    # 复杂查询：长查询 + 10 轮历史，四项复杂度因子几乎拉满
    complex_result = builder.build_result(
        "如何根据文档配置 Qdrant 向量库？" + "需要详细步骤。" * 20,
        conversation_history=[
            Message(role=MessageRole.USER, content=f"第{i}轮对话") for i in range(10)
        ],
    )
    for label, result in (("简单查询", simple), ("复杂查询", complex_result)):
        budget = result.stats.budget
        print(
            f"{label}: complexity={budget.complexity:.2f} "
            f"scaled={budget.scaled_max_tokens} "
            f"reserved={budget.reserved_tokens} "
            f"available={budget.available_tokens}"
        )
    return (
        simple.stats.budget.scaled_max_tokens,
        complex_result.stats.budget.scaled_max_tokens,
    )


def demo_scorers() -> None:
    """第 2 节：关键词重叠 vs 向量余弦两种相关性打分器"""
    banner("2. 两种相关性打分器")
    query = "如何配置 Qdrant 向量库"
    contents = [packet.content for packet in _knowledge_packets()]
    scorers = (
        KeywordOverlapScorer(),
        EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64)),
    )
    for scorer in scorers:
        scores = scorer.score_many(contents, query)
        print(f"{scorer.name}: {[round(score, 3) for score in scores]}")


def demo_cache() -> int:
    """第 3 节：系统指令包缓存，返回二次构建的 cache_hits"""
    banner("3. 缓存命中")
    builder = ContextBuilder()
    instructions = "你是严谨的技术助手。"
    first = builder.build_result("问题", system_instructions=instructions)
    second = builder.build_result("问题", system_instructions=instructions)
    print(f"首次构建 cache_hits={first.stats.cache_hits}")
    print(f"二次构建 cache_hits={second.stats.cache_hits}")
    return second.stats.cache_hits


def demo_stats() -> None:
    """第 4 节：构建统计与生成的上下文"""
    banner("4. 构建统计")
    builder = ContextBuilder()
    result = builder.build_result(
        "如何配置向量库？",
        conversation_history=[Message(role=MessageRole.USER, content="你好")],
        system_instructions="你是技术助手。",
        additional_packets=_knowledge_packets(),
    )
    print(result.stats.summary())
    print(f"按来源候选数: {result.stats.candidates_by_source}")
    print(f"按来源入选数: {result.stats.selected_by_source}")
    print("\n--- 生成的上下文 ---")
    print(result.context)


def demo_experiment() -> tuple[str, str, set[str]]:
    """第 5 节：A/B 稳定分流，返回 (两次变体, 覆盖集合)"""
    banner("5. A/B 稳定分流")
    spec = ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
    )
    assigner = ExperimentAssigner()
    # 稳定性：同一 session 两次独立调用存变量再比（自指式断言是假钉扎）
    assign_a = assigner.assign(spec, "session-42")
    assign_b = assigner.assign(spec, "session-42")
    print(f"session-42 第1次 -> {assign_a}")
    print(f"session-42 第2次 -> {assign_b}")
    print(f"session-7 -> {assigner.assign(spec, 'session-7')}")
    # 非退化：32 个 session 必须覆盖两种变体（与 Task 11 J-a 口径一致）
    seen: set[str] = set()
    for index in range(32):
        seen.add(assigner.assign(spec, f"demo-session-{index}"))
    print(f"32 个 session 覆盖的变体: {sorted(seen)}")
    builder = ContextBuilder(ContextConfig(experiment=spec))
    result = builder.build_result("问题", session_id="session-42")
    print(f"构建时记录的实验: {result.stats.experiment}/{result.stats.variant}")
    return assign_a, assign_b, seen


def self_check(
    simple_scaled: int,
    complex_scaled: int,
    second_hits: int,
    assign_a: str,
    assign_b: str,
    seen: set[str],
) -> None:
    """自检：任何一条不满足即以 AssertionError 非零退出

    示例脚本不是 pytest 用例，没有 harness 帮你判 —— 不加自检就等于没验。
    覆盖 N-b 三条 + N-c 两条（稳定性与 N-b 第 3 条同一断言）。
    """
    # N-b 1：复杂查询的 scaled 必须大于简单查询
    assert complex_scaled > simple_scaled, "复杂查询 scaled 应大于简单查询"
    # N-b 2：二次构建必须命中缓存
    assert second_hits > 0, "二次构建应命中缓存"
    # N-b 3 / N-c 稳定性：同一 session 两次分流必须稳定
    assert assign_a == assign_b, "同一 session 两次分流必须稳定"
    # N-c 非退化 + 集合双向：32 个 session 必须恰好覆盖两种变体
    assert seen == {"control", "variant_a"}, "32 个 session 必须覆盖两种变体"
    print("\n[自检通过] 全部断言满足")


def main() -> None:
    """依次跑五节演示，最后自检"""
    simple_scaled, complex_scaled = demo_dynamic_budget()
    demo_scorers()
    second_hits = demo_cache()
    demo_stats()
    assign_a, assign_b, seen = demo_experiment()
    self_check(simple_scaled, complex_scaled, second_hits, assign_a, assign_b, seen)


if __name__ == "__main__":
    main()
