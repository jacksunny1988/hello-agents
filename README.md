# hello-agents

一个从零实现的 Agent 框架，包含 LLM 客户端、四种 Agent 模式、工具系统、
四类认知记忆与 RAG 管道，以及生产化的上下文构建器。

## 安装

```bash
uv sync
```

可选能力通过 extras 安装：

```bash
uv sync --extra qdrant --extra neo4j --extra dashscope --extra rag --extra local
# 或一次性装齐：uv sync --extra all
```

## 模块概览

| 模块 | 说明 |
|---|---|
| `hello_agents.core` | `Agent` 基类、`Message`、`Config`、`HelloAgentsLLM`、异常体系 |
| `hello_agents.agents` | `SimpleAgent` / `ReActAgent` / `PlanSolveAgent` / `ReflectionAgent` |
| `hello_agents.tools` | `BaseTool`、`ToolRegistry`、`ToolChain` 与内置工具 |
| `hello_agents.memory` | 工作 / 情景 / 语义 / 感知四类记忆、RAG 管道、embedding 后端 |
| `hello_agents.context` | `ContextBuilder`：token 预算内的上下文组装 |

## 上下文构建（ContextBuilder）

在 token 预算内汇集系统指令、记忆命中、知识命中、对话历史与自定义信息，
按相关性与新近性打分排序后组织成结构化模板，并按需压缩。零外部依赖即可运行。

### 快速开始

```python
from hello_agents.context import ContextBuilder, ContextConfig

history = []  # list[Message]，元素为 hello_agents.core.Message(role=…, content=…)
builder = ContextBuilder(ContextConfig(max_tokens=4096))
context = builder.build("用户想了解什么？", conversation_history=history)
```

需要构建统计时改用 `build_result()` —— `build()` 保持返回 `str` 不变：

```python
result = builder.build_result("用户想了解什么？", conversation_history=history)
print(result.context)
print(result.stats.summary())
# candidates=5 selected=3 tokens=412/2908 utilization=0.14 complexity=0.42 ...
print(result.stats.to_dict())   # 可直接投递给日志平台
```

### 接入检索工具

```python
from hello_agents.context import ContextBuilder, ContextConfig
from hello_agents.memory import MemoryManager
from hello_agents.memory.rag import RAGPipeline
from hello_agents.tools.builtin import MemoryTool, RAGTool

manager = MemoryManager()
pipeline = RAGPipeline(memory_manager=manager)
builder = ContextBuilder(
    ContextConfig(memory_limit=10, rag_limit=5),
    memory_tool=MemoryTool(manager=manager),
    rag_tool=RAGTool(pipeline=pipeline),
)
```

工具调用失败会被记录并降级，不会中断构建。

### 配置项

| 字段 | 默认值 | 说明 |
|---|---|---|
| `max_tokens` | `3000` | 预算请求值，实际值按查询复杂度缩放 |
| `min_budget_ratio` | `0.5` | 复杂度为 0 时的预算比例 |
| `max_budget_ratio` | `1.0` | 复杂度为 1 时的预算比例 |
| `reserve_ratio` | `0.2` | 为系统指令预留的预算比例 |
| `relevance_weight` | `0.7` | 相关性权重，与 `recency_weight` 之和须为 1.0 |
| `recency_weight` | `0.3` | 新近性权重 |
| `min_relevance` | `0.1` | 低于此相关性的候选被丢弃 |
| `min_source_score` | `0.0` | 检索命中 score 的最低值 |
| `min_importance` | `0.0` | 记忆命中 `metadata.importance` 的最低值 |
| `memory_limit` / `rag_limit` | `10` / `5` | 记忆 / 知识检索条数上限 |
| `history_window` | `5` | 纳入的最近对话条数 |
| `enable_compression` | `True` | 超预算时是否按段压缩 |
| `cache_max_size` / `cache_ttl_seconds` | `256` / `3600` | 缓存容量与存活时间 |
| `log_stats` | `True` | 是否输出构建统计日志 |
| `budget_policy` | `None` | 复杂度估计策略，缺省用 `HeuristicBudgetPolicy` |
| `experiment` | `None` | A/B 实验声明 |

### 自定义相关性打分

默认使用零依赖的 `KeywordOverlapScorer`；需要向量相似度时**显式注入** `EmbeddingSimilarityScorer`
（`ContextBuilder` 的缺省打分器恒为 keyword，不会因配置了 embedding 后端就自动切换）：

```python
from hello_agents.context import ContextBuilder, EmbeddingSimilarityScorer
from hello_agents.memory import TFIDFEmbedding

builder = ContextBuilder(
    relevance_scorer=EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
)
```

`create_relevance_scorer("auto")`（`from hello_agents.context import create_relevance_scorer`）会在向量后端不可用时自动降级为关键词重叠。

### 自定义预算策略

`BudgetPolicy` 是复杂度估计的扩展点（稳定 `name` + `estimate`），缺省策略为
`HeuristicBudgetPolicy`。下例实现一个复杂度恒为 0.5 的最小策略，并经
`ContextConfig(budget_policy=…)` 注入：

```python
from hello_agents.context import BudgetPolicy, ContextBuilder, ContextConfig
from hello_agents.core import Message


class FixedBudgetPolicy(BudgetPolicy):
    """最小自定义策略：复杂度恒为 0.5"""

    name = "fixed-0.5"

    def estimate(
        self,
        query: str,
        *,
        history: list[Message],
        system_instructions: str | None,
    ) -> float:
        return 0.5


config = ContextConfig(max_tokens=4096, budget_policy=FixedBudgetPolicy())
builder = ContextBuilder(config)
result = builder.build_result("用户想了解什么？", conversation_history=[])
print(result.stats.budget.policy)      # -> "fixed-0.5"
print(result.stats.budget.complexity)  # -> 0.5
```

`estimate` 的 `system_instructions` 可忽略，但签名不得收窄（调用方始终以关键字传入）。

### A/B 测试

```python
from hello_agents.context import ContextBuilder, ContextConfig, ExperimentSpec

spec = ExperimentSpec(
    name="scoring_v1",
    variants={
        "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
        "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
    },
)
builder = ContextBuilder(ContextConfig(experiment=spec))
query, user_session_id = "用户想了解什么？", "session-42"
result = builder.build_result(query, session_id=user_session_id)
print(result.stats.experiment, result.stats.variant)
```

同一 `session_id` 恒定落在同一变体（基于 SHA-256 稳定分流，无随机状态）。
指标随 `BuildStats` 落入日志，聚合分析交给外部日志平台。

### 日志

模块使用标准库 `logging`，logger 名为 `hello_agents.context.builder` / `.scoring` / `.base`（`hello_agents.context` 为层级父节点，配置它或根 logger 即可一并捕获）：

```python
import logging

logging.basicConfig(level=logging.INFO)
```

`DEBUG` 仅输出时间戳解析兜底提示（各阶段明细暂未落地），`INFO` 输出单行统计摘要（受 `log_stats` 控制），
`WARNING` 输出检索失败、预算被系统指令占满、压缩触发、配置实验但缺 `session_id`。

### 示例

```bash
uv run python examples/context_builder_demo.py
```

## 测试

```bash
uv run pytest tests/ -q
uv run ruff check hello_agents tests examples
uv run ruff format --check hello_agents tests examples
```

## 设计文档

- `docs/specs/2026-09-23-context-management-design.md` —— 上下文管理优化设计 Spec
- `docs/plans/2026-09-23-context-management-plan.md` —— 对应实现计划
