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

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

import tiktoken

from ..core import Message
from ..core.exceptions import ConfigError
from ..tools.base import BaseTool
from ..tools.response import ToolStatus
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

    def _system_packet(self, instructions: str) -> ContextPacket:
        """构造系统指令包，命中缓存时直接复用"""
        key = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        packet = ContextPacket(
            content=instructions,
            timestamp=datetime.now(tz=UTC),
            token_count=self._count_tokens(instructions),
            relevance_score=1.0,
            metadata={"type": "system_instruction", "priority": "high"},
        )
        self.cache.put(key, packet)
        return packet

    def _hits_to_packets(
        self,
        hits: list[dict[str, Any]],
        source: str,
        config: ContextConfig,
    ) -> list[ContextPacket]:
        """把工具返回的命中字典转换为候选包，并按阈值过滤"""
        packets: list[ContextPacket] = []
        for hit in hits:
            content = hit.get("content", "")
            raw_score = hit.get("score")
            score = 0.0 if raw_score is None else float(raw_score)
            if score < config.min_source_score:
                continue
            metadata = dict(hit.get("metadata") or {})
            importance = metadata.get("importance")
            if importance is not None and float(importance) < config.min_importance:
                continue
            packets.append(
                ContextPacket(
                    content=content,
                    timestamp=_parse_timestamp(hit.get("created_at")),
                    token_count=self._count_tokens(content),
                    relevance_score=score if raw_score is not None else None,
                    metadata={**metadata, "type": source, "source_score": score},
                )
            )
        return packets

    def _memory_packets(
        self, user_query: str, config: ContextConfig
    ) -> list[ContextPacket]:
        if self.memory_tool is None:
            return []
        try:
            response = self.memory_tool.run(
                {"action": "recall", "query": user_query, "limit": config.memory_limit}
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败一律降级
            logger.warning("记忆检索失败: %s", exc)
            return []
        if getattr(response, "status", None) == ToolStatus.ERROR:
            logger.warning("记忆检索返回错误: %s", getattr(response, "text", ""))
            return []
        hits = (getattr(response, "data", None) or {}).get("hits") or []
        return self._hits_to_packets(hits, "memory", config)

    def _rag_packets(
        self, user_query: str, config: ContextConfig
    ) -> list[ContextPacket]:
        if self.rag_tool is None:
            return []
        try:
            response = self.rag_tool.run(
                {"action": "query", "question": user_query, "top_k": config.rag_limit}
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败一律降级
            logger.warning("知识检索失败: %s", exc)
            return []
        if getattr(response, "status", None) == ToolStatus.ERROR:
            logger.warning("知识检索返回错误: %s", getattr(response, "text", ""))
            return []
        chunks = (getattr(response, "data", None) or {}).get("chunks") or []
        return self._hits_to_packets(chunks, "rag", config)

    def _history_packets(
        self, history: list[Message], config: ContextConfig
    ) -> list[ContextPacket]:
        """Message 无 timestamp 字段，故用 metadata["position"] 承载新近性"""
        window = config.history_window
        if window <= 0 or not history:
            return []
        now = datetime.now(tz=UTC)
        packets: list[ContextPacket] = []
        for position, message in enumerate(history[-window:]):
            content = f"{message.role}: {message.content}"
            packets.append(
                ContextPacket(
                    content=content,
                    timestamp=now,
                    token_count=self._count_tokens(content),
                    metadata={
                        "type": "history",
                        "role": message.role,
                        "position": position,
                    },
                )
            )
        return packets

    def _gather(
        self,
        user_query: str,
        conversation_history: list[Message],
        system_instructions: str | None,
        additional_packets: list[ContextPacket],
        config: ContextConfig,
    ) -> list[ContextPacket]:
        """汇集所有候选信息"""
        packets: list[ContextPacket] = []
        if system_instructions:
            packets.append(self._system_packet(system_instructions))
        packets.extend(self._memory_packets(user_query, config))
        packets.extend(self._rag_packets(user_query, config))
        packets.extend(self._history_packets(conversation_history, config))
        for packet in additional_packets:
            if packet.token_count == 0:
                packet.token_count = self._count_tokens(packet.content)
            packets.append(packet)
        return packets
