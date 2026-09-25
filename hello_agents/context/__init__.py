"""上下文构建

在 token 预算内汇集系统指令、记忆命中、知识命中、对话历史与自定义信息，
按相关性与新近性打分排序后组织成结构化模板，并按需压缩。

零外部依赖即可运行：不注入工具与向量服务时，仅凭系统指令、对话历史与
自定义信息包也能工作。默认相关性打分为关键词重叠；需要向量相似度时可显式
传入 `EmbeddingSimilarityScorer` 或使用 `create_relevance_scorer("auto")`
自动降级。

典型用法：
    from hello_agents.context import ContextBuilder, ContextConfig

    builder = ContextBuilder(ContextConfig(max_tokens=4096))
    context = builder.build("用户想了解什么？", conversation_history=history)
    result = builder.build_result("用户想了解什么？", conversation_history=history)
    print(result.stats.summary())
"""

from .base import (
    BuildResult,
    BuildStats,
    ContextConfig,
    ContextPacket,
    ContextSection,
)
from .budget import BudgetInfo, BudgetPolicy, HeuristicBudgetPolicy
from .builder import ContextBuilder
from .cache import TTLCache
from .experiment import ExperimentAssigner, ExperimentSpec
from .scoring import (
    EmbeddingSimilarityScorer,
    KeywordOverlapScorer,
    RelevanceScorer,
    create_relevance_scorer,
)

__all__ = [
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
]
