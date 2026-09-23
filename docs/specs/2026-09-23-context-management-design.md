# Agent 上下文管理优化设计 Spec

| 项 | 值 |
|---|---|
| 日期 | 2026-09-23 |
| 范围 | `hello_agents/context/`（不改动 `hello_agents/agents/`） |
| 状态 | 待评审 |
| 前置讨论 | ContextBuilder 五条生产化最佳实践优化方案 |

---

## 1. 背景与目标

### 1.1 背景

`hello_agents/context/base.py` 中的 `ContextBuilder` 是一个「收集 → 选择 → 组织 → 压缩」四段式上下文构建器，职责是在 token 预算内为 LLM 组装 prompt：汇集系统指令、记忆命中、RAG 命中、对话历史与自定义信息，按相关性与新近性打分排序后贪心填充预算，再组织成结构化模板并按需压缩。

当前实现存在 15 处确定缺陷（§2），`build()` 无法运行到结束。

### 1.2 目标

按下表五条生产化实践重构本模块。每条实践对应一个明确落点：

| # | 实践 | 落点 |
|---|---|---|
| P1 | 动态调整 token 预算（按任务复杂度） | `hello_agents/context/budget.py` |
| P2 | 相关性计算优化（关键词重叠 → 向量相似度） | `hello_agents/context/scoring.py` |
| P3 | 缓存机制（系统指令 / 知识库内容免重复计算） | `hello_agents/context/cache.py` |
| P4 | 监控与日志（选中数量、token 使用率等构建统计） | `hello_agents/context/base.py`（`BuildStats` + `logging`） |
| P5 | A/B 测试（相关性权重、新近性权重等关键参数） | `hello_agents/context/experiment.py` |

### 1.3 非目标

- **不改动 `hello_agents/agents/`**。Agent（`SimpleAgent` / `ReActAgent` 等）如何调用 `ContextBuilder` 的接入契约留待下一轮设计与实现。
- **不引入新外部依赖**。向量能力复用 `hello_agents/memory/embedding.py` 既有实现。
- **不做实验结果持久化与显著性检验**。A/B 仅负责稳定分流与指标落日志，聚合分析交给外部日志平台。
- **不做 LLM 摘要式压缩**。压缩保持结构化截断，不额外调用模型。

### 1.4 设计原则

- **零外部依赖可运行，配置后自动升级**（对齐 `MemoryConfig` 的既有哲学）：不注入任何工具与向量服务时，`ContextBuilder` 仍可仅凭系统指令、对话历史与自定义信息包工作。
- **公开 API 向后兼容**：`build()` 的签名与返回类型 `str` 不变，现有调用零改动。
- **模块边界可独立理解与测试**：每个单元只做一件事，通过明确定义的接口通信。

---

## 2. 现状缺陷清单

以下缺陷在本次重构中一并修复，每条至少对应一条回归测试断言（§7.3）。

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| B1 | `_gather` 末尾 | 缺少 `return packets` | 返回 `None`，下游全部失败 |
| B2 | `_gather` | `self.memory_tool` / `self.rag_tool` 从未在 `__init__` 定义 | `AttributeError` |
| B3 | `_gather` | `_parse_memory_results` / `_parse_rag_results` 被调用但不存在 | `AttributeError` |
| B4 | `build()` 调 `_gather` | 实参名 `additional_packets`，形参名 `custom_packets` | `TypeError` |
| B5 | `build()` 调 `_select` | 缺必需参数 `available_tokens` | `TypeError` |
| B6 | `build()` 调 `_structure` | 多传不存在的形参 `system_instructions` | `TypeError` |
| B7 | `build()` 调 `_compress` | 缺必需参数 `max_tokens` | `TypeError` |
| B8 | `_select` | 用 `relevance_score == 0.5` 作「未评分」哨兵 | 真实 0.5 被覆盖；预置 0.5 被重算 |
| B9 | `_select` 贪心填充 | 遇第一个放不下的包即 `break` | 跳过后面更小、其实放得下的包 |
| B10 | `_gather` 与 `_count_tokens` | tiktoken 与中英文字符启发式两套 token 口径混用；`_truncate_text` 按字符比例猜测 | 预算算术不可靠 |
| B11 | `ContextConfig` | `reserve_ratio`、`enable_compression` 声明后从未被读取 | 配置项失效 |
| B12 | 全文 | 用 `print` 输出诊断；用 `assert` 做配置校验 | 无法关断/采集日志；`python -O` 下校验被剥离 |
| B13 | `_gather` | 工具调用契约错误：`action:"search"` 不存在（真实为 `recall` / `query`）；`min_importance` / `min_score` 参数记忆层不支持 | 检索必然失败 |
| B14 | `_gather` | `Message` 无 `timestamp` 字段（见 `core/message.py`），却读取 `msg.timestamp` | 历史消息新近性全退化为「当前时刻」 |
| B15 | `tools/builtin/rag_tool.py` | `chunk.to_dict()` 丢弃 `score`（`MemoryTool.recall` 则保留，不对称） | RAG 相关性无法传入上下文层 |

