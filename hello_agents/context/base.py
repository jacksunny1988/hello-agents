import tiktoken
from dataclasses import dataclass
from typing import List,Optional, Dict, Any
from datetime import datetime

from ..core import Message


@dataclass
class ContextPacket:
    """候选信息包

    Attributes:
        content: 信息内容
        timestamp: 时间戳
        token_count: Token 数量
        relevance_score: 相关性分数(0.0-1.0)
        metadata: 可选的元数据
    """
    content: str
    timestamp: datetime
    token_count: int
    relevance_score: float = 0.5
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.metadata is None:
            self.metadata = {}
        # 确保相关性分数在有效范围内
        self.relevance_score = max(0.0, min(1.0, self.relevance_score))

@dataclass
class ContextConfig:
    """上下文构建配置

    Attributes:
        max_tokens: 最大 token 数量
        reserve_ratio: 为系统指令预留的比例(0.0-1.0)
        min_relevance: 最低相关性阈值
        enable_compression: 是否启用压缩
        recency_weight: 新近性权重(0.0-1.0)
        relevance_weight: 相关性权重(0.0-1.0)
    """
    max_tokens: int = 3000
    reserve_ratio: float = 0.2
    min_relevance: float = 0.1
    enable_compression: bool = True
    recency_weight: float = 0.3
    relevance_weight: float = 0.7

    def __post_init__(self):
        """验证配置参数"""
        assert 0.0 <= self.reserve_ratio <= 1.0, "reserve_ratio 必须在 [0, 1] 范围内"
        assert 0.0 <= self.min_relevance <= 1.0, "min_relevance 必须在 [0, 1] 范围内"
        assert abs(self.recency_weight + self.relevance_weight - 1.0) < 1e-6, \
            "recency_weight + relevance_weight 必须等于 1.0"

