"""上下文构建器

编排「实验分流 -> 汇集 -> 选择 -> 组织 -> 压缩」五步，在 token 预算内
产出上下文字符串与构建统计。

典型用法：
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    context = builder.build("用户想了解什么？", conversation_history=history)
    result = builder.build_result("用户想了解什么？", conversation_history=history)
    print(result.stats.summary())

五步职责（详见 :meth:`ContextBuilder.build_result`）：
    0. 实验分流   —— 按 session_id 稳定分桶，把变体覆盖应用到 config
    1. 汇集       —— 系统指令 / 记忆 / 知识 / 历史 / 自定义 → 候选包列表
    2. 选择       —— 打分 + 加权排序 + 预算内贪心填充，产出入选包
    3. 组织       —— 按模板把入选包路由到五个固定段落（只组织不渲染）
    4. 压缩       —— 超预算时按段优先级取舍或截断，产出最终上下文

对外承诺（spec §6）：除 :class:`~hello_agents.core.exceptions.ConfigError`
外，构建过程中的任何异常都不得外泄给调用方 —— 工具失败、打分失败、复杂度
估计失败一律降级并记日志。
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import tiktoken

from ..core import Message
from ..core.exceptions import ConfigError
from ..tools.base import BaseTool
from ..tools.response import ToolStatus
from .base import (
    _SOURCE_TYPES,
    _TEMPLATE_ORDER,
    BuildResult,
    BuildStats,
    ContextConfig,
    ContextPacket,
    ContextSection,
)
from .budget import BudgetInfo, BudgetPolicy, HeuristicBudgetPolicy
from .cache import TTLCache
from .experiment import ExperimentAssigner, ExperimentSpec
from .scoring import KeywordOverlapScorer, RelevanceScorer

logger = logging.getLogger(__name__)

__all__ = ["ContextBuilder"]

# 渲染时段间插入 "\n\n"，压缩预算需预留的分隔符余量（token）
#
# 为什么是 4：段与段之间由 _render 拼两个换行，新增一段会多吃分隔符 token。
# 若不预留，截断预算算得刚好满就会溢出；预留少量余量后，_compress 的
# 「能否整段纳入」判断不会因为分隔符而误判。
_SEPARATOR_MARGIN = 4

# 复杂度估计失败时的降级值：给足预算，超限交由压缩阶段兜底
#
# 取 1.0 而非 0.0 的理由：估计器崩溃属于异常工况，此时宁可给满预算、
# 让压缩阶段去裁，也不要因为低估复杂度而过早丢内容。降级会记 WARNING。
_DEFAULT_COMPLEXITY = 1.0


def _parse_timestamp(raw: Any) -> datetime:
    """把 ISO 字符串还原为 datetime，缺失或非法时退回当前时刻

    出口一律 tz-aware：naive 一律按 UTC 归一，已是 aware 的原样返回。

    **归一化只发生在解析这一层**，下游（:meth:`_calculate_recency`）不得
    再做 ``replace(tzinfo=UTC)``。原因：对 offset-aware 的 ISO 串再 replace
    会把「偏移壁钟」改写成「UTC 壁钟」，同一个瞬时被算成另一个时刻
    （历史上 P6/P8 就是这类 bug）。因此这里要么保留原 tzinfo，要么只在
    naive 上贴 UTC，绝不改写已有的偏移。

    输入形态：
        datetime  —— naive 则贴 UTC；aware 原样返回
        str       —— ``fromisoformat`` 解析，失败记 DEBUG 后退回当前时刻
        其它/缺失 —— 退回 ``datetime.now(tz=UTC)``
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

    契约：结果的键集合恒等于 :data:`_SOURCE_TYPES`（system_instruction /
    memory / rag / history / custom），不多不少 —— ``BuildStats`` 的
    ``candidates_by_source`` / ``selected_by_source`` 靠这一点做稳定对账。

    未知 type 一律归入 custom，不泄漏第六个键。这样即使调用方塞进带
    奇怪 ``metadata["type"]`` 的自定义包，统计结构也不会漂移。
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

    组件解析顺序（均为「显式参数 > config > 内置缺省」）：
        * ``budget_policy``   —— 构造参数 > ``config.budget_policy``
                                > :class:`HeuristicBudgetPolicy`
        * ``relevance_scorer``—— 构造参数 > :class:`KeywordOverlapScorer`
                                （**缺省恒为关键词重叠**，不因配了向量后端
                                就自动切换；需要向量相似度必须显式注入）
        * ``cache``           —— 构造参数 > 按 ``config.cache_*`` 建 TTLCache

    线程安全：实例本身无锁。``cache`` 是可变共享状态，多线程并发
    ``build_result`` 时统计计数可能交错；需要严格计数请外部串行化。
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
        # 预算策略：显式传入优先，其次看 config，最后才用内置启发式
        if budget_policy is not None:
            self.budget_policy: BudgetPolicy = budget_policy
        elif self.config.budget_policy is not None:
            self.budget_policy = self.config.budget_policy
        else:
            self.budget_policy = HeuristicBudgetPolicy()
        # 打分器缺省恒为 KeywordOverlapScorer：零外部依赖即可跑，
        # 向量相似度须由调用方显式注入 EmbeddingSimilarityScorer
        self.relevance_scorer: RelevanceScorer = (
            relevance_scorer if relevance_scorer is not None else KeywordOverlapScorer()
        )
        # 缓存同时服务「系统指令包复用」与「打分器向量缓存」两类负载
        self.cache: TTLCache = (
            cache
            if cache is not None
            else TTLCache(
                max_size=self.config.cache_max_size,
                ttl_seconds=self.config.cache_ttl_seconds,
            )
        )
        try:
            # cl100k_base 与 OpenAI 系模型对齐；取不到编码器属配置问题
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # 编码器缺失一律转为配置错误
            raise ConfigError(f"tiktoken 编码器不可用: {exc}") from exc
        self._assigner = ExperimentAssigner()

    def _count_tokens(self, text: str) -> int:
        """精确 token 计数，全模块唯一口径

        一切 token 相关判断（预算、截断、利用率）都走这里，避免出现
        「有的地方按字符数、有的地方按 token 数」的口径漂移。
        用 ``len(encode(x))`` 而非近似估算，因为截断与预算都是硬契约。
        """
        return len(self.encoder.encode(text))

    def _compute_budget(
        self,
        config: ContextConfig,
        user_query: str,
        history: list[Message],
        system_instructions: str | None,
    ) -> BudgetInfo:
        """按复杂度缩放预算并拆分为预留 / 可用两部分

        缩放公式（spec §4.2）::

            scaled = int(max_tokens * (min_budget_ratio
                                       + (max_budget_ratio - min_budget_ratio)
                                       * complexity))

        即复杂度 0 时只给 ``min_budget_ratio`` 比例，复杂度 1 时给满
        ``max_budget_ratio`` 比例。再按 ``reserve_ratio`` 切出系统指令预留。

        降级契约：复杂度估计抛异常不外泄，按 :data:`_DEFAULT_COMPLEXITY`
        记满预算并记 WARNING —— 宁可给多让压缩阶段裁，也不要低估而丢内容。
        """
        try:
            complexity = self.budget_policy.estimate(
                user_query, history=history, system_instructions=system_instructions
            )
            # 复杂度夹进 [0, 1]，防御自定义策略返回越界值
            complexity = max(0.0, min(1.0, complexity))
        except Exception as exc:  # noqa: BLE001 - 复杂度估计失败一律降级，不外泄
            logger.warning(
                "复杂度估计失败，按默认复杂度 %.1f 降级: %s", _DEFAULT_COMPLEXITY, exc
            )
            complexity = _DEFAULT_COMPLEXITY
        span = config.max_budget_ratio - config.min_budget_ratio
        scaled = int(config.max_tokens * (config.min_budget_ratio + span * complexity))
        # 至少给 1 个 token，保证 token_utilization 的分母恒非零
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
        """汇总包缓存与打分器缓存命中数

        ``BuildStats.cache_hits`` / ``cache_misses`` 是**本次构建的增量**，
        所以 build_result 里前后各采样一次再相减。这里把两类缓存都算进来：
        包缓存（系统指令复用）与打分器可能自带的向量缓存。
        """
        stats = self.cache.stats()
        hits, misses = stats["hits"], stats["misses"]
        scorer_cache = getattr(self.relevance_scorer, "cache", None)
        if isinstance(scorer_cache, TTLCache):
            scorer_stats = scorer_cache.stats()
            hits += scorer_stats["hits"]
            misses += scorer_stats["misses"]
        return hits, misses

    def _system_packet(self, instructions: str) -> ContextPacket:
        """构造系统指令包，命中缓存时直接复用

        缓存键是指令文本的 sha256 —— 同一段系统指令反复构建就能复用同一个
        包对象，省掉重复的 token 计数，也让 ``cache_hits`` 可观测。

        包自带 ``relevance_score=1.0`` 与 ``priority=high``：系统指令不参与
        相关性评分（见 :meth:`_select`），恒保留、恒排前。
        """
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
        """把工具返回的命中字典转换为候选包，并按阈值过滤

        两处脏值防御（spec §6「不外泄」）：
            * ``score`` 无法转 float —— 并入缺分路径（0.0 且 ``None``），
              不让 ``ValueError`` 穿透 ``_gather``
            * ``importance`` 无法转 float —— 视同缺失，**不触发过滤**，
              因为「看不懂」不等于「不重要」

        **红线**：``relevance_score`` 只有在工具真的给了分数时才写数值；
        工具没给就留 ``None``。绝不能写成 ``float(raw_score or 0)`` 之类的
        写法 —— 那会把「预置 0.0 分」与「缺分」混为一谈，导致调用方
        预置的 0.0 分被下游误判为缺失而重算（B8 语义）。
        """
        packets: list[ContextPacket] = []
        for hit in hits:
            content = hit.get("content", "")
            raw_score = hit.get("score")
            try:
                score = 0.0 if raw_score is None else float(raw_score)
            except (TypeError, ValueError):
                # 转换失败并入缺分路径，不让 ValueError 穿透 _gather
                score, raw_score = 0.0, None
            # 严格小于：min_source_score=0.0 时零分命中靠 0.0 < 0.0 为假存活
            if score < config.min_source_score:
                continue
            metadata = dict(hit.get("metadata") or {})
            importance = metadata.get("importance")
            if importance is not None:
                try:
                    if float(importance) < config.min_importance:
                        continue
                except (TypeError, ValueError):
                    # 重要度无法解析时视同缺失，不触发过滤
                    pass
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
        """调用记忆工具召回命中；工具调用失败一律降级为空，不向调用方外泄

        降级覆盖三种形态：工具未挂载（直接空）、``run`` 抛异常、工具返回
        ``ToolStatus.ERROR``。三者都只记 WARNING 然后返回 ``[]`` —— 构建
        上下文不该因为记忆服务抖动而整个失败。
        """
        if self.memory_tool is None:
            return []
        try:
            response = self.memory_tool.run(
                {"action": "recall", "query": user_query, "limit": config.memory_limit}
            )
        except Exception as exc:  # noqa: BLE001 - 工具调用失败一律降级
            logger.warning("记忆检索失败: %s", exc)
            return []
        if getattr(response, "status", None) == ToolStatus.ERROR:
            logger.warning("记忆检索返回错误: %s", getattr(response, "text", ""))
            return []
        # 工具契约：data.hits 是命中字典列表，缺失或空都退化为空列表
        hits = (getattr(response, "data", None) or {}).get("hits") or []
        return self._hits_to_packets(hits, "memory", config)

    def _rag_packets(
        self, user_query: str, config: ContextConfig
    ) -> list[ContextPacket]:
        """调用知识工具检索命中；工具调用失败一律降级为空，不向调用方外泄

        与 :meth:`_memory_packets` 同构的三重降级，只是动作与载荷键不同
        （``query``/``chunks`` 对 ``recall``/``hits``）。
        """
        if self.rag_tool is None:
            return []
        try:
            response = self.rag_tool.run(
                {"action": "query", "question": user_query, "top_k": config.rag_limit}
            )
        except Exception as exc:  # noqa: BLE001 - 工具调用失败一律降级
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
        """Message 无 timestamp 字段，故用 metadata["position"] 承载新近性

        ``position`` 是**窗口相对下标**（0=最旧，n-1=最新），不是全局下标。
        ``_recency_of`` 依赖这个语义把历史映射到 [0.5, 1.0]，改动这里必须
        同步改那边。

        时间戳统一取 ``now`` 是刻意的：历史消息本身没有时刻，真正的「新旧」
        由 position 表达；若填伪时间戳会让 :meth:`_calculate_recency` 误判。
        正文格式 ``"{role}: {content}"`` 与 README / 规格示例保持一致。
        """
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
        """汇集所有候选信息

        顺序即统计顺序：系统指令 → 记忆 → 知识 → 历史 → 自定义。
        自定义包可能是调用方手搓的（``token_count=0``），这里补算一次，
        保证后续预算与截断拿到的是真 token 数。

        **不就地改写** ``relevance_score`` / ``metadata``：调用方留存的包
        对象与这里的引用是同一个，就地改会污染调用方那份（B8 语义）。
        """
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

    def _calculate_recency(self, timestamp: datetime) -> float:
        """指数衰减：24 小时内保持高分，之后逐渐衰减

        直接消费 ``_parse_timestamp`` 的出口（恒 tz-aware），此处不再做归一：
        解析处已把 naive 归一为 UTC、保留 offset-aware 的绝对时刻，
        下游再 ``replace(tzinfo=UTC)`` 会把偏移壁钟改写成 UTC 壁钟（时刻失真）。

        衰减曲线：``exp(-0.1 * age_hours / 24)`` —— 24 小时衰到 ``exp(-0.1)``
        ≈ 0.905，整体很缓和；再夹进 [0.1, 1.0]，保证再旧的包也有下限分，
        不会因为「太旧」被数学上归零。
        """
        age_hours = max(0.0, (datetime.now(tz=UTC) - timestamp).total_seconds() / 3600)
        return max(0.1, min(1.0, math.exp(-0.1 * age_hours / 24)))

    def _recency_of(self, packet: ContextPacket, history_count: int) -> float:
        """历史消息按 position 线性映射到 [0.5, 1.0]，其余按时间戳衰减

        ``metadata["position"]`` 是窗口相对下标（0=最旧，n-1=最新），由 _gather 写入。
        非历史包的时间戳先经 ``_parse_timestamp`` 归一（解析处归一，可容 naive），
        ``_calculate_recency`` 仍直接消费其出口，函数体内不做二次归一。

        ``span = max(history_count - 1, 1)`` 是**单位元护栏**：只有 1 条历史时
        若用 ``n-1 == 0`` 做除数会除零；退化成 1 之后位置 0 映射到 0.5，
        与「最旧那条拿 0.5 下限」一致。注意 ``history_count==2`` 时 span=1，
        此时 0.5 + 0.5*(p/1) 在 p=1 处恰好取到 1.0 —— 这是端点，不是 bug。
        """
        if packet.metadata.get("type") == "history":
            position = int(packet.metadata.get("position", 0))
            span = max(history_count - 1, 1)
            return max(0.5, min(1.0, 0.5 + 0.5 * (position / span)))
        return self._calculate_recency(_parse_timestamp(packet.timestamp))

    def _select(
        self,
        packets: list[ContextPacket],
        user_query: str,
        available_tokens: int,
        scaled_max_tokens: int,
        config: ContextConfig,
    ) -> tuple[list[ContextPacket], int, int]:
        """选择最相关的信息包

        系统指令包恒保留、不参与评分，也不受相关性阈值淘汰。对其余候选包：
        预置 ``relevance_score`` 的不重算（None 才重算），按「加权和」排序后
        在 ``available_tokens`` 内贪心填充，放不下的大包跳过而非终止。

        注意：包对象与调用方共享，本方法会**就地写入** ``relevance_score``
        （仅对原本为 None 的包），调用方持有的同一对象会看到计算后的分数。

        几处容易写错的口径：
            * 打分范围用 ``relevance_score is None`` 判定，**不是** falsy。
              预置 ``relevance_score=0.0`` 是合法的「我已打过 0 分」，
              用 ``not x`` 会被误判成缺失而重算（B8 红线）。
            * 阈值淘汰用严格 ``<``：``min_relevance=0.0`` 时零分命中靠
              ``0.0 < 0.0`` 为假存活。改 ``<=`` 会静默丢光零分命中。
            * 打分器返回长度必须与待打分包数**严格相等**：短了会静默截断
              （`zip` 丢尾部），长了会混入无效分。不等一律整批降级为 0.0。
            * 加权和是 ``w_rel*rel + w_rec*rec``（不是乘积），权重和恒为 1。

        Returns:
            (入选包列表, 因相关性丢弃数, 因预算丢弃数)
        """
        system_packets = [
            packet
            for packet in packets
            if packet.metadata.get("type") == "system_instruction"
        ]
        other_packets = [
            packet
            for packet in packets
            if packet.metadata.get("type") != "system_instruction"
        ]

        system_tokens = sum(packet.token_count for packet in system_packets)
        if system_tokens > scaled_max_tokens:
            # 系统指令本身就超预算：不再做任何取舍，整段保留并让下游压缩兜底
            logger.warning(
                "系统指令占用 %d tokens，已超过预算 %d，跳过打分选择",
                system_tokens,
                scaled_max_tokens,
            )
            return system_packets, 0, len(other_packets)

        # 只给「真缺分」的包打分；is None 是红线判定，见上方说明
        unscored = [
            packet for packet in other_packets if packet.relevance_score is None
        ]
        if unscored:
            try:
                scores = self.relevance_scorer.score_many(
                    [packet.content for packet in unscored], user_query
                )
            except Exception as exc:  # noqa: BLE001 - 打分失败一律降级，不外泄
                logger.warning("相关性打分失败，该批按 0.0 分降级: %s", exc)
                for packet in unscored:
                    packet.relevance_score = 0.0
            else:
                if len(scores) != len(unscored):
                    # 第二道防线：短列表会静默截断、长列表会混入无效分，长度不等一律整批降级
                    logger.warning(
                        "打分器返回 %d 个分数，与 %d 个待打分包不符，整批按 0.0 分降级",
                        len(scores),
                        len(unscored),
                    )
                    for packet in unscored:
                        packet.relevance_score = 0.0
                else:
                    for packet, score in zip(unscored, scores):
                        # 夹进 [0,1]：余弦自比可得 1.0000000000000002
                        packet.relevance_score = max(0.0, min(1.0, score))

        history_count = sum(
            1 for packet in other_packets if packet.metadata.get("type") == "history"
        )

        scored: list[tuple[float, ContextPacket]] = []
        dropped_by_relevance = 0
        for packet in other_packets:
            # 打分降级后恒为数值，此处 None 只是类型上的兜底
            relevance = (
                0.0 if packet.relevance_score is None else packet.relevance_score
            )
            if relevance < config.min_relevance:
                dropped_by_relevance += 1
                continue
            recency = self._recency_of(packet, history_count)
            combined = (
                config.relevance_weight * relevance + config.recency_weight * recency
            )
            scored.append((combined, packet))

        # 降序：加权分高的先占预算
        scored.sort(key=lambda item: item[0], reverse=True)

        selected = list(system_packets)
        current_tokens = 0
        dropped_by_budget = 0
        for _, packet in scored:
            if current_tokens + packet.token_count <= available_tokens:
                selected.append(packet)
                current_tokens += packet.token_count
            else:
                # 跳过而非终止：后面可能还有放得下的小包
                dropped_by_budget += 1

        return selected, dropped_by_relevance, dropped_by_budget

    def _structure(
        self, selected: list[ContextPacket], user_query: str
    ) -> list[ContextSection]:
        """把入选包路由到模板段落，只组织不渲染

        路由规则：
            * ``system_instruction``            -> ``Role & Policies``
            * ``rag`` 或 ``section=evidence``   -> ``Evidence``
            * 其余（memory / history / custom） -> ``Context``

        ``Task`` 恒为 user_query，``Output`` 恒为固定收尾语 —— 两者无条件
        出现，而 ``Role & Policies`` / ``Evidence`` / ``Context`` 只在有内容
        时才加入（对应 :data:`_TEMPLATE_ORDER` 里的可选段）。
        """
        policies: list[str] = []
        evidence: list[str] = []
        context: list[str] = []
        for packet in selected:
            packet_type = packet.metadata.get("type", "custom")
            if packet_type == "system_instruction":
                policies.append(packet.content)
            elif packet_type == "rag" or packet.metadata.get("section") == "evidence":
                evidence.append(packet.content)
            else:
                context.append(packet.content)

        sections: list[ContextSection] = []
        if policies:
            sections.append(ContextSection("Role & Policies", "\n".join(policies)))
        sections.append(ContextSection("Task", user_query))
        if evidence:
            sections.append(ContextSection("Evidence", "\n---\n".join(evidence)))
        if context:
            sections.append(ContextSection("Context", "\n".join(context)))
        sections.append(
            ContextSection("Output", "请基于以上信息，提供准确、有据的回答。")
        )
        return sections

    def _order(self, sections: dict[str, ContextSection]) -> list[ContextSection]:
        """把标题到段落的映射按模板顺序还原为列表

        顺序契约 :data:`_TEMPLATE_ORDER` =
        ``("Role & Policies", "Task", "Evidence", "Context", "Output")``。
        压缩阶段用 ``{title: section}`` 做中间结构，最后必须靠这里还原成
        稳定顺序，否则输出段落顺序会随 dict 构造顺序漂移。
        """
        return [sections[title] for title in _TEMPLATE_ORDER if title in sections]

    def _render(self, sections: list[ContextSection]) -> str:
        """把段落列表渲染为最终上下文字符串

        每段形如 ``[Title]\\nBody``，段间空一行。这个格式被 README 与规格
        示例引用，改动需同步文档；压缩阶段也用它来量 token（含分隔符），
        所以「量」和「出」走的是同一份字节，不会各算各的。
        """
        return "\n\n".join(f"[{section.title}]\n{section.body}" for section in sections)

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """按 token 精确截断文本

        **精确**的含义：返回值的 token 数恰为 ``min(len(tokens), max_tokens)``，
        不是「大致不超过」。做法是先 encode 再取前 n 个 token decode ——
        由于 BPE 分词可能回涨，被截断串的 token 数不保证等于 n，
        但这里保证的是「不超」且「尽量给满」，契约侧只承诺上限。

        ``max_tokens <= 0`` 直接给空串，避免 decode 空列表的边界歧义。
        """
        if max_tokens <= 0:
            return ""
        tokens = self.encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return self.encoder.decode(tokens[:max_tokens])

    def _truncate_section(
        self, section: ContextSection, budget: int
    ) -> ContextSection | None:
        """把段落 body 截断到不超过 budget 个 token；空间过小则整段丢弃

        预算要先扣掉两块开销：标题行 ``[Title]\\n`` 与压缩标记
        ``\\n[... 内容已压缩 ...]``。不扣就会溢出 budget。

        ``budget <= 50`` 整段丢弃：剩下这么点空间塞标题 + 标记都嫌挤，
        留一段几乎全空的壳反而误导阅读者以为内容还在。丢弃返回 ``None``，
        由 :meth:`_compress` 决定是否终止本轮纳入。
        """
        if budget <= 50:
            return None
        marker = "\n[... 内容已压缩 ...]"
        overhead = self._count_tokens(f"[{section.title}]\n") + self._count_tokens(
            marker
        )
        body = self._truncate_text(section.body, budget - overhead)
        if not body:
            return None
        return ContextSection(section.title, body + marker)

    def _compress(
        self,
        sections: list[ContextSection],
        max_tokens: int,
        config: ContextConfig,
    ) -> tuple[list[ContextSection], bool]:
        """按段优先级压缩

        恒定段 Role & Policies / Task / Output 优先全额保留（仅 Role & Policies
        允许截断），弹性段按 Evidence -> Context 顺序贪心纳入，首个放不下的弹性段
        截断后终止。

        优先级分三档：
            1. **Task / Output** —— 恒不截断、恒保留。这是用户问题与回答
               约定，砍掉就等于废掉这次构建。
            2. **Role & Policies** —— 优先整段保留；塞不下就截断（可截断的
               恒定段）。
            3. **Evidence -> Context** —— 弹性段，按此顺序贪心；第一个塞不下
               的截断后 **break**，不再尝试后面的段（后面的段更「软」）。

        之所以「首个放不下就终止」而不是继续塞：一旦进入截断模式，剩余预算
        已经很紧，继续硬塞只会产出一堆零碎片段；不如保留完整的靠前段落。

        返回 ``(段落列表, 是否压缩)``。``compressed=False`` 有两条路径：
        未启用压缩，或原样就能塞进 ``max_tokens``。调用方据此把
        ``compression_ratio`` 定为 1.0。
        """
        if not config.enable_compression:
            return sections, False
        if self._count_tokens(self._render(sections)) <= max_tokens:
            return sections, False

        logger.warning(
            "上下文超限(%d > %d tokens)，执行压缩",
            self._count_tokens(self._render(sections)),
            max_tokens,
        )

        by_title = {section.title: section for section in sections}

        # Task 与 Output 恒不截断，先为它们预留预算
        kept: dict[str, ContextSection] = {
            title: by_title[title] for title in ("Task", "Output") if title in by_title
        }
        mandatory_tokens = self._count_tokens(self._render(self._order(kept)))

        # Role & Policies：先试整段，塞不下再截断
        policies = by_title.get("Role & Policies")
        if policies is not None:
            candidate = {**kept, "Role & Policies": policies}
            if self._count_tokens(self._render(self._order(candidate))) <= max_tokens:
                kept = candidate
            else:
                truncated = self._truncate_section(
                    policies, max_tokens - mandatory_tokens - _SEPARATOR_MARGIN
                )
                if truncated is not None:
                    kept["Role & Policies"] = truncated

        # 弹性段按 Evidence -> Context 贪心纳入，首个放不下的截断后终止
        for title in ("Evidence", "Context"):
            section = by_title.get(title)
            if section is None:
                continue
            candidate = {**kept, title: section}
            if self._count_tokens(self._render(self._order(candidate))) <= max_tokens:
                kept = candidate
                continue
            remaining = (
                max_tokens
                - self._count_tokens(self._render(self._order(kept)))
                - _SEPARATOR_MARGIN
            )
            truncated = self._truncate_section(section, remaining)
            if truncated is not None:
                kept[title] = truncated
            break
        return self._order(kept), True

    def build_result(
        self,
        user_query: str,
        conversation_history: list[Message] | None = None,
        system_instructions: str | None = None,
        additional_packets: list[ContextPacket] | None = None,
        *,
        session_id: str | None = None,
    ) -> BuildResult:
        """构建上下文并返回上下文与统计

        五阶段编排（对齐规格 §5）：
            0. 实验分流 —— 有 ``config.experiment`` 且给了 ``session_id``
               则稳定分桶取变体并应用其覆盖；没给 ``session_id`` 则退化到
               **键序第一个变体**（spec §5.1）并记 WARNING。
            1. 汇集 + 预算 —— :meth:`_gather` 出候选包，:meth:`_compute_budget`
               出预留/可用两段预算。
            2. 选择 —— :meth:`_select` 在可用预算内贪心，产出入选包与两类丢弃计数。
            3. 组织 —— :meth:`_structure` 路由到模板段落，量一次 structured_tokens。
            4. 压缩 —— :meth:`_compress` 按优先级取舍，渲染得最终上下文。

        **异常边界**：本方法只允许 :class:`ConfigError` 外泄（实验配置非法等），
        其余异常都在各自子步骤里降级。缓存计数用「构建前后各采样一次相减」
        得到本次构建的增量，避免把历史累计算进来。
        """
        started = perf_counter()
        history = list(conversation_history or [])
        packets_input = list(additional_packets or [])
        config = self.config
        experiment: ExperimentSpec | None = config.experiment
        experiment_name: str | None = None
        variant: str | None = None

        # 阶段 0：实验分流
        if experiment is not None:
            experiment_name = experiment.name
            if session_id:
                config, variant = self._assigner.apply(config, experiment, session_id)
            else:
                # spec 是可变 dataclass：构造后把 variants 就地清空可绕过
                # __post_init__，next() 会裸抛 StopIteration 穿透 build_result，
                # 违反 §6「除配置错误外不外泄」——此处按配置错误转为 ConfigError。
                if not experiment.variants:
                    raise ConfigError("ExperimentSpec.variants 不能为空")
                variant = next(iter(experiment.variants))
                # §5.1「使用 variants 键序的首个变体」= 取名 + 应用其覆盖，
                # 与 apply() 同走「白名单 + replace + __post_init__ 复验」。
                # 只记名字不 apply 会让 stats.variant 归因与下游 config 不一致。
                # 单变体 spec 下 apply()->assign() 恒返回该变体，unit_id 不参与。
                fallback_spec = ExperimentSpec(
                    name=experiment.name,
                    variants={variant: experiment.variants[variant]},
                )
                config, _ = self._assigner.apply(config, fallback_spec, "")
                logger.warning(
                    "配置了实验 %s 但未提供 session_id，使用变体 %s",
                    experiment_name,
                    variant,
                )

        # 缓存增量：前后各采样一次，构建完成后相减
        before_hits, before_misses = self._cache_counters()

        # 阶段 1：汇集
        packets = self._gather(
            user_query, history, system_instructions, packets_input, config
        )
        budget = self._compute_budget(config, user_query, history, system_instructions)

        # 阶段 2：选择
        selected, dropped_by_relevance, dropped_by_budget = self._select(
            packets,
            user_query,
            budget.available_tokens,
            budget.scaled_max_tokens,
            config,
        )

        # 阶段 3：组织
        sections = self._structure(selected, user_query)
        structured_tokens = self._count_tokens(self._render(sections))

        # 阶段 4：压缩
        kept_sections, compressed = self._compress(
            sections, budget.scaled_max_tokens, config
        )
        context = self._render(kept_sections)
        final_tokens = self._count_tokens(context)

        after_hits, after_misses = self._cache_counters()

        stats = BuildStats(
            candidates_total=len(packets),
            candidates_by_source=_count_by_source(packets),
            selected_total=len(selected),
            selected_by_source=_count_by_source(selected),
            dropped_by_relevance=dropped_by_relevance,
            dropped_by_budget=dropped_by_budget,
            structured_tokens=structured_tokens,
            final_tokens=final_tokens,
            # 只统计非系统指令包的 token：系统指令属预留，不算「选进来的内容」
            selected_tokens=sum(
                packet.token_count
                for packet in selected
                if packet.metadata.get("type") != "system_instruction"
            ),
            budget=budget,
            # scaled_max_tokens 恒 >= 1（_compute_budget 里 max(1, scaled)），
            # 此处不再留零除护栏；压缩契约：未压缩时 final==structured 恒为 1.0，
            # 压缩后 structured > max_tokens >= 1，除法不会零除。
            #
            # 注意 token_utilization **不设上界**：Task/Output 恒不截断，
            # 系统指令又可能撑破预算，故 final_tokens 可能大于 scaled_max_tokens。
            # 上界由压缩契约兜底，不是这里能保证的。
            token_utilization=final_tokens / budget.scaled_max_tokens,
            compressed=compressed,
            # 未压缩时恒 1.0（明确定义，不是隐式）；压缩后为 final/structured
            compression_ratio=(
                1.0 if not compressed else final_tokens / structured_tokens
            ),
            cache_hits=after_hits - before_hits,
            cache_misses=after_misses - before_misses,
            duration_ms=(perf_counter() - started) * 1000,
            experiment=experiment_name,
            variant=variant,
        )
        if config.log_stats:
            logger.info("%s", stats.summary())
        return BuildResult(context=context, stats=stats)

    def build(
        self,
        user_query: str,
        conversation_history: list[Message] | None = None,
        system_instructions: str | None = None,
        additional_packets: list[ContextPacket] | None = None,
        *,
        session_id: str | None = None,
    ) -> str:
        """构建上下文，仅返回上下文字符串（向后兼容入口）

        纯委托：签名与历史版本保持一致，只丢掉 ``stats``。需要统计信息请用
        :meth:`build_result`。两者产出的 ``context`` 必然相同（同一份实现）。
        """
        return self.build_result(
            user_query,
            conversation_history,
            system_instructions,
            additional_packets,
            session_id=session_id,
        ).context