---

## 3. 架构

### 3.1 模块划分

`hello_agents/context/` 按横切能力拆为五个模块，与 `hello_agents/memory/` 子包的多文件风格一致：

```
hello_agents/context/
├── __init__.py       中文模块 docstring +「典型用法：」+ 按字母序 __all__
├── base.py           编排器与核心数据类型（P4 落点）
├── budget.py         P1 动态 token 预算
├── scoring.py        P2 相关性打分
├── cache.py          P3 TTL + LRU 缓存
└── experiment.py     P5 轻量 A/B
```

**为何按横切能力而非流水线阶段拆**：`cache`、`experiment` 是横切关注点，贯穿 Gather/Select/Structure/Compress 多个阶段，塞进任一阶段文件都会造成职责混杂；而 `ContextPacket` / `BuildStats` 等跨阶段共享类型也需要稳定归属。按能力拆后每个文件 80–450 行，单一职责，测试文件可一一对应。

### 3.2 各单元职责边界

| 单元 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `cache.TTLCache` | 通用 TTL + LRU 键值缓存，带命中统计 | `get` / `put` / `clear` / `stats` | 无 |
| `budget.BudgetPolicy` | 估算查询复杂度 `[0,1]` | `estimate(query, *, history, system_instructions)` | 无 |
| `budget.HeuristicBudgetPolicy` | 零成本启发式实现（默认） | 同上 | 无 |
| `scoring.RelevanceScorer` | 内容与查询的相关性 `[0,1]` | `score` / `score_many` | 无 |
| `scoring.KeywordOverlapScorer` | Jaccard 词重叠（默认，零依赖） | 同上 | 无 |
| `scoring.EmbeddingSimilarityScorer` | 向量余弦相似度 | 同上 | `memory.embedding`、`cache.TTLCache` |
| `experiment.ExperimentSpec` | 声明实验名与变体参数覆盖 | 构造即声明 | 无 |
| `experiment.ExperimentAssigner` | 按 unit_id 稳定分流并生成配置副本 | `assign` / `apply` | `base.ContextConfig` |
| `base.ContextBuilder` | 编排四阶段，产出上下文与统计 | `build` / `build_result` | 上述全部 + `tools.BaseTool` |

依赖方向单一：`base` → `budget` / `scoring` / `cache` / `experiment`；`scoring` → `memory.embedding`。`memory` 与 `tools` 均不反向依赖 `context`，无循环。

### 3.3 复用的既有实现（不重写）

| 复用对象 | 路径 | 用途 |
|---|---|---|
| `BaseEmbedding`、`cosine_similarity`、`create_embedding`、`TFIDFEmbedding` | `hello_agents/memory/embedding.py` | P2 向量相似度与「dashscope → local → tfidf」降级策略 |
| `MemoryTool`（`recall` → `data["hits"]`） | `hello_agents/tools/builtin/memory_tool.py` | 记忆检索入口（同步，避免扩大 async API 面） |
| `RAGTool`（`query` → `data["chunks"]`） | `hello_agents/tools/builtin/rag_tool.py` | 知识检索入口 |
| `ConfigError`（`AgentError` 子类） | `hello_agents/core/exceptions.py` | 替换 `assert` 校验 |
| `Message`、`MessageRole` | `hello_agents/core/message.py` | 对话历史输入类型 |
| TTL + LRU 淘汰策略 | `hello_agents/memory/types/working.py` | `cache.TTLCache` 的实现参考 |

