"""上下文构建器

编排「实验分流 -> 汇集 -> 选择 -> 组织 -> 压缩」五步，在 token 预算内
产出上下文字符串与构建统计。

典型用法：
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    context = builder.build("用户想了解什么？", conversation_history=history)
    result = builder.build_result("用户想了解什么？", conversation_history=history)
    print(result.stats.summary())
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import tiktoken

from ..core import Message
from ..core.exceptions import ConfigError
from ..tools.base import BaseTool
from .base import _SOURCE_TYPES, ContextConfig, ContextPacket
from .budget import BudgetInfo, BudgetPolicy, HeuristicBudgetPolicy
from .cache import TTLCache
from .experiment import ExperimentAssigner
from .scoring import KeywordOverlapScorer, RelevanceScorer

logger = logging.getLogger(__name__)

__all__ = ["ContextBuilder"]


def _parse_timestamp(raw: Any) -> datetime:
    """把 ISO 字符串还原为 datetime，缺失或非法时退回当前时刻

    出口一律 tz-aware：naive 一律按 UTC 归一，已是 aware 的原样返回。
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("无法解析时间戳 %r，退回当前时刻", raw)
        else:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _count_by_source(packets: list[ContextPacket]) -> dict[str, int]:
    """按 metadata["type"] 统计包数量，五种来源恒出现在结果中

    未知 type 一律归入 custom，不泄漏第六个键。
    """
    counts = {name: 0 for name in _SOURCE_TYPES}
    for packet in packets:
        source = packet.metadata.get("type", "custom")
        if source not in counts:
            source = "custom"
        counts[source] += 1
    return counts


class ContextBuilder:
    """上下文构建器

    按「实验分流 -> 汇集 -> 选择 -> 组织 -> 压缩」五步构建上下文。
    """

    def __init__(
        self,
        config: ContextConfig | None = None,
        *,
        memory_tool: BaseTool | None = None,
        rag_tool: BaseTool | None = None,
        relevance_scorer: RelevanceScorer | None = None,
        budget_policy: BudgetPolicy | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.config = config if config is not None else ContextConfig()
        self.memory_tool = memory_tool
        self.rag_tool = rag_tool
        if budget_policy is not None:
            self.budget_policy: BudgetPolicy = budget_policy
        elif self.config.budget_policy is not None:
            self.budget_policy = self.config.budget_policy
        else:
            self.budget_policy = HeuristicBudgetPolicy()
        self.relevance_scorer: RelevanceScorer = (
            relevance_scorer if relevance_scorer is not None else KeywordOverlapScorer()
        )
        self.cache: TTLCache = (
            cache
            if cache is not None
            else TTLCache(
                max_size=self.config.cache_max_size,
                ttl_seconds=self.config.cache_ttl_seconds,
            )
        )
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # 编码器缺失一律转为配置错误
            raise ConfigError(f"tiktoken 编码器不可用: {exc}") from exc
        self._assigner = ExperimentAssigner()

    def _count_tokens(self, text: str) -> int:
        """精确 token 计数，全模块唯一口径"""
        return len(self.encoder.encode(text))

    def _compute_budget(
        self,
        config: ContextConfig,
        user_query: str,
        history: list[Message],
        system_instructions: str | None,
    ) -> BudgetInfo:
        """按复杂度缩放预算并拆分为预留 / 可用两部分"""
        complexity = self.budget_policy.estimate(
            user_query, history=history, system_instructions=system_instructions
        )
        complexity = max(0.0, min(1.0, complexity))
        span = config.max_budget_ratio - config.min_budget_ratio
        scaled = int(config.max_tokens * (config.min_budget_ratio + span * complexity))
        scaled = max(1, scaled)
        reserved = int(scaled * config.reserve_ratio)
        return BudgetInfo(
            policy=getattr(
                self.budget_policy, "name", type(self.budget_policy).__name__
            ),
            complexity=complexity,
            requested_max_tokens=config.max_tokens,
            scaled_max_tokens=scaled,
            reserved_tokens=reserved,
            available_tokens=scaled - reserved,
        )

    def _cache_counters(self) -> tuple[int, int]:
        """汇总包缓存与打分器缓存命中数"""
        stats = self.cache.stats()
        hits, misses = stats["hits"], stats["misses"]
        scorer_cache = getattr(self.relevance_scorer, "cache", None)
        if isinstance(scorer_cache, TTLCache):
            scorer_stats = scorer_cache.stats()
            hits += scorer_stats["hits"]
            misses += scorer_stats["misses"]
        return hits, misses
