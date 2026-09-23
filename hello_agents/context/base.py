"""上下文构建

在 token 预算内汇集系统指令、记忆命中、知识命中、对话历史与自定义信息，
按相关性与新近性打分排序后组织成结构化模板，并按需压缩。

典型用法：
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    context = builder.build("用户想了解什么？", conversation_history=history)
    result = builder.build_result("用户想了解什么？", conversation_history=history)
    print(result.stats.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.exceptions import ConfigError
from .budget import BudgetInfo, BudgetPolicy

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from .experiment import ExperimentSpec

logger = logging.getLogger(__name__)

__all__ = [
    "BuildResult",
    "BuildStats",
    "ContextConfig",
    "ContextPacket",
    "ContextSection",
]

_SOURCE_TYPES = ("system_instruction", "memory", "rag", "history", "custom")
_TEMPLATE_ORDER = ("Role & Policies", "Task", "Evidence", "Context", "Output")


@dataclass
class ContextPacket:
    """候选信息包

    Attributes:
        content: 信息内容
        timestamp: 时间戳
        token_count: Token 数量，0 表示由 ContextBuilder 计算
        relevance_score: 相关性分数(0.0-1.0)，None 表示待计算
        metadata: 元数据，type 取值为 system_instruction/memory/rag/history/custom
    """

    content: str
    timestamp: datetime
    token_count: int = 0
    relevance_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.relevance_score is not None:
            self.relevance_score = max(0.0, min(1.0, self.relevance_score))


@dataclass
class ContextSection:
    """上下文模板中的一个段落

    Attributes:
        title: 段落标题，如 "Role & Policies"
        body: 段落正文
    """

    title: str
    body: str


@dataclass
class ContextConfig:
    """上下文构建配置

    Attributes:
        max_tokens: 最大 token 数量(请求值，实际值按复杂度缩放)
        reserve_ratio: 为系统指令预留的比例(0.0-1.0)
        min_relevance: 最低相关性阈值
        enable_compression: 是否启用压缩
        recency_weight: 新近性权重(0.0-1.0)
        relevance_weight: 相关性权重(0.0-1.0)
        min_budget_ratio: 复杂度为 0 时的预算比例
        max_budget_ratio: 复杂度为 1 时的预算比例
        budget_policy: 复杂度估计策略，None 时使用 HeuristicBudgetPolicy
        memory_limit: 记忆检索条数上限
        rag_limit: 知识检索条数上限
        min_importance: 记忆命中 importance 元数据的最低值
        min_source_score: 检索命中 score 的最低值
        history_window: 纳入的最近对话条数
        cache_max_size: 缓存最大条目数
        cache_ttl_seconds: 缓存条目存活秒数
        log_stats: 是否输出构建统计日志
        experiment: A/B 实验声明，None 表示不做实验
    """

    max_tokens: int = 3000
    reserve_ratio: float = 0.2
    min_relevance: float = 0.1
    enable_compression: bool = True
    recency_weight: float = 0.3
    relevance_weight: float = 0.7
    min_budget_ratio: float = 0.5
    max_budget_ratio: float = 1.0
    budget_policy: BudgetPolicy | None = None
    memory_limit: int = 10
    rag_limit: int = 5
    min_importance: float = 0.0
    min_source_score: float = 0.0
    history_window: int = 5
    cache_max_size: int = 256
    cache_ttl_seconds: float = 3600.0
    log_stats: bool = True
    experiment: ExperimentSpec | None = None

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ConfigError("max_tokens 必须为正整数")
        if not 0.0 <= self.reserve_ratio <= 1.0:
            raise ConfigError("reserve_ratio 必须在 [0, 1] 范围内")
        if not 0.0 <= self.min_relevance <= 1.0:
            raise ConfigError("min_relevance 必须在 [0, 1] 范围内")
        if not 0.0 <= self.relevance_weight <= 1.0:
            raise ConfigError("relevance_weight 必须在 [0, 1] 范围内")
        if not 0.0 <= self.recency_weight <= 1.0:
            raise ConfigError("recency_weight 必须在 [0, 1] 范围内")
        if abs(self.recency_weight + self.relevance_weight - 1.0) >= 1e-6:
            raise ConfigError("recency_weight + relevance_weight 必须等于 1.0")
        if not 0.0 <= self.min_budget_ratio <= self.max_budget_ratio <= 1.0:
            raise ConfigError(
                "需满足 0.0 <= min_budget_ratio <= max_budget_ratio <= 1.0"
            )
        for name in ("memory_limit", "rag_limit", "history_window", "cache_max_size"):
            if getattr(self, name) < 0:
                raise ConfigError(f"{name} 不能为负数")
        if self.cache_ttl_seconds <= 0:
            raise ConfigError("cache_ttl_seconds 必须为正数")


@dataclass
class BuildStats:
    """一次上下文构建的统计

    Attributes:
        candidates_total: 候选包总数
        candidates_by_source: 各来源的候选数
        selected_total: 入选包总数
        selected_by_source: 各来源的入选数
        dropped_by_relevance: 因低于相关性阈值被丢弃的数量
        dropped_by_budget: 因预算不足被丢弃的数量
        structured_tokens: 压缩前的 token 数
        final_tokens: 压缩后的 token 数
        selected_tokens: 入选打分包的 token 合计
        budget: 预算明细
        token_utilization: final_tokens / scaled_max_tokens
        compressed: 是否执行了压缩
        compression_ratio: final_tokens / structured_tokens，1.0 表示未压缩
        cache_hits: 缓存命中数(系统指令包与 embedding 合并计数)
        cache_misses: 缓存未命中数
        duration_ms: 构建耗时(毫秒)
        experiment: 实验名
        variant: 变体名
    """

    candidates_total: int
    candidates_by_source: dict[str, int]
    selected_total: int
    selected_by_source: dict[str, int]
    dropped_by_relevance: int
    dropped_by_budget: int
    structured_tokens: int
    final_tokens: int
    selected_tokens: int
    budget: BudgetInfo
    token_utilization: float
    compressed: bool
    compression_ratio: float
    cache_hits: int
    cache_misses: int
    duration_ms: float
    experiment: str | None
    variant: str | None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典"""
        return {
            "candidates_total": self.candidates_total,
            "candidates_by_source": dict(self.candidates_by_source),
            "selected_total": self.selected_total,
            "selected_by_source": dict(self.selected_by_source),
            "dropped_by_relevance": self.dropped_by_relevance,
            "dropped_by_budget": self.dropped_by_budget,
            "structured_tokens": self.structured_tokens,
            "final_tokens": self.final_tokens,
            "selected_tokens": self.selected_tokens,
            "budget": {
                "policy": self.budget.policy,
                "complexity": round(self.budget.complexity, 4),
                "requested_max_tokens": self.budget.requested_max_tokens,
                "scaled_max_tokens": self.budget.scaled_max_tokens,
                "reserved_tokens": self.budget.reserved_tokens,
                "available_tokens": self.budget.available_tokens,
            },
            "token_utilization": round(self.token_utilization, 4),
            "compressed": self.compressed,
            "compression_ratio": round(self.compression_ratio, 4),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "duration_ms": round(self.duration_ms, 2),
            "experiment": self.experiment,
            "variant": self.variant,
        }

    def summary(self) -> str:
        """单行摘要，供日志使用"""
        text = (
            f"candidates={self.candidates_total} selected={self.selected_total} "
            f"tokens={self.final_tokens}/{self.budget.scaled_max_tokens} "
            f"utilization={self.token_utilization:.2f} "
            f"complexity={self.budget.complexity:.2f} "
            f"compressed={self.compressed} "
            f"cache={self.cache_hits}/{self.cache_hits + self.cache_misses} "
            f"duration={self.duration_ms:.1f}ms"
        )
        if self.experiment:
            text += f" experiment={self.experiment}/{self.variant}"
        return text


@dataclass
class BuildResult:
    """上下文构建结果

    Attributes:
        context: 组织好的上下文字符串
        stats: 构建统计
    """

    context: str
    stats: BuildStats