---

## 4. 公开 API

### 4.1 `hello_agents/context/cache.py`

```python
class TTLCache(Generic[K, V]):
    def __init__(self, max_size: int = 256, ttl_seconds: float = 3600.0) -> None: ...
    def get(self, key: K) -> V | None: ...
    def put(self, key: K, value: V) -> None: ...
    def clear(self) -> None: ...
    def stats(self) -> dict[str, int]: ...   # {"hits", "misses", "size", "evictions"}
```

淘汰顺序：过期优先（按写入时间），未过期则淘汰最久未使用项。`put` 到达 `max_size` 时先清过期项，仍超限才触发 LRU 淘汰。

### 4.2 `hello_agents/context/budget.py`

```python
@dataclass
class BudgetInfo:
    policy: str                 # 策略名，供 A/B 归因
    complexity: float           # 0.0–1.0
    requested_max_tokens: int   # ContextConfig.max_tokens
    scaled_max_tokens: int      # 复杂度缩放后的实际预算
    reserved_tokens: int        # 为系统指令预留
    available_tokens: int       # 可供打分包竞争

class BudgetPolicy(Protocol):
    def estimate(
        self,
        query: str,
        *,
        history: list[Message],
        system_instructions: str | None,
    ) -> float: ...             # 返回 [0.0, 1.0]

class HeuristicBudgetPolicy:
    """零成本启发式复杂度估计，BudgetPolicy 的默认实现。"""
```

**启发式复杂度公式**（四项权重和为 1.0，结果截断到 `[0,1]`）：

```
complexity = clamp01(
      0.40 * min(len(query) / 200, 1.0)        # 查询长度
    + 0.20 * is_interrogative                  # 含疑问词：何/如何/为什么/怎么/what/why/how/which/when/where
    + 0.20 * min(len(history) / 10, 1.0)       # 历史规模
    + 0.20 * has_retrieval_cue                 # 含「根据/依据/文档/参考/手册/based on/according to」等线索
)
```

**预算缩放**：

```
scaled_max_tokens = int(max_tokens * (min_budget_ratio + (max_budget_ratio - min_budget_ratio) * complexity))
reserved_tokens   = int(scaled_max_tokens * reserve_ratio)
available_tokens  = scaled_max_tokens - reserved_tokens
```

`BudgetPolicy` 为 `Protocol`，可注入 LLM 复杂度估计器；启发式做默认，升级零侵入。

### 4.3 `hello_agents/context/scoring.py`

```python
class RelevanceScorer(Protocol):
    def score(self, content: str, query: str) -> float: ...
    def score_many(self, contents: list[str], query: str) -> list[float]: ...
    # 两者均返回 [0.0, 1.0]；score_many 须批量计算（向量实现走一次 embed_texts 调用）

class KeywordOverlapScorer:
    """Jaccard 词重叠，零依赖，默认实现。"""

class EmbeddingSimilarityScorer:
    def __init__(
        self,
        embedding: BaseEmbedding | None = None,   # 缺省 create_embedding("auto")
        cache: TTLCache | None = None,            # 缺省内部新建，键为 sha256(text)
    ) -> None: ...

def create_relevance_scorer(
    kind: Literal["keyword", "embedding", "auto"] = "keyword",
) -> RelevanceScorer: ...
```

- 默认 `keyword`：与「零外部依赖即可运行」原则一致，测试与示例可完全离线。
- 余弦归一化：`score = max(0.0, min(1.0, cosine_similarity(query_vec, content_vec)))`。
- `kind="auto"`：优先 `EmbeddingSimilarityScorer`，构造失败时降级 `KeywordOverlapScorer` 并 `logger.warning`。

### 4.4 `hello_agents/context/experiment.py`