class ContextBuilder:
    """上下文构建器

    根据配置和候选信息包构建最终的上下文内容。
    """

    def __init__(self, config: Optional[ContextConfig] = None):
        self.config = config or ContextConfig()
        self.encoder = tiktoken.get_encoding("cl100k_base")  # 使用适当的编码器

    def _gather(self, user_query: str,                
                conversation_history: Optional[List[Message]] = None,
                system_instructions: Optional[str] = None,
                custom_packets: Optional[List[ContextPacket]] = None) -> List[ContextPacket]:
        """汇集所有候选信息

        `Args:
            user_query: 用户查询
            conversation_history: 对话历史
            system_instructions: 系统指令
            custom_packets: 自定义信息包

        Returns:
            `List[ContextPacket]: 候选信息列表
        """
        packets = []
        # 1. 添加系统指令(最高优先级,不参与评分)
        if system_instructions:
            packets.append(ContextPacket(
                content=system_instructions,
                timestamp=datetime.now(),
                token_count=len(self.encoder.encode(system_instructions)),
                relevance_score=1.0,
                metadata={"type": "system_instruction", "priority": "high"}
            ))
        # 2. 从记忆系统检索相关记忆
        if self.memory_tool:
            try:
                memory_results = self.memory_tool.run({
                    "action": "search",
                    "query": user_query,
                    "limit": 10,
                    "min_importance": 0.3
                })
                # 解析记忆结果并转换为 ContextPacket
                memory_packets = self._parse_memory_results(memory_results, user_query)
                packets.extend(memory_packets)
            except Exception as e:
                print(f"[WARNING] 记忆检索失败: {e}")

        # 3. 从 RAG 系统检索相关知识
        if self.rag_tool:
            try:
                rag_results = self.rag_tool.run({
                    "action": "search",
                    "query": user_query,
                    "limit": 5,
                    "min_score": 0.3
                })
                # 解析 RAG 结果并转换为 ContextPacket
                rag_packets = self._parse_rag_results(rag_results, user_query)
                packets.extend(rag_packets)
            except Exception as e:
                print(f"[WARNING] RAG 检索失败: {e}")

        # 4.添加对话历史(仅保留最近的 N 条)
        if conversation_history:
            recent_history = conversation_history[-5:]  # 默认保留最近 5 条
            for msg in recent_history:
                packets.append(ContextPacket(
                    content=f"{msg.role}: {msg.content}",
                    timestamp=msg.timestamp if hasattr(msg, 'timestamp') else datetime.now(),
                    token_count=len(self.encoder.encode(msg.content)),
                    relevance_score=0.6,  # 历史消息的基础相关性
                    metadata={"type": "conversation_history", "role": msg.role}
                ))
        # 5. 添加自定义信息包
        if custom_packets:
            packets.extend(custom_packets)

        print(f"[ContextBuilder] 汇集了 {len(packets)} 个候选信息包")

    def _select(
        self,
        packets: List[ContextPacket],
        user_query: str,
        available_tokens: int
    ) -> List[ContextPacket]:
        """选择最相关的信息包

        Args:
            packets: 候选信息包列表
            user_query: 用户查询(用于计算相关性)
            available_tokens: 可用的 token 数量

        Returns:
            List[ContextPacket]: 选中的信息包列表
        """
        # 1. 分离系统指令和其他信息
        system_packets = [p for p in packets if p.metadata.get("type") == "system_instruction"]
        other_packets = [p for p in packets if p.metadata.get("type") != "system_instruction"]

        # 2. 计算系统指令占用的 token
        system_tokens = sum(p.token_count for p in system_packets)
        remaining_tokens = available_tokens - system_tokens

        if remaining_tokens <= 0:
            print("[WARNING] 系统指令已占满所有 token 预算")
            return system_packets

        # 3. 为其他信息计算综合分数
        scored_packets = []
        for packet in other_packets:
            # 计算相关性分数(如果尚未计算)
            if packet.relevance_score == 0.5:  # 默认值,需要重新计算
                relevance = self._calculate_relevance(packet.content, user_query)
                packet.relevance_score = relevance

            # 计算新近性分数
            recency = self._calculate_recency(packet.timestamp)

            # 综合分数 = 相关性权重 × 相关性 + 新近性权重 × 新近性
            combined_score = (
                self.config.relevance_weight * packet.relevance_score +
                self.config.recency_weight * recency
            )

            # 过滤低于最小相关性阈值的信息
            if packet.relevance_score >= self.config.min_relevance:
                scored_packets.append((combined_score, packet))

        # 4. 按分数降序排序
        scored_packets.sort(key=lambda x: x[0], reverse=True)

        # 5. 贪心选择:按分数从高到低填充,直到达到 token 上限
        selected = system_packets.copy()
        current_tokens = system_tokens

        for  packet in scored_packets:
            if current_tokens + packet.token_count <= available_tokens:
                selected.append(packet)
                current_tokens += packet.token_count
            else:
                # Token 预算已满,停止选择
                break

        print(f"[ContextBuilder] 选择了 {len(selected)} 个信息包,共 {current_tokens} tokens")
        return selected

    def _calculate_relevance(self, content: str, query: str) -> float:
        """计算内容与查询的相关性

        使用简单的关键词重叠算法。在生产环境中,可以替换为向量相似度计算。

        Args:
            content: 内容文本
            query: 查询文本

        Returns:
            float: 相关性分数(0.0-1.0)
        """
        # 分词(简单实现,可以使用更复杂的分词器)
        content_words = set(content.lower().split())
        query_words = set(query.lower().split())

        if not query_words:
            return 0.0

        # Jaccard 相似度
        intersection = content_words & query_words
        union = content_words | query_words

        return len(intersection) / len(union) if union else 0.0

    def _calculate_recency(self, timestamp: datetime) -> float:
        """计算时间近因性分数

        使用指数衰减模型,24小时内保持高分,之后逐渐衰减。

        Args:
            timestamp: 信息的时间戳

        Returns:
            float: 新近性分数(0.0-1.0)
        """
        import math

        age_hours = (datetime.now() - timestamp).total_seconds() / 3600

        # 指数衰减:24小时内保持高分,之后逐渐衰减
        decay_factor = 0.1  # 衰减系数
        recency_score = math.exp(-decay_factor * age_hours / 24)

        return max(0.1, min(1.0, recency_score))  # 限制在 [0.1, 1.0] 范围内

    def _structure(self, selected_packets: List[ContextPacket], user_query: str) -> str:
        """将选中的信息包组织成结构化的上下文模板

        Args:
            selected_packets: 选中的信息包列表
            user_query: 用户查询

        Returns:
            str: 结构化的上下文字符串
        """
        # 按类型分组
        system_instructions = []
        evidence = []
        context = []

        for packet in selected_packets:
            packet_type = packet.metadata.get("type", "general")

            if packet_type == "system_instruction":
                system_instructions.append(packet.content)
            elif packet_type in ["rag_result", "knowledge"]:
                evidence.append(packet.content)
            else:
                context.append(packet.content)

        # 构建结构化模板
        sections = []

        # [Role & Policies]
        if system_instructions:
            sections.append("[Role & Policies]\n" + "\n".join(system_instructions))

        # [Task]
        sections.append(f"[Task]\n{user_query}")

        # [Evidence]
        if evidence:
            sections.append("[Evidence]\n" + "\n---\n".join(evidence))

        # [Context]
        if context:
            sections.append("[Context]\n" + "\n".join(context))

        # [Output]
        sections.append("[Output]\n请基于以上信息,提供准确、有据的回答。")

        return "\n\n".join(sections)


    def build(
        self,
        user_query: str,
        conversation_history: Optional[List[Message]] = None,
        system_instructions: Optional[str] = None,
        additional_packets: Optional[List[ContextPacket]] = None
    ) -> str:
         # 1. Gather: 收集候选信息
        packets = self._gather(
            user_query=user_query,
            conversation_history=conversation_history or [],
            system_instructions=system_instructions,
            additional_packets=additional_packets or []
        )
        
        # 2. Select: 筛选与排序
        selected_packets = self._select(packets, user_query)
        
        # 3. Structure: 组织成结构化模板
        structured_context = self._structure(
            selected_packets=selected_packets,
            user_query=user_query,
            system_instructions=system_instructions
        )
        
        # 4. Compress: 压缩与规范化（如果超预算）
        final_context = self._compress(structured_context)
        
        return final_context
