"""动态 token 预算

按查询复杂度缩放 token 预算，并拆分为「系统指令预留」与「打分包可用」两部分。

典型用法：
    from hello_agents.context import HeuristicBudgetPolicy

    policy = HeuristicBudgetPolicy()
    complexity = policy.estimate(
        "如何配置 Qdrant 向量库？", history=[], system_instructions=None
    )
"""

import re
from dataclasses import dataclass
from typing import Protocol

from ..core import Message

__all__ = ["BudgetInfo", "BudgetPolicy", "HeuristicBudgetPolicy"]

_INTERROGATIVE_CN = (
    "如何",
    "为何",
    "为什么",
    "怎么",
    "怎样",
    "多少",
    "哪些",
    "哪个",
    "什么",
    "吗",
)
_INTERROGATIVE_EN = re.compile(r"\b(what|why|how|which|when|where)\b", re.IGNORECASE)
_RETRIEVAL_CN = ("根据", "依据", "文档", "参考", "手册", "资料")
_RETRIEVAL_EN = re.compile(r"\b(based on|according to)\b", re.IGNORECASE)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass
class BudgetInfo:
    """一次构建的预算明细

    Attributes:
        policy: 策略名，供 A/B 归因
        complexity: 查询复杂度(0.0-1.0)
        requested_max_tokens: 配置中的 max_tokens
        scaled_max_tokens: 复杂度缩放后的实际预算
        reserved_tokens: 为系统指令预留的 token
        available_tokens: 可供打分包竞争的 token
    """

    policy: str
    complexity: float
    requested_max_tokens: int
    scaled_max_tokens: int
    reserved_tokens: int
    available_tokens: int


class BudgetPolicy(Protocol):
    """复杂度估计策略"""

    def estimate(
        self,
        query: str,
        *,
        history: list[Message],
        system_instructions: str | None,
    ) -> float:
        """返回 [0.0, 1.0] 的复杂度分数"""
        ...


class HeuristicBudgetPolicy:
    """零成本启发式复杂度估计

    四项因子加权求和，权重和为 1.0：查询长度 0.40、疑问词 0.20、
    历史规模 0.20、检索线索 0.20。
    """

    name = "heuristic"

    def estimate(
        self,
        query: str,
        *,
        history: list[Message],
        system_instructions: str | None,
    ) -> float:
        length_factor = min(len(query) / 200, 1.0)
        interrogative = 1.0 if self._has_interrogative(query) else 0.0
        history_factor = min(len(history) / 10, 1.0)
        retrieval_cue = 1.0 if self._has_retrieval_cue(query) else 0.0
        return _clamp01(
            0.40 * length_factor
            + 0.20 * interrogative
            + 0.20 * history_factor
            + 0.20 * retrieval_cue
        )

    @staticmethod
    def _has_interrogative(query: str) -> bool:
        if any(marker in query for marker in _INTERROGATIVE_CN):
            return True
        return _INTERROGATIVE_EN.search(query) is not None

    @staticmethod
    def _has_retrieval_cue(query: str) -> bool:
        if any(cue in query for cue in _RETRIEVAL_CN):
            return True
        return _RETRIEVAL_EN.search(query) is not None