```python
@dataclass
class ExperimentSpec:
    name: str                                   # 实验名，参与分流哈希
    variants: dict[str, dict[str, Any]]         # 变体名 -> ContextConfig 字段覆盖
    weights: dict[str, float] | None = None     # 缺省各变体均匀

class ExperimentAssigner:
    def __init__(self, seed: str = "hello-agents") -> None: ...
    def assign(self, spec: ExperimentSpec, unit_id: str) -> str: ...
    def apply(
        self,
        config: ContextConfig,
        spec: ExperimentSpec,
        unit_id: str,
    ) -> tuple[ContextConfig, str]: ...         # (应用覆盖后的配置副本, 变体名)
```

**稳定分流算法**（无 `random` 全局状态，结果可复现）：

```
digest = sha256(f"{seed}:{spec.name}:{unit_id}".encode()).digest()
bucket = int.from_bytes(digest[:8], "big") / 2**64      # 落入 [0, 1)
# 将 weights 归一化为累积区间，按 variants 键插入序排列，bucket 落入哪个区间即取哪个变体
```

同一 `unit_id` 在同一 `spec.name` 与 `seed` 下永远得到同一变体。

**可覆盖的 `ContextConfig` 字段**（白名单，共 12 个）：
`recency_weight`、`relevance_weight`、`min_relevance`、`max_tokens`、`reserve_ratio`、`min_budget_ratio`、`max_budget_ratio`、`history_window`、`memory_limit`、`rag_limit`、`enable_compression`、`log_stats`。

`apply()` 用 `dataclasses.replace(config, **overrides)` 生成副本，**之后**重跑 `ContextConfig.__post_init__` 的全部校验（含 `recency_weight + relevance_weight == 1.0`）。字段名不在白名单内 → 抛 `ConfigError`。

### 4.5 `hello_agents/context/base.py`

```python
@dataclass
class ContextPacket:
    content: str
    timestamp: datetime
    token_count: int = 0                      # 0 表示由 ContextBuilder 计算并缓存
    relevance_score: float | None = None      # None 表示待计算（修复 B8 哨兵）
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class ContextSection:
    title: str                                # "Role & Policies" | "Task" | "Evidence" | "Context" | "Output"
    body: str

@dataclass
class ContextConfig:
    # 既有字段
    max_tokens: int = 3000
    reserve_ratio: float = 0.2
    min_relevance: float = 0.1
    enable_compression: bool = True
    recency_weight: float = 0.3
    relevance_weight: float = 0.7
    # 新增：预算（P1）
    min_budget_ratio: float = 0.5
    max_budget_ratio: float = 1.0
    budget_policy: BudgetPolicy | None = None          # None -> HeuristicBudgetPolicy()
    # 新增：检索（替换 _gather 中的硬编码 10 / 5 / 0.3）
    memory_limit: int = 10
    rag_limit: int = 5
    min_importance: float = 0.0                        # 取回后按 metadata["importance"] 过滤
    min_source_score: float = 0.0                      # 取回后按命中 score 过滤
    history_window: int = 5
    # 新增：缓存（P3）
    cache_max_size: int = 256
    cache_ttl_seconds: float = 3600.0
    # 新增：监控与实验（P4 / P5）
    log_stats: bool = True
    experiment: ExperimentSpec | None = None

    def __post_init__(self) -> None: ...               # 校验改抛 ConfigError（修复 B12）

@dataclass
class BuildStats:
    candidates_total: int
    candidates_by_source: dict[str, int]      # {"system_instruction","memory","rag","history","custom"}
    selected_total: int
    selected_by_source: dict[str, int]
    dropped_by_relevance: int
    dropped_by_budget: int
    structured_tokens: int                    # 压缩前
    final_tokens: int                         # 压缩后
    selected_tokens: int                      # 入选打分包的 token 合计
    budget: BudgetInfo
    token_utilization: float                  # final_tokens / scaled_max_tokens
    compressed: bool
    compression_ratio: float                  # final_tokens / structured_tokens（1.0 表示未压缩）
    cache_hits: int                           # 系统指令包缓存与 embedding 缓存命中合并计数
    cache_misses: int
    duration_ms: float
    experiment: str | None
    variant: str | None
    def to_dict(self) -> dict[str, Any]: ...
    def summary(self) -> str: ...             # 单行摘要，供日志

@dataclass
class BuildResult:
    context: str
    stats: BuildStats

class ContextBuilder:
    def __init__(
        self,
        config: ContextConfig | None = None,
        *,
        memory_tool: BaseTool | None = None,
        rag_tool: BaseTool | None = None,
        relevance_scorer: RelevanceScorer | None = None,
        budget_policy: BudgetPolicy | None = None,
        cache: TTLCache | None = None,
    ) -> None: ...

    def build(
        self,
        user_query: str,
        conversation_history: list[Message] | None = None,
        system_instructions: str | None = None,
        additional_packets: list[ContextPacket] | None = None,
        *,
        session_id: str | None = None,
    ) -> str: ...                                 # 兼容层，等价 build_result(...).context

    def build_result(
        self,
        user_query: str,
        conversation_history: list[Message] | None = None,
        system_instructions: str | None = None,
        additional_packets: list[ContextPacket] | None = None,
        *,
        session_id: str | None = None,
    ) -> BuildResult: ...
```

构造参数优先级：显式传入的 `budget_policy` / `relevance_scorer` > `config.budget_policy` > 模块默认实现。

### 4.6 来源类型与模板路由

`metadata["type"]` 标准化为五个取值：`system_instruction`、`memory`、`rag`、`history`、`custom`。

| `metadata["type"]` | 路由到 | 说明 |
|---|---|---|
| `system_instruction` | `[Role & Policies]` | 不参与评分，恒定保留 |
| `rag`，或 `metadata["section"] == "evidence"` | `[Evidence]` | 多条之间以 `\n---\n` 分隔 |
| 其余（`memory` / `history` / `custom`） | `[Context]` | 多条之间以 `\n` 连接 |

模板固定为五段，顺序恒定：`[Role & Policies]` → `[Task]` → `[Evidence]` → `[Context]` → `[Output]`。`[Task]` 内容为 `user_query`；`[Output]` 内容为固定收尾指令「请基于以上信息，提供准确、有据的回答。」。空段省略，但 `[Task]` 与 `[Output]` 恒不省略。

---

## 5. 数据流

`build_result()` 依次执行四个阶段，全程记录 `BuildStats`。

### 5.1 阶段 0：实验分流（若有）

若 `config.experiment` 非空且 `session_id` 非空 → `ExperimentAssigner.apply()` 得到配置副本与变体名，写入 `stats.experiment` / `stats.variant`，后续阶段使用该副本。
若配置了实验但 `session_id` 为空 → 使用 `variants` 键序的首个变体（control）并 `logger.warning`。
未配置实验 → `stats.experiment` 与 `stats.variant` 均为 `None`。

### 5.2 阶段 1：Gather（汇集）

产出 `List[ContextPacket]`，按以下顺序追加：

1. **系统指令**：`system_instructions` 非空时构造 `type=system_instruction`、`relevance_score=1.0` 的包。`token_count` 与该包本体经 `sha256(instructions)` 查缓存，命中则复用（P3）。
2. **记忆命中**：`memory_tool` 已注入时调用
   `memory_tool.run({"action": "recall", "query": user_query, "limit": config.memory_limit})`，
   解析 `ToolResponse.data["hits"]`。每个 hit 的 `created_at`（ISO 字符串）经 `datetime.fromisoformat` 还原（修复 B14 的时间退化）；`metadata.get("importance")` 低于 `config.min_importance` 的丢弃；`score`（缺失时按 0.0 处理）低于 `config.min_source_score` 的丢弃。产出 `type=memory` 的包。
3. **知识命中**：`rag_tool` 已注入时调用
   `rag_tool.run({"action": "query", "question": user_query, "top_k": config.rag_limit})`，
   解析 `ToolResponse.data["chunks"]`（须含 `score`，见 §8 的 B15 修正）。过滤规则同上。产出 `type=rag` 的包。
4. **对话历史**：取末尾 `config.history_window` 条 `Message`，正文格式化为 `"{msg.role}: {msg.content}"`。**`Message` 无 `timestamp` 字段**，故时间戳统一取构建时刻，并以 `metadata["position"]`（0 = 最旧，n-1 = 最新）承载新近性；阶段 2 对 `type=history` 的包按 `position` 线性映射到 `[0.5, 1.0]` 作为新近性分，不再读 `msg.timestamp`（修复 B14）。产出 `type=history` 的包。
5. **自定义包**：`additional_packets` 原样追加。`token_count == 0` 的由构建器计算并缓存；`relevance_score is None` 的留待阶段 2 计算。

工具注入与调用全部为可选：未注入工具则跳过对应来源，不抛异常。

### 5.3 阶段 2：Select（选择）

1. **预算计算**：`budget_policy.estimate(...)` 得 `complexity`，按 §4.2 公式得 `BudgetInfo`。打分包竞争 `BudgetInfo.available_tokens` 额度；系统指令已占用 `reserved_tokens` 额度，不参与本阶段预算（若系统指令实际占用超过 `reserved_tokens`，超出部分由阶段 4 压缩兜底）。
2. **打分**：对每个 `relevance_score is None` 的包调 `relevance_scorer.score_many` 计算相关性；新近性由 `_calculate_recency(timestamp)` 算出：`recency = clamp(0.1, 1.0, exp(-0.1 × age_hours / 24))`，`age_hours` 为信息距构建时刻的小时数（`type=history` 的包改用 `position` 线性映射到 `[0.5, 1.0]`，见 §5.2）。
3. **综合分**：`relevance_weight * relevance_score + recency_weight * recency`。
4. **相关性门槛**：`relevance_score < config.min_relevance` 的包丢弃并计入 `stats.dropped_by_relevance`。系统指令不参与评分与门槛。
5. **贪心填充**：按综合分降序逐个纳入，直到 `available_tokens` 耗尽。放不下的包**跳过继续**（修复 B9 的 `break`），以便后续更小的包仍能入选；最终未能入选的计入 `stats.dropped_by_budget`。

### 5.4 阶段 3：Structure（组织）

按 §4.6 路由规则把入选包分组，产出 `List[ContextSection]`。本阶段只组织不渲染，便于阶段 4 做结构感知压缩。

### 5.5 阶段 4：Compress（压缩）

仅当 `config.enable_compression` 为 `True` 且渲染后 token 数超过 `scaled_max_tokens` 时执行；`enable_compression=False` 时跳过本阶段并置 `stats.compressed = False`（修复 B11 中该项失效的问题）。

压缩策略（按段的优先级，非按出现顺序）：

1. **恒定段**：`Role & Policies`、`Task`、`Output` 优先全额保留。若三者合计已超预算，则仅对 `Role & Policies` 的 body 按 token 精确截断，`Task` 与 `Output` 不截断。
2. **弹性段**：按 `Evidence` → `Context` 的优先级贪心全额纳入。
3. **首个放不下的弹性段**：剩余 token > 50 时按 token 精确截断其 body 并追加 `[... 内容已压缩 ...]`；剩余 ≤ 50 时整段丢弃。
4. 其后的弹性段全部丢弃。

token 精确截断使用 tiktoken：`encoder.decode(encoder.encode(body)[:max_tokens])`（修复 B10 的字符比例猜测）。

最终由 `_render(sections) -> str` 拼装为 `"\n\n".join(f"[{title}]\n{body}" for title, body in sections)`。

### 5.6 统计与日志

`BuildStats` 在 `build_result()` 返回前构造完毕。日志经 `logging.getLogger(__name__)`（logger 名 `hello_agents.context`）输出：

| 级别 | 内容 | 条件 |
|---|---|---|
| `DEBUG` | 各阶段明细（候选数、逐包分数、预算数字、缓存命中） | 恒输出 |
| `INFO` | `stats.summary()` 单行摘要 | `config.log_stats` 为 `True` |
| `WARNING` | 检索失败、预算被系统指令占满、压缩触发、配置实验但缺 `session_id` | 恒输出 |

未配置 `logging` 时 Python 默认级别为 `WARNING`，故对现有调用方保持静默。`stats` 始终可经 `BuildResult` 程序化读取，不依赖日志配置。

---

## 6. 错误处理

| 情形 | 处理 | 是否向调用方抛出 |
|---|---|---|
| 配置参数非法（权重和 ≠ 1.0、比例越界、负数 limit 等） | 抛 `ConfigError` | 是（`__init__` / `build_result` 入口） |
| 实验变体覆盖了白名单外的字段名 | 抛 `ConfigError` | 是（`ExperimentAssigner.apply`） |
| 实验变体覆盖后配置非法（如权重和被改坏） | 抛 `ConfigError` | 是 |
| 工具调用抛异常，或返回 `ToolResponse.status == ERROR` | `logger.warning` + 跳过该来源，其余来源照常 | 否 |
| 某来源解析结果为空 | 计入 `candidates_by_source` 的 0，继续 | 否 |
| 系统指令实际占用超过 `scaled_max_tokens` | `logger.warning`，跳过打分选择、只保留恒定段 | 否 |
| 预算过小导致所有打分包被丢弃 | 正常返回仅含恒定段的上下文，`stats.dropped_by_budget` 反映 | 否 |
| tiktoken 编码器不可用 | 抛 `ConfigError`（`cl100k_base` 为 tiktoken 内置编码，正常安装下必可用） | 是（`__init__`） |

原则：**除配置错误外，`build()` / `build_result()` 不向调用方抛出异常**，检索类故障一律降级并记录。

---

## 7. 测试与验收标准

### 7.1 测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/test_context_cache.py` | 命中/未命中统计、TTL 过期、LRU 淘汰顺序、`max_size` 边界、`clear` |
| `tests/test_context_budget.py` | 四项启发式因子各自的影响、`complexity` 边界、缩放公式、`reserve_ratio` 落入 `reserved_tokens`、自定义 `BudgetPolicy` 注入 |
| `tests/test_context_scoring.py` | Jaccard 正确性、空查询、`score_many` 与 `score` 一致、`TFIDFEmbedding` 向量打分单调性、缓存二次调用不重复嵌入、`create_relevance_scorer` 三种 kind |
| `tests/test_context_experiment.py` | 同 `unit_id` 分流稳定、不同 `unit_id` 分布接近 `weights`、覆盖字段写入配置副本、白名单外字段抛 `ConfigError`、覆盖后权重和校验生效 |
| `tests/test_context_builder.py` | 四阶段全链路、`BuildStats` 各字段合理性、`build()` 返回 `str`、§7.3 全部回归点 |

### 7.2 测试约定

- 沿用 `tests/conftest.py` 的 `embedding`（`TFIDFEmbedding(dim=64)`）、`config`、`manager` 夹具；`test_context_builder.py` 另建 `MemoryTool(manager=manager)` / `RAGTool` 夹具。
- **全部离线零 API key**：打分用 `KeywordOverlapScorer` 或 `TFIDFEmbedding`。
- 中文模块 docstring 与中文用例名，纯 `assert`，flat `tests/test_*.py`。
- 异步用例显式 `@pytest.mark.asyncio`（与既有测试一致）。

### 7.3 缺陷回归点

每条缺陷至少一条断言：

| # | 断言 |
|---|---|
| B1 | `_gather` 返回非空 `list[ContextPacket]` |
| B2 | 不注入工具时构建成功且对应来源候选数为 0；注入后候选数 > 0 |
| B3 | 解析方法存在且产出带 `score` 的包 |
| B4–B7 | `build()` 与 `build_result()` 全链路无 `TypeError`，返回合法结果 |
| B8 | 预置 `relevance_score=0.5` 的包**不被重算**；`relevance_score=None` 的包被重算 |
| B9 | 构造「高分大包放不下、低分小包放得下」的场景，小包仍入选 |
| B10 | `_count_tokens` 与截断共用 tiktoken 口径；截断后 token 数 ≤ 指定上限 |
| B11 | `reserve_ratio=0.3` 时 `reserved_tokens == int(scaled * 0.3)`；`enable_compression=False` 时 `stats.compressed is False` |
| B12 | 非法 `ContextConfig` 抛 `ConfigError` 而非 `AssertionError` |
| B13 | 工具收到的 payload 为 `action="recall"` / `action="query"`，且无 `min_importance` / `min_score` 这类记忆层不识别的参数 |
| B14 | `type=history` 的包新近性随 `position` 递增；带 `created_at` 的记忆命中新近性随时间衰减 |
| B15 | RAG 命中包携带 `score` 且参与排序 |

### 7.4 验收标准

1. `uv run pytest tests/ -q` 全绿，**既有 10 个 memory / tools 测试不得回归**。
2. `uv run ruff check hello_agents tests examples` 无告警；`uv run ruff format --check hello_agents tests examples` 通过。
3. 无 `DASHSCOPE_API_KEY` 时 `uv run python examples/context_builder_demo.py` 可完整跑通并打印统计摘要。
4. `builder.build(q, history, sys)` 返回 `str` 且含 `[Task]` 段。
5. `builder.build_result(q, history, sys).stats` 满足：`candidates_total > 0`、`0 < token_utilization <= 1.0`、`budget.available_tokens == budget.scaled_max_tokens - budget.reserved_tokens`。
6. 同一 `session_id` 连续两次 `ExperimentAssigner.assign()` 结果相同。
7. 缓存二次构建 `cache_hits > 0`。

---

## 8. 交付物清单

| 文件 | 动作 |
|---|---|
| `hello_agents/context/cache.py` | 新建 |
| `hello_agents/context/budget.py` | 新建 |
| `hello_agents/context/scoring.py` | 新建 |
| `hello_agents/context/experiment.py` | 新建 |
| `hello_agents/context/base.py` | 重写（修复 B1–B14，接入 P1–P5） |
| `hello_agents/context/__init__.py` | 重写（中文 docstring +「典型用法：」+ 按字母序 `__all__`） |
| `hello_agents/tools/builtin/rag_tool.py` | 1 处：`data["chunks"]` 改为 `chunk.to_dict() \| {"score": chunk.score}`（修复 B15） |
| `hello_agents/memory/__init__.py` | 1 行：`__all__` 补导出 `cosine_similarity` |
| `hello_agents/core/__init__.py` | 1 行：`__all__` 补导出 `ConfigError` |
| `tests/test_context_cache.py` | 新建 |
| `tests/test_context_budget.py` | 新建 |
| `tests/test_context_scoring.py` | 新建 |
| `tests/test_context_experiment.py` | 新建 |
| `tests/test_context_builder.py` | 新建 |
| `examples/context_builder_demo.py` | 新建（新建 `examples/` 目录） |
| `README.md` | 补写（当前为空文件） |
| `docs/2026-09-23-context-management-design.md` | 本文件 |

**示例脚本覆盖点**（须离线可跑）：动态预算（简单 / 复杂查询得到不同 `BudgetInfo`）、两种打分器对比、缓存二次构建命中率、`BuildStats.summary()` 输出、A/B 稳定分流（同一 `session_id` 复跑结果一致）。

**README 覆盖点**：项目简介、安装、`ContextBuilder` 用法（配置项表格、`build` 与 `build_result` 的差异、注入 `MemoryTool` / `RAGTool`、自定义 `RelevanceScorer` / `BudgetPolicy`、A/B 配置片段），其余模块各留一段指引。文档语言与代码 docstring 一致，使用中文。

---

## 9. 实施顺序

1. `cache.py`（无依赖）
2. `budget.py`
3. `scoring.py`
4. `experiment.py`
5. `base.py` 重写（接入 1–4，修复 B1–B14）
6. 跨模块 3 处微调（`rag_tool.py` 补 `score`、`memory/__init__.py` 与 `core/__init__.py` 补导出）
7. `context/__init__.py` 导出
8. 5 个测试文件
9. `examples/context_builder_demo.py`
10. `README.md`
11. `ruff format` + `ruff check` + `pytest`
