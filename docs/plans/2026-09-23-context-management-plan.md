# Agent 上下文管理优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `hello_agents/context/` 重构为可运行、可观测、可实验的上下文构建器，落实五条生产化最佳实践并修复 15 项既有缺陷。

**Architecture:** 按横切能力拆为五个模块 —— `cache.py`（TTL+LRU 缓存）、`budget.py`（动态 token 预算）、`scoring.py`（相关性打分）、`experiment.py`（轻量 A/B）、`base.py`（编排器与数据类型）。`base.ContextBuilder` 依次执行「实验分流 → 汇集 → 选择 → 组织 → 压缩」五步，全程记录 `BuildStats` 并经 `logging` 输出。公开 API 向后兼容：`build()` 仍返回 `str`，新增 `build_result()` 返回 `BuildResult(context, stats)`。

**Tech Stack:** Python 3.13、tiktoken（token 计数）、dataclasses、`typing.Protocol`、`logging`、pytest、ruff。复用 `hello_agents/memory/embedding.py` 的向量能力，不新增依赖。

**Spec:** `docs/specs/2026-09-23-context-management-design.md`

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `hello_agents/context/cache.py` | 通用 TTL + LRU 缓存，带命中统计 | 新建 |
| `hello_agents/context/budget.py` | 复杂度估计与预算缩放 | 新建 |
| `hello_agents/context/scoring.py` | 相关性打分（关键词 / 向量） | 新建 |
| `hello_agents/context/experiment.py` | 实验声明、稳定分流、配置覆盖 | 新建 |
| `hello_agents/context/base.py` | 共享数据类型与来源/模板常量 | 重写 |
| `hello_agents/context/builder.py` | `ContextBuilder` 五阶段编排 | 新建（Task 7–11） |
| `hello_agents/context/__init__.py` | 公开导出 | 重写 |
| `hello_agents/core/__init__.py` | 补导出 `ConfigError` | 改 1 行 |
| `hello_agents/memory/__init__.py` | 补导出 `cosine_similarity` | 改 1 行 |
| `hello_agents/tools/builtin/rag_tool.py` | chunk 补 `score` | 改 1 行 |
| `tests/test_context_cache.py` | 缓存测试 | 新建 |
| `tests/test_context_budget.py` | 预算测试 | 新建 |
| `tests/test_context_scoring.py` | 打分测试 | 新建 |
| `tests/test_context_experiment.py` | 实验测试 | 新建 |
| `tests/test_context_builder.py` | 全链路 + 缺陷回归 | 新建 |
| `examples/context_builder_demo.py` | 离线可跑示例 | 新建 |
| `README.md` | 项目与模块文档 | 补写 |

**依赖方向（无环）**：`builder` → `base` / `budget` / `scoring` / `cache` / `experiment`；`base` → `budget`（只用 `BudgetInfo` / `BudgetPolicy` 作注解）；`scoring` → `memory.embedding`。`experiment` 只在 `TYPE_CHECKING` 下引用 `base.ContextConfig`，运行时无环。

**约定**：中文 docstring、PEP 604 类型注解（`str | None`）、**泛型一律用 PEP 695 语法（`class Foo[T]:`，不用 `Generic[T]`）**、`@dataclass`（仅 `core/` 用 pydantic）、ruff（行宽 88；本环境 `requires-python = ">=3.13"` 推出 `target-version = py313`，有效规则集含 `DTZ` / `BLE` / `I` / `F` / `E`（已实测复现））。每个任务结束提交一次，并在收尾时对**本任务触碰的文件**跑 `uv run ruff check` 清零告警。

**校验用例的变异钉扎**：当多条校验共享一个约束（如 `recency_weight + relevance_weight == 1.0` 会让两个权重同时越界），裸 `pytest.raises(ConfigError)` 钉不住任一条分支 —— 删掉一条检查，另一条照样抛同类型异常。此时必须对**每条分支各写一条 `match=` 断言**，且 `match` 模式要避开相邻校验消息的公共子串（例如用 `"必须在"` 后缀而非裸字段名，否则会被和校验的 `"…必须等于 1.0"` 骗过）。写校验用例一律先做变异验证，报告「真实值 vs 变异后值」。

---

## Task 1: TTLCache

**Files:**
- Create: `hello_agents/context/cache.py`
- Test: `tests/test_context_cache.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_context_cache.py`：

```python
"""TTLCache 测试：命中统计 / TTL 过期 / LRU 淘汰"""

import time

import pytest

from hello_agents.context.cache import TTLCache


def test_get_put_roundtrip_and_stats():
    cache = TTLCache(max_size=4, ttl_seconds=60)
    assert cache.get("missing") is None
    cache.put("k", "v")
    assert cache.get("k") == "v"
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["size"] == 1


def test_entry_expires_after_ttl():
    cache = TTLCache(max_size=4, ttl_seconds=0.05)
    cache.put("k", "v")
    time.sleep(0.08)
    assert cache.get("k") is None
    assert cache.stats()["size"] == 0


def test_lru_eviction_order():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # a 成为最近使用项
    cache.put("c", 3)  # 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.stats()["evictions"] == 1


def test_put_purges_expired_before_evicting():
    cache = TTLCache(max_size=2, ttl_seconds=0.05)
    cache.put("a", 1)
    time.sleep(0.08)
    cache.put("b", 2)  # "a" 已过期，应被清理而非计入淘汰
    assert cache.stats()["size"] == 1
    assert cache.stats()["evictions"] == 0


def test_put_overwrites_without_growing():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.put("a", 2)
    assert cache.stats()["size"] == 1
    assert cache.get("a") == 2


def test_clear_resets_entries_and_counters():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.get("a")
    cache.clear()
    assert cache.stats() == {"hits": 0, "misses": 0, "size": 0, "evictions": 0}


def test_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        TTLCache(max_size=0)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hello_agents.context.cache'`

- [ ] **Step 3: Write minimal implementation**

创建 `hello_agents/context/cache.py`：

```python
"""TTL + LRU 缓存

为上下文构建提供带过期与容量控制的键值缓存，并记录命中统计。

典型用法：
    from hello_agents.context import TTLCache

    cache = TTLCache(max_size=128, ttl_seconds=600)
    cache.put("k", "v")
    cache.get("k")   # -> "v"
    cache.stats()    # -> {"hits": 1, "misses": 0, "size": 1, "evictions": 0}
"""

from collections import OrderedDict
from time import monotonic

__all__ = ["TTLCache"]


class TTLCache[K, V]:
    """带 TTL 与 LRU 淘汰的缓存

    统计口径：evictions 只统计容量驱动的淘汰，TTL 过期不增加任何计数；
    stats() 的 size 可能包含已过期但尚未回收的条目——过期项仅在访问或
    下一次写入新键时才被清理。

    Attributes:
        max_size: 最大条目数
        ttl_seconds: 条目存活秒数
    """

    def __init__(self, max_size: int = 256, ttl_seconds: float = 3600.0) -> None:
        if max_size <= 0:
            raise ValueError("max_size 必须为正整数")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须为正数")
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _is_expired(self, created_at: float, now: float) -> bool:
        return now - created_at > self.ttl_seconds

    def _purge_expired(self, now: float) -> None:
        stale = [
            key
            for key, (created_at, _) in self._data.items()
            if self._is_expired(created_at, now)
        ]
        for key in stale:
            del self._data[key]

    def get(self, key: K) -> V | None:
        """读取缓存，未命中或已过期返回 None"""
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        created_at, value = entry
        if self._is_expired(created_at, monotonic()):
            del self._data[key]
            self._misses += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return value

    def put(self, key: K, value: V) -> None:
        """写入缓存，超容量时先清过期项、再按 LRU 淘汰"""
        now = monotonic()
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = (now, value)
            return
        self._purge_expired(now)
        while len(self._data) >= self.max_size:
            self._data.popitem(last=False)
            self._evictions += 1
        self._data[key] = (now, value)

    def clear(self) -> None:
        """清空所有条目与统计计数"""
        self._data.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict[str, int]:
        """返回 {"hits", "misses", "size", "evictions"}"""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._data),
            "evictions": self._evictions,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_cache.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/cache.py tests/test_context_cache.py
git commit -m "feat: add TTLCache with TTL expiry and LRU eviction"
```

---

## Task 2: 动态 token 预算

**Files:**
- Create: `hello_agents/context/budget.py`
- Test: `tests/test_context_budget.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_context_budget.py`：

```python
"""预算策略测试：复杂度启发式与缩放公式"""

import pytest

from hello_agents.context.budget import BudgetInfo, HeuristicBudgetPolicy
from hello_agents.core import Message, MessageRole


def _policy() -> HeuristicBudgetPolicy:
    return HeuristicBudgetPolicy()


def test_short_plain_query_has_low_complexity():
    complexity = _policy().estimate("你好", history=[], system_instructions=None)
    assert 0.0 <= complexity < 0.2


def test_long_query_raises_complexity():
    short = _policy().estimate("你好", history=[], system_instructions=None)
    long = _policy().estimate("请" * 200, history=[], system_instructions=None)
    assert long > short
    assert long >= 0.4


def test_interrogative_marker_raises_complexity():
    # 等长对照，仅疑问词不同
    plain = _policy().estimate("配置配置向量库", history=[], system_instructions=None)
    asking = _policy().estimate("如何配置向量库", history=[], system_instructions=None)
    assert asking - plain == pytest.approx(0.20)


def test_english_question_word_matches_as_whole_word():
    """`how` 作为整词命中，且不应在 `show` 中误命中"""
    matched = _policy().estimate("how", history=[], system_instructions=None)
    control = _policy().estimate("sho", history=[], system_instructions=None)
    assert matched - control == pytest.approx(0.20)

    # 等长对照：`show` 含 `how` 子串，但不应命中
    embedded = _policy().estimate("show", history=[], system_instructions=None)
    embedded_control = _policy().estimate("shou", history=[], system_instructions=None)
    assert embedded - embedded_control == pytest.approx(0.0)


def test_history_size_raises_complexity():
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(10)]
    empty = _policy().estimate("配置向量库", history=[], system_instructions=None)
    full = _policy().estimate("配置向量库", history=history, system_instructions=None)
    assert full > empty


def test_retrieval_cue_raises_complexity():
    # 等长对照，仅线索词不同
    plain = _policy().estimate(
        "配置配置配置向量库", history=[], system_instructions=None
    )
    cued = _policy().estimate(
        "根据文档配置向量库", history=[], system_instructions=None
    )
    assert cued - plain == pytest.approx(0.20)


def test_english_retrieval_cue_raises_complexity():
    # 等长对照：仅线索词不同，长度因子抵消
    plain = _policy().estimate(
        "zzzzzzzz the docs configure vector store", history=[], system_instructions=None
    )
    cued = _policy().estimate(
        "based on the docs configure vector store", history=[], system_instructions=None
    )
    assert cued - plain == pytest.approx(0.20)


def test_policy_exposes_stable_name():
    assert _policy().name == "heuristic"


def test_complexity_is_clamped_to_unit_interval():
    huge = _policy().estimate("如何" + "请" * 500, history=[], system_instructions=None)
    assert 0.0 <= huge <= 1.0


def test_budget_info_fields_are_consistent():
    info = BudgetInfo(
        policy="heuristic",
        complexity=0.5,
        requested_max_tokens=3000,
        scaled_max_tokens=2250,
        reserved_tokens=450,
        available_tokens=1800,
    )
    assert info.available_tokens == info.scaled_max_tokens - info.reserved_tokens
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hello_agents.context.budget'`

- [ ] **Step 3: Write minimal implementation**

创建 `hello_agents/context/budget.py`：

```python
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

    不变式：``available_tokens == scaled_max_tokens - reserved_tokens``，
    由构建方（ContextBuilder）保证。
    """

    policy: str
    complexity: float
    requested_max_tokens: int
    scaled_max_tokens: int
    reserved_tokens: int
    available_tokens: int


class BudgetPolicy(Protocol):
    """复杂度估计策略

    实现应提供稳定的 ``name``，用于 ``BudgetInfo.policy`` 的 A/B 归因；
    缺失时调用方回退为类名。
    """

    name: str

    def estimate(
        self,
        query: str,
        *,
        history: list[Message],
        system_instructions: str | None,
    ) -> float:
        """返回 [0.0, 1.0] 的复杂度分数

        实现可以忽略 ``system_instructions``；调用方始终以关键字传入，
        签名不得收窄。
        """
        ...


class HeuristicBudgetPolicy:
    """零成本启发式复杂度估计

    四项因子加权求和，权重和为 1.0：查询长度 0.40、疑问词 0.20、
    历史规模 0.20、检索线索 0.20。

    查询长度以 200 字符、历史规模以 10 轮为饱和点（超出即取满该项），
    结果 clamp 到 [0.0, 1.0]。

    本实现忽略 ``system_instructions``，但为保持与 ``BudgetPolicy`` 的
    可替换性，签名不得收窄（调用方始终以关键字传入该参数）。
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_budget.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/budget.py tests/test_context_budget.py
git commit -m "feat: add complexity-aware token budget policy"
```

---

## Task 3: 补齐前置导出

`scoring.py` 需要 `cosine_similarity`，`base.py` 需要 `ConfigError`。两者都已存在，只是未从包级 `__all__` 导出。

**Files:**
- Modify: `hello_agents/core/__init__.py`
- Modify: `hello_agents/memory/__init__.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_scoring.py` 中先只写这一条（后续 Task 5 会补全整个文件）：

```python
"""打分器测试：关键词重叠 / 向量相似度 / 缓存"""

from hello_agents.memory import cosine_similarity


def test_cosine_similarity_is_exported_from_memory_package():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_scoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'cosine_similarity' from 'hello_agents.memory'`

- [ ] **Step 3: Write minimal implementation**

在 `hello_agents/core/__init__.py` 中，把 `ConfigError` 加入导入与 `__all__`：

```python
from .agent import Agent
from .config import Config
from .exceptions import AgentError, ConfigError, LLMError, ToolError
from .llm import HelloAgentsLLM
from .message import Message, MessageRole

__all__ = [
    "Agent",
    "AgentError",
    "Config",
    "ConfigError",
    "HelloAgentsLLM",
    "LLMError",
    "Message",
    "MessageRole",
    "ToolError",
]
```

在 `hello_agents/memory/__init__.py` 中，把 `cosine_similarity` 加入从 `.embedding` 的导入与 `__all__`（保持该文件既有结构，仅新增这一个名字，`__all__` 按字母序插入）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_scoring.py -v`
Expected: PASS — 1 passed

- [ ] **Step 5: Run the existing suite to confirm no regression**

Run: `uv run pytest tests/ -q`
Expected: PASS — 既有 10 个测试文件全绿

- [ ] **Step 6: Commit**

```bash
git add hello_agents/core/__init__.py hello_agents/memory/__init__.py tests/test_context_scoring.py
git commit -m "feat: export ConfigError and cosine_similarity from package roots"
```

---

## Task 4: 核心数据类型

`base.py` 当前完全不可运行（缺 `return`、未定义属性、四阶段调用签名全错）。本任务先只落地数据类型并加校验，为后续任务提供稳定契约。

**Files:**
- Rewrite: `hello_agents/context/base.py`（本任务只写数据类型，`ContextBuilder` 留到 Task 7）
- Modify: `hello_agents/context/__init__.py`（**执行期发现的计划缺陷**，见下）
- Test: `tests/test_context_builder.py`

> **执行期修订**：本任务把 `ContextBuilder` 从 `base.py` 移除，但
> `context/__init__.py` 仍写着 `from .base import ContextBuilder, ...`。导入
> `hello_agents.context.base` 会先执行包 `__init__`，因此**整个测试套件会在
> collection 阶段报 `ImportError`**，Task 5–12 每步都要跑 `pytest`，全都会撞上。
> 计划原先把 `__init__.py` 的重写放在 Task 13，太晚。此处先做最小替换：
> 从 import 与 `__all__` 中摘掉 `ContextBuilder`（Task 13 会整体重写该文件）。
> 计划原文的 Step 4「Expected: PASS — 12 passed」在不改该文件时无法达成。

- [ ] **Step 1: Write the failing test**

创建 `tests/test_context_builder.py`：

```python
"""ContextBuilder 测试：数据类型 / 四阶段全链路 / 缺陷回归"""

from datetime import UTC, datetime

import pytest

from hello_agents.context.base import (
    BuildResult,
    BuildStats,
    ContextConfig,
    ContextPacket,
    ContextSection,
)
from hello_agents.context.budget import BudgetInfo
from hello_agents.core import ConfigError


def test_packet_clamps_relevance_score():
    packet = ContextPacket(
        content="x", timestamp=datetime.now(tz=UTC), relevance_score=1.7
    )
    assert packet.relevance_score == 1.0


def test_packet_keeps_none_relevance_score():
    """修复 B8：None 表示待计算，不再用 0.5 哨兵"""
    packet = ContextPacket(content="x", timestamp=datetime.now(tz=UTC))
    assert packet.relevance_score is None
    assert packet.token_count == 0
    assert packet.metadata == {}


def test_packet_keeps_explicit_half_score():
    packet = ContextPacket(
        content="x", timestamp=datetime.now(tz=UTC), relevance_score=0.5
    )
    assert packet.relevance_score == 0.5


def test_config_defaults():
    config = ContextConfig()
    assert config.max_tokens == 3000
    assert config.reserve_ratio == 0.2
    assert config.min_budget_ratio == 0.5
    assert config.max_budget_ratio == 1.0
    assert config.memory_limit == 10
    assert config.rag_limit == 5
    assert config.history_window == 5
    assert config.cache_max_size == 256
    assert config.log_stats is True
    assert config.experiment is None


def test_config_rejects_bad_weights():
    """修复 B12：抛 ConfigError 而非 AssertionError"""
    with pytest.raises(ConfigError):
        ContextConfig(relevance_weight=0.5, recency_weight=0.5 + 0.01)


def test_config_rejects_out_of_range_ratio():
    with pytest.raises(ConfigError):
        ContextConfig(reserve_ratio=1.5)


def test_config_rejects_negative_limits():
    with pytest.raises(ConfigError):
        ContextConfig(history_window=-1)


def test_config_rejects_inverted_budget_ratios():
    with pytest.raises(ConfigError):
        ContextConfig(min_budget_ratio=0.9, max_budget_ratio=0.5)


def test_config_rejects_non_positive_max_tokens():
    with pytest.raises(ConfigError):
        ContextConfig(max_tokens=0)


def test_section_holds_title_and_body():
    section = ContextSection(title="Task", body="做什么")
    assert section.title == "Task"
    assert section.body == "做什么"


def test_stats_to_dict_is_serializable():
    stats = BuildStats(
        candidates_total=3,
        candidates_by_source={"system_instruction": 1, "memory": 2},
        selected_total=2,
        selected_by_source={"system_instruction": 1, "memory": 1},
        dropped_by_relevance=1,
        dropped_by_budget=0,
        structured_tokens=100,
        final_tokens=90,
        selected_tokens=60,
        budget=BudgetInfo(
            policy="heuristic",
            complexity=0.4,
            requested_max_tokens=3000,
            scaled_max_tokens=2100,
            reserved_tokens=420,
            available_tokens=1680,
        ),
        token_utilization=0.0429,
        compressed=True,
        compression_ratio=0.9,
        cache_hits=1,
        cache_misses=2,
        duration_ms=1.23,
        experiment="scoring_v1",
        variant="control",
    )
    payload = stats.to_dict()
    assert payload["budget"]["scaled_max_tokens"] == 2100
    assert payload["experiment"] == "scoring_v1"
    assert "candidates=3" in stats.summary()
    assert "scoring_v1/control" in stats.summary()


def test_build_result_bundles_context_and_stats():
    result = BuildResult(context="ctx", stats=None)
    assert result.context == "ctx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `ImportError: cannot import name 'BuildResult' from 'hello_agents.context.base'`

- [ ] **Step 3: Write minimal implementation**

用以下内容重写 `hello_agents/context/base.py`（本任务只到数据类型为止；`ContextBuilder` 由 Task 7–11 在新建的 `builder.py` 中实现）：

```python
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
            raise ConfigError("需满足 0.0 <= min_budget_ratio <= max_budget_ratio <= 1.0")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/base.py hello_agents/context/__init__.py tests/test_context_builder.py
git commit -m "feat: add context data types with ConfigError validation"
```

---

## Task 5: 相关性打分器

**Files:**
- Create: `hello_agents/context/scoring.py`
- Modify: `tests/test_context_scoring.py`（Task 3 已建，此处补全）

- [ ] **Step 1: Write the failing test**

把 `tests/test_context_scoring.py` 补全为：

```python
"""打分器测试：关键词重叠 / 向量相似度 / 缓存"""

import pytest

from hello_agents.context.cache import TTLCache
from hello_agents.context.scoring import (
    EmbeddingSimilarityScorer,
    KeywordOverlapScorer,
    create_relevance_scorer,
)
from hello_agents.core import ConfigError
from hello_agents.memory import TFIDFEmbedding, cosine_similarity


class _CountingEmbedding(TFIDFEmbedding):
    """记录 embed_texts 调用次数，用于验证批量与缓存行为"""

    def __init__(self, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self.calls = 0
        self.texts_seen = 0

    def embed_texts(self, texts):
        self.calls += 1
        self.texts_seen += len(texts)
        return super().embed_texts(texts)


def test_cosine_similarity_is_exported_from_memory_package():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_keyword_scorer_matches_identical_text():
    scorer = KeywordOverlapScorer()
    assert scorer.score("用户喜欢爬山", "用户喜欢爬山") == pytest.approx(1.0)


def test_keyword_scorer_returns_zero_for_empty_query():
    scorer = KeywordOverlapScorer()
    assert scorer.score("任意内容", "") == 0.0


def test_keyword_scorer_scores_english_overlap():
    scorer = KeywordOverlapScorer()
    related = scorer.score("vector store configuration", "vector store")
    unrelated = scorer.score("disk usage alert", "vector store")
    assert related > unrelated
    assert 0.0 <= related <= 1.0


def test_keyword_scorer_tokenizes_cjk_characters():
    """中文无空格，按单字切分才能产生重叠"""
    scorer = KeywordOverlapScorer()
    assert scorer.score("用户偏好深蓝色", "用户喜欢什么颜色") > 0.0


def test_score_many_matches_individual_scores():
    scorer = KeywordOverlapScorer()
    contents = ["用户喜欢爬山", "磁盘告警", ""]
    assert scorer.score_many(contents, "用户喜欢") == [
        scorer.score(content, "用户喜欢") for content in contents
    ]


def test_embedding_scorer_batches_into_single_call():
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    scorer.score_many(["用户喜欢爬山", "磁盘告警", "网络延迟"], "用户喜欢什么")
    assert embedding.calls == 1
    assert embedding.texts_seen == 4  # 1 条查询 + 3 条内容


def test_embedding_scorer_uses_cache_on_repeat():
    embedding = _CountingEmbedding()
    scorer = EmbeddingSimilarityScorer(embedding=embedding)
    scorer.score_many(["用户喜欢爬山"], "用户喜欢什么")
    scorer.score_many(["用户喜欢爬山"], "用户喜欢什么")
    assert embedding.calls == 1


def test_embedding_scorer_ranks_identical_text_highest():
    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
    query = "用户喜欢什么颜色"
    same = scorer.score("用户喜欢什么颜色", query)
    unrelated = scorer.score("服务器磁盘容量告警", query)
    assert same > unrelated


def test_embedding_scorer_returns_zero_for_empty_query():
    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
    assert scorer.score_many(["内容"], "") == [0.0]


def test_embedding_scorer_handles_empty_contents():
    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
    assert scorer.score_many([], "查询") == []


def test_embedding_scorer_accepts_external_cache():
    cache: TTLCache[str, list[float]] = TTLCache(max_size=8, ttl_seconds=60)
    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64), cache=cache)
    scorer.score("用户喜欢爬山", "用户喜欢")
    assert cache.stats()["size"] > 0


def test_create_relevance_scorer_keyword():
    assert isinstance(create_relevance_scorer("keyword"), KeywordOverlapScorer)


def test_create_relevance_scorer_embedding():
    assert isinstance(create_relevance_scorer("embedding"), EmbeddingSimilarityScorer)


def test_create_relevance_scorer_auto_returns_usable_scorer():
    scorer = create_relevance_scorer("auto")
    assert 0.0 <= scorer.score("用户喜欢爬山", "用户喜欢") <= 1.0


def test_create_relevance_scorer_rejects_unknown_kind():
    with pytest.raises(ConfigError):
        create_relevance_scorer("bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hello_agents.context.scoring'`

- [ ] **Step 3: Write minimal implementation**

创建 `hello_agents/context/scoring.py`：

```python
"""相关性打分

把候选内容与用户查询的相关性量化为 [0,1]，支持关键词重叠与向量相似度两种实现。

典型用法：
    from hello_agents.context import create_relevance_scorer

    scorer = create_relevance_scorer("keyword")
    scorer.score("用户偏好深蓝色", "用户喜欢什么颜色")
"""

import hashlib
import logging
import re
from typing import Literal, Protocol

from ..core.exceptions import ConfigError
from ..memory.embedding import BaseEmbedding, cosine_similarity, create_embedding
from .cache import TTLCache

logger = logging.getLogger(__name__)

__all__ = [
    "EmbeddingSimilarityScorer",
    "KeywordOverlapScorer",
    "RelevanceScorer",
    "create_relevance_scorer",
]

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_CHAR_RE = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> set[str]:
    """切词：ASCII 按单词、CJK 按单字（中文无空格，按字切分才有重叠）"""
    tokens = set(_ASCII_WORD_RE.findall(text.lower()))
    tokens.update(_CJK_CHAR_RE.findall(text))
    return tokens


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RelevanceScorer(Protocol):
    """相关性打分策略，返回值恒在 [0.0, 1.0]"""

    def score(self, content: str, query: str) -> float: ...

    def score_many(self, contents: list[str], query: str) -> list[float]: ...


class KeywordOverlapScorer:
    """Jaccard 词重叠，零依赖"""

    name = "keyword"

    def score(self, content: str, query: str) -> float:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return 0.0
        content_tokens = _tokenize(content)
        union = content_tokens | query_tokens
        if not union:
            return 0.0
        return len(content_tokens & query_tokens) / len(union)

    def score_many(self, contents: list[str], query: str) -> list[float]:
        return [self.score(content, query) for content in contents]


class EmbeddingSimilarityScorer:
    """向量余弦相似度，复用 memory 的 embedding 后端"""

    name = "embedding"

    def __init__(
        self,
        embedding: BaseEmbedding | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.embedding = embedding if embedding is not None else create_embedding("auto")
        self.cache: TTLCache = cache if cache is not None else TTLCache()

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """批量取向量，命中缓存的文本不重复嵌入"""
        keys = [_cache_key(text) for text in texts]
        vectors: list[list[float] | None] = [self.cache.get(key) for key in keys]
        missing = [index for index, vector in enumerate(vectors) if vector is None]
        if missing:
            computed = self.embedding.embed_texts([texts[index] for index in missing])
            for index, vector in zip(missing, computed):
                vectors[index] = vector
                self.cache.put(keys[index], vector)
        return [vector for vector in vectors if vector is not None]

    def score(self, content: str, query: str) -> float:
        return self.score_many([content], query)[0]

    def score_many(self, contents: list[str], query: str) -> list[float]:
        if not contents:
            return []
        if not query:
            return [0.0] * len(contents)
        vectors = self._embed_many([query, *contents])
        query_vector = vectors[0]
        return [
            _clamp01(cosine_similarity(query_vector, vector)) for vector in vectors[1:]
        ]


def create_relevance_scorer(
    kind: Literal["keyword", "embedding", "auto"] = "keyword",
) -> RelevanceScorer:
    """按名称创建打分器

    Args:
        kind: "keyword" 关键词重叠；"embedding" 向量相似度；
            "auto" 优先向量、构造失败时降级关键词

    Returns:
        RelevanceScorer: 打分器实例
    """
    if kind == "keyword":
        return KeywordOverlapScorer()
    if kind == "embedding":
        return EmbeddingSimilarityScorer()
    if kind == "auto":
        try:
            return EmbeddingSimilarityScorer()
        except Exception as exc:  # noqa: BLE001 - 降级需吞掉任意后端构造错误
            logger.warning("向量打分器构造失败，降级为关键词重叠: %s", exc)
            return KeywordOverlapScorer()
    raise ConfigError(f"未知的打分器类型: {kind}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_scoring.py -v`
Expected: PASS — 16 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/scoring.py tests/test_context_scoring.py
git commit -m "feat: add pluggable relevance scorers with embedding cache"
```

---

## Task 6: 轻量 A/B 实验

**Files:**
- Create: `hello_agents/context/experiment.py`
- Modify: `hello_agents/context/base.py`（补 `experiment` 的运行时类型校验）
- Test: `tests/test_context_experiment.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_context_experiment.py`：

```python
"""实验分流测试：稳定分流 / 权重分布 / 配置覆盖与校验"""

import pytest

from hello_agents.context.base import ContextConfig
from hello_agents.context.experiment import ExperimentAssigner, ExperimentSpec
from hello_agents.core import ConfigError


def _spec(weights=None) -> ExperimentSpec:
    return ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
        weights=weights,
    )


def test_assignment_is_stable_for_same_unit():
    assigner = ExperimentAssigner()
    spec = _spec()
    first = assigner.assign(spec, "session-42")
    for _ in range(5):
        assert assigner.assign(spec, "session-42") == first


def test_assignment_covers_all_variants():
    assigner = ExperimentAssigner()
    spec = _spec()
    seen = {assigner.assign(spec, f"session-{i}") for i in range(200)}
    assert seen == {"control", "variant_a"}


def test_assignment_distribution_follows_weights():
    assigner = ExperimentAssigner()
    spec = _spec(weights={"control": 3.0, "variant_a": 1.0})
    total = 2000
    controls = sum(
        1 for i in range(total) if assigner.assign(spec, f"u{i}") == "control"
    )
    assert 0.70 < controls / total < 0.80


def test_seed_changes_assignment():
    spec = _spec()
    a = ExperimentAssigner(seed="seed-a").assign(spec, "session-1")
    b = ExperimentAssigner(seed="seed-b").assign(spec, "session-1")
    assert isinstance(a, str) and isinstance(b, str)


def test_single_variant_spec_always_returns_it():
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(name="only", variants={"control": {}})
    assert assigner.assign(spec, "anything") == "control"


def test_apply_returns_overridden_copy():
    assigner = ExperimentAssigner()
    spec = _spec()
    config = ContextConfig()
    updated, variant = assigner.apply(config, spec, "session-42")
    overrides = spec.variants[variant]
    assert updated.relevance_weight == overrides["relevance_weight"]
    assert updated.recency_weight == overrides["recency_weight"]
    assert updated.max_tokens == config.max_tokens  # 未覆盖字段保持原值


def test_apply_does_not_mutate_original_config():
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(name="budget", variants={"control": {"max_tokens": 1000}})
    config = ContextConfig()
    assigner.apply(config, spec, "session-1")
    assert config.max_tokens == 3000


def test_apply_reruns_config_validation():
    """覆盖后权重和不为 1.0 时必须抛 ConfigError"""
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(
        name="broken", variants={"control": {"relevance_weight": 0.9}}
    )
    with pytest.raises(ConfigError):
        assigner.apply(ContextConfig(), spec, "session-1")


def test_spec_rejects_unknown_override_field():
    with pytest.raises(ConfigError):
        ExperimentSpec(name="bad", variants={"control": {"not_a_field": 1}})


def test_spec_rejects_empty_variants():
    with pytest.raises(ConfigError):
        ExperimentSpec(name="empty", variants={})


def test_spec_rejects_weights_missing_a_variant():
    with pytest.raises(ConfigError):
        ExperimentSpec(
            name="partial",
            variants={"control": {}, "variant_a": {}},
            weights={"control": 1.0},
        )


def test_config_rejects_non_spec_experiment():
    """experiment 必须是 ExperimentSpec 实例，None 表示不做实验"""
    with pytest.raises(ConfigError):
        ContextConfig(experiment="not-a-spec")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_experiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hello_agents.context.experiment'`

- [ ] **Step 3: Write minimal implementation**

创建 `hello_agents/context/experiment.py`：

```python
"""轻量 A/B 实验

按 unit_id 稳定分流到实验变体，并把变体的参数覆盖应用到 ContextConfig 副本上。

典型用法：
    from hello_agents.context import ExperimentAssigner, ExperimentSpec

    spec = ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
    )
    assigner = ExperimentAssigner()
    config, variant = assigner.apply(config, spec, session_id)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..core.exceptions import ConfigError

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，避免与 base 形成导入环
    from .base import ContextConfig

__all__ = ["ExperimentAssigner", "ExperimentSpec"]

_OVERRIDABLE_FIELDS = frozenset(
    {
        "recency_weight",
        "relevance_weight",
        "min_relevance",
        "max_tokens",
        "reserve_ratio",
        "min_budget_ratio",
        "max_budget_ratio",
        "history_window",
        "memory_limit",
        "rag_limit",
        "enable_compression",
        "log_stats",
    }
)


@dataclass
class ExperimentSpec:
    """实验声明

    Attributes:
        name: 实验名，参与分流哈希
        variants: 变体名 -> ContextConfig 字段覆盖
        weights: 变体权重，缺省各变体均匀
    """

    name: str
    variants: dict[str, dict[str, Any]]
    weights: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("ExperimentSpec.name 不能为空")
        if not self.variants:
            raise ConfigError("ExperimentSpec.variants 不能为空")
        unknown = {
            key for overrides in self.variants.values() for key in overrides
        } - _OVERRIDABLE_FIELDS
        if unknown:
            raise ConfigError(f"实验变体包含不可覆盖的字段: {sorted(unknown)}")
        if self.weights is not None:
            missing = set(self.variants) - set(self.weights)
            if missing:
                raise ConfigError(f"weights 缺少变体: {sorted(missing)}")


class ExperimentAssigner:
    """按 unit_id 稳定分流的实验分流器"""

    def __init__(self, seed: str = "hello-agents") -> None:
        self.seed = seed

    def assign(self, spec: ExperimentSpec, unit_id: str) -> str:
        """返回 unit_id 所属的变体名，同一 unit_id 恒定落在同一变体"""
        names = list(spec.variants)
        if len(names) == 1:
            return names[0]
        weights = self._normalized_weights(spec)
        digest = hashlib.sha256(
            f"{self.seed}:{spec.name}:{unit_id}".encode()
        ).digest()
        bucket = int.from_bytes(digest[:8], "big") / 2**64
        cumulative = 0.0
        for name in names:
            cumulative += weights[name]
            if bucket < cumulative:
                return name
        return names[-1]

    def apply(
        self,
        config: ContextConfig,
        spec: ExperimentSpec,
        unit_id: str,
    ) -> tuple[ContextConfig, str]:
        """返回 (应用覆盖后的配置副本, 变体名)

        `dataclasses.replace` 会重新调用 `__post_init__`，因此覆盖后的配置会
        重新走一遍全部校验。
        """
        variant = self.assign(spec, unit_id)
        try:
            updated = replace(config, **spec.variants[variant])
        except TypeError as exc:
            raise ConfigError(f"实验变体 {variant} 的字段覆盖非法: {exc}") from exc
        return updated, variant

    def _normalized_weights(self, spec: ExperimentSpec) -> dict[str, float]:
        names = list(spec.variants)
        raw = spec.weights or {name: 1.0 for name in names}
        total = sum(raw[name] for name in names)
        if total <= 0:
            raise ConfigError("实验权重之和必须为正数")
        return {name: raw[name] / total for name in names}
```

`experiment.py` 写好之后，回到 `hello_agents/context/base.py` 补上 `experiment` 的运行时类型校验（Task 4 时 `experiment.py` 尚不存在，只能用 `TYPE_CHECKING` 导入，运行时无从校验）。

在 `ContextConfig.__post_init__` 的末尾追加：

```python
        if self.experiment is not None:
            from .experiment import ExperimentSpec  # 局部导入，避免与 experiment 形成模块级循环

            if not isinstance(self.experiment, ExperimentSpec):
                raise ConfigError("experiment 必须是 ExperimentSpec 实例")
```

局部导入是有意的：`experiment.py` 只在 `TYPE_CHECKING` 下引用 `base.ContextConfig`，运行时无环；但保持 `base.py` 的模块级导入仍写在 `TYPE_CHECKING` 下，可以把改动面控制到最小。`base.py` 原有的 `if TYPE_CHECKING: from .experiment import ExperimentSpec` 块**保持不变**（它供 `experiment: ExperimentSpec | None` 注解使用）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_experiment.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/experiment.py hello_agents/context/base.py tests/test_context_experiment.py
git commit -m "feat: add lightweight A/B experiment assigner with config validation"
```

---

## Task 7: ContextBuilder 骨架、token 计数与预算计算

**Files:**
- Create: `hello_agents/context/builder.py`
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 7: 骨架 / token 计数 / 预算 ---

from hello_agents.context.builder import ContextBuilder, _count_by_source, _parse_timestamp
from hello_agents.context.budget import HeuristicBudgetPolicy


class _FixedPolicy:
    """恒定返回指定复杂度的策略替身"""

    name = "fixed"

    def __init__(self, complexity: float) -> None:
        self.complexity = complexity

    def estimate(self, query, *, history, system_instructions) -> float:
        return self.complexity


def test_count_tokens_uses_tiktoken():
    """修复 B10：统一走 tiktoken，不再用中英文字符启发式"""
    builder = ContextBuilder()
    text = "用户喜欢深蓝色 hello world"
    assert builder._count_tokens(text) == len(builder.encoder.encode(text))
    assert builder._count_tokens("") == 0


def test_budget_scales_with_complexity():
    config = ContextConfig(
        max_tokens=1000, min_budget_ratio=0.5, max_budget_ratio=1.0, reserve_ratio=0.2
    )
    builder = ContextBuilder(config)
    simple = builder._compute_budget(config, "你好", [], None)
    complex_query = "如何根据文档配置向量库" + "详" * 300
    complex_budget = builder._compute_budget(config, complex_query, [], None)
    assert simple.scaled_max_tokens < complex_budget.scaled_max_tokens
    assert simple.requested_max_tokens == 1000
    assert simple.policy == "heuristic"
    assert simple.complexity < complex_budget.complexity


def test_budget_reserves_ratio_for_system_instructions():
    """修复 B11：reserve_ratio 必须真正生效"""
    config = ContextConfig(max_tokens=2000, reserve_ratio=0.3)
    builder = ContextBuilder(config)
    info = builder._compute_budget(config, "你好", [], "系统指令")
    assert info.reserved_tokens == int(info.scaled_max_tokens * 0.3)
    assert info.available_tokens == info.scaled_max_tokens - info.reserved_tokens


def test_budget_uses_injected_policy():
    config = ContextConfig(max_tokens=1000)
    builder = ContextBuilder(config, budget_policy=_FixedPolicy(1.0))
    info = builder._compute_budget(config, "任意查询", [], None)
    assert info.policy == "fixed"
    assert info.scaled_max_tokens == 1000
    assert info.complexity == 1.0


def test_budget_scaled_max_tokens_is_at_least_one():
    config = ContextConfig(max_tokens=1, min_budget_ratio=0.1, max_budget_ratio=0.1)
    builder = ContextBuilder(config)
    assert builder._compute_budget(config, "你好", [], None).scaled_max_tokens >= 1


def test_config_budget_policy_is_used_when_not_injected():
    config = ContextConfig(budget_policy=_FixedPolicy(0.0))
    builder = ContextBuilder(config)
    assert builder.budget_policy.name == "fixed"


def test_explicit_policy_beats_config_policy():
    config = ContextConfig(budget_policy=_FixedPolicy(0.0))
    builder = ContextBuilder(config, budget_policy=HeuristicBudgetPolicy())
    assert builder.budget_policy.name == "heuristic"


def test_parse_timestamp_accepts_iso_and_datetime():
    now = datetime.now(tz=UTC)
    assert _parse_timestamp(now) is now
    parsed = _parse_timestamp("2026-09-23T10:00:00")
    assert parsed.year == 2026
    assert isinstance(_parse_timestamp(None), datetime)
    assert isinstance(_parse_timestamp("not-a-date"), datetime)


def test_count_by_source_always_lists_all_five_types():
    packets = [
        ContextPacket(content="a", timestamp=datetime.now(tz=UTC), metadata={"type": "rag"}),
        ContextPacket(content="b", timestamp=datetime.now(tz=UTC), metadata={"type": "rag"}),
        ContextPacket(content="c", timestamp=datetime.now(tz=UTC)),
    ]
    counts = _count_by_source(packets)
    assert counts == {
        "system_instruction": 0,
        "memory": 0,
        "rag": 2,
        "history": 0,
        "custom": 1,
    }


def test_cache_counters_include_scorer_cache():
    from hello_agents.context.scoring import EmbeddingSimilarityScorer
    from hello_agents.memory import TFIDFEmbedding

    scorer = EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))
    builder = ContextBuilder(relevance_scorer=scorer)
    scorer.score("用户喜欢爬山", "用户喜欢")
    hits, misses = builder._cache_counters()
    assert hits + misses > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `ImportError: cannot import name 'ContextBuilder'`

- [ ] **Step 3: Write minimal implementation**

创建 `hello_agents/context/builder.py`。数据类型仍在 `base.py`，此处只做导入；`__all__` 只导出 `ContextBuilder`（数据类型由 `base.__all__` 负责）。

**导入区按需逐任务补齐**，不要一次写全——本计划每个任务收尾都要对触碰的文件跑 `ruff check` 清零，提前导入未使用的名字会触发 `F401`。下面是 **Task 7 时点**的精确导入区：

```python
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
import tiktoken
from datetime import UTC, datetime
from typing import Any

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
```

后续任务追加代码时的**导入增量**（已在各自的 Step 3 中重复列出，此处备查）：
- Task 8：`import hashlib`（isort 顺序排在 `logging` 之前）、`from ..tools.response import ToolStatus`（`ContextPacket` 已在 Task 7 导入，见下）
- Task 9：`import math`
- Task 10：`from .base import ...` 扩为 `(_SOURCE_TYPES, _TEMPLATE_ORDER, ContextConfig, ContextPacket, ContextSection)`
- Task 11：`from time import perf_counter`、`from typing import TYPE_CHECKING, Any`、`from .base import ...` 扩为 `(_SOURCE_TYPES, _TEMPLATE_ORDER, BuildResult, BuildStats, ContextConfig, ContextPacket, ContextSection)`，并补 `if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from .experiment import ExperimentSpec`

⚠️ **`ContextPacket` 必须在 Task 7 就导入**，不能推到 Task 8。理由：`_count_by_source`
的注解是 `list[ContextPacket]`，而 ruff 的 `F821` 会检查注解里的未定义名字 —— 即使
`from __future__ import annotations` 把注解变成惰性字符串也照样报（已用 `--isolated` 复现）。
推到 Task 8 会让 Task 7 过不了本计划自己定的「触碰文件 lint 清零」门槛。

导入区之后追加模块级辅助函数与 `ContextBuilder` 的骨架：

```python
def _parse_timestamp(raw: Any) -> datetime:
    """把 ISO 字符串还原为 datetime，缺失或非法时退回当前时刻"""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("无法解析时间戳 %r，退回当前时刻", raw)
    return datetime.now(tz=UTC)


def _count_by_source(packets: list[ContextPacket]) -> dict[str, int]:
    """按 metadata["type"] 统计包数量，五种来源恒出现在结果中"""
    counts = {name: 0 for name in _SOURCE_TYPES}
    for packet in packets:
        source = packet.metadata.get("type", "custom")
        counts[source] = counts.get(source, 0) + 1
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
        except Exception as exc:  # noqa: BLE001 - 编码器缺失一律转为配置错误
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
            policy=getattr(self.budget_policy, "name", type(self.budget_policy).__name__),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 22 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/builder.py tests/test_context_builder.py
git commit -m "feat: add ContextBuilder skeleton with tiktoken counting and budget scaling"
```

---

## Task 8: 汇集阶段（Gather）

**Files:**
- Modify: `hello_agents/context/builder.py`（追加 `_system_packet` / `_hits_to_packets` / `_memory_packets` / `_rag_packets` / `_history_packets` / `_gather`）
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 8: Gather ---

from hello_agents.core import Message, MessageRole
from hello_agents.tools.response import ToolResponse, ToolStatus


class _FakeTool:
    """工具替身：记录 payload 并返回预设响应"""

    def __init__(self, data=None, status=ToolStatus.SUCCESS, raises=None) -> None:
        self.data = data or {}
        self.status = status
        self.raises = raises
        self.calls: list[dict] = []

    def run(self, input_data, **kwargs):
        self.calls.append(input_data)
        if self.raises is not None:
            raise self.raises
        return ToolResponse(status=self.status, text="", data=self.data)


def _memory_hits(*contents, score=0.8, importance=None):
    hits = []
    for index, content in enumerate(contents):
        metadata = {} if importance is None else {"importance": importance}
        hits.append(
            {
                "id": f"m{index}",
                "content": content,
                "memory_type": "semantic",
                "metadata": metadata,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "expires_at": None,
                "score": score,
            }
        )
    return hits


def test_gather_builds_system_instruction_packet():
    builder = ContextBuilder()
    packets = builder._gather("查询", [], "你是助手", [], builder.config)
    assert len(packets) == 1
    assert packets[0].metadata["type"] == "system_instruction"
    assert packets[0].relevance_score == 1.0
    assert packets[0].token_count == builder._count_tokens("你是助手")


def test_gather_reuses_cached_system_instruction_packet():
    builder = ContextBuilder()
    first = builder._gather("查询", [], "你是助手", [], builder.config)
    second = builder._gather("另一查询", [], "你是助手", [], builder.config)
    assert first[0] is second[0]


def test_gather_calls_memory_tool_with_real_contract():
    """修复 B13：action 必须是 recall，且不得传记忆层不识别的参数"""
    tool = _FakeTool(data={"hits": _memory_hits("用户喜欢爬山")})
    builder = ContextBuilder(memory_tool=tool)
    packets = builder._gather("爬山", [], None, [], builder.config)
    payload = tool.calls[0]
    assert payload["action"] == "recall"
    assert payload["query"] == "爬山"
    assert payload["limit"] == builder.config.memory_limit
    assert "min_importance" not in payload
    assert "min_score" not in payload
    assert packets[0].metadata["type"] == "memory"
    assert packets[0].relevance_score == 0.8


def test_gather_calls_rag_tool_with_real_contract():
    """修复 B13：action 必须是 query，条数参数是 top_k"""
    tool = _FakeTool(data={"chunks": _memory_hits("向量库配置说明", score=0.9)})
    builder = ContextBuilder(rag_tool=tool)
    packets = builder._gather("向量库", [], None, [], builder.config)
    payload = tool.calls[0]
    assert payload["action"] == "query"
    assert payload["question"] == "向量库"
    assert payload["top_k"] == builder.config.rag_limit
    assert packets[0].metadata["type"] == "rag"


def test_gather_skips_missing_tools():
    """修复 B2：未注入工具时必须静默跳过而非 AttributeError"""
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [], builder.config)
    assert packets == []


def test_gather_skips_tool_error_response():
    tool = _FakeTool(status=ToolStatus.ERROR)
    builder = ContextBuilder(memory_tool=tool)
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_skips_tool_exception():
    tool = _FakeTool(raises=RuntimeError("boom"))
    builder = ContextBuilder(memory_tool=tool)
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_filters_by_min_source_score():
    tool = _FakeTool(data={"hits": _memory_hits("低分命中", score=0.05)})
    builder = ContextBuilder(
        memory_tool=tool, config=ContextConfig(min_source_score=0.5)
    )
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_filters_by_min_importance():
    tool = _FakeTool(data={"hits": _memory_hits("低重要度", importance=0.1)})
    builder = ContextBuilder(
        memory_tool=tool, config=ContextConfig(min_importance=0.5)
    )
    assert builder._gather("查询", [], None, [], builder.config) == []


def test_gather_keeps_hits_without_importance_metadata():
    tool = _FakeTool(data={"hits": _memory_hits("无重要度字段")})
    builder = ContextBuilder(
        memory_tool=tool, config=ContextConfig(min_importance=0.5)
    )
    assert len(builder._gather("查询", [], None, [], builder.config)) == 1


def test_gather_trims_history_to_window():
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(10)]
    builder = ContextBuilder(config=ContextConfig(history_window=3))
    packets = builder._gather("查询", history, None, [], builder.config)
    assert len(packets) == 3
    assert "第7轮" in packets[0].content
    assert "第9轮" in packets[2].content


def test_gather_marks_history_position_and_type():
    """修复 B14：历史消息用 position 承载新近性，不读 msg.timestamp"""
    history = [Message(role=MessageRole.USER, content=f"第{i}轮") for i in range(3)]
    builder = ContextBuilder()
    packets = builder._gather("查询", history, None, [], builder.config)
    assert [p.metadata["position"] for p in packets] == [0, 1, 2]
    assert all(p.metadata["type"] == "history" for p in packets)
    assert packets[0].relevance_score is None


def test_gather_fills_token_count_for_custom_packets():
    custom = ContextPacket(content="自定义信息", timestamp=datetime.now(tz=UTC))
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [custom], builder.config)
    assert packets[0].token_count == builder._count_tokens("自定义信息")


def test_gather_keeps_explicit_relevance_score_on_custom_packets():
    """修复 B8：预置 0.5 不得被当作「未评分」"""
    custom = ContextPacket(
        content="自定义信息", timestamp=datetime.now(tz=UTC), relevance_score=0.5
    )
    builder = ContextBuilder()
    packets = builder._gather("查询", [], None, [custom], builder.config)
    assert packets[0].relevance_score == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `AttributeError: 'ContextBuilder' object has no attribute '_gather'`

- [ ] **Step 3: Write minimal implementation**

**先补齐导入区**：加一行 `import hashlib`（isort 顺序排在 `import logging` 之前）、加一行 `from ..tools.response import ToolStatus`，并把 `from .base import _SOURCE_TYPES, ContextConfig` 扩为 `from .base import _SOURCE_TYPES, ContextConfig, ContextPacket`。

在 `ContextBuilder` 的 `_cache_counters` 之后追加：

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 37 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/builder.py tests/test_context_builder.py
git commit -m "feat: implement gather stage with real tool contracts"
```

---

## Task 9: 选择阶段（Select）

> **注意**：本段测试用到 `timedelta`，需把 `tests/test_context_builder.py` 顶部的
> `from datetime import UTC, datetime` 扩展为 `from datetime import UTC, datetime, timedelta`
> （Task 4 只写了 `UTC, datetime`，因为那时 `timedelta` 尚未被使用，留着会触发 F401）。

**Files:**
- Modify: `hello_agents/context/builder.py`（追加 `_calculate_recency` / `_recency_of` / `_select`）
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 9: Select ---


class _SpyScorer:
    """记录被打了分的文本，用于验证「未评分才重算」"""

    def __init__(self, value: float = 0.1) -> None:
        self.value = value
        self.scored: list[str] = []

    def score(self, content: str, query: str) -> float:
        return self.value

    def score_many(self, contents: list[str], query: str) -> list[float]:
        self.scored.extend(contents)
        return [self.value] * len(contents)


def _select_config(**overrides) -> ContextConfig:
    base = {"relevance_weight": 1.0, "recency_weight": 0.0, "min_relevance": 0.0}
    base.update(overrides)
    return ContextConfig(**base)


def test_select_does_not_rescore_explicit_half_score():
    """修复 B8：预置 0.5 不得被重算，None 才重算"""
    scorer = _SpyScorer(0.1)
    config = _select_config()
    builder = ContextBuilder(config, relevance_scorer=scorer)
    explicit = ContextPacket(
        content="explicit", timestamp=datetime.now(tz=UTC), token_count=1, relevance_score=0.5
    )
    unscored = ContextPacket(content="unscored", timestamp=datetime.now(tz=UTC), token_count=1)
    builder._select([explicit, unscored], "q", 100, 100, config)
    assert scorer.scored == ["unscored"]
    assert explicit.relevance_score == 0.5
    assert unscored.relevance_score == 0.1


def test_select_keeps_small_packet_when_large_one_does_not_fit():
    """修复 B9：放不下的大包应跳过而非终止选择"""
    config = _select_config()
    builder = ContextBuilder(config)
    big = ContextPacket(
        content="big", timestamp=datetime.now(tz=UTC), token_count=20, relevance_score=1.0
    )
    small = ContextPacket(
        content="small", timestamp=datetime.now(tz=UTC), token_count=5, relevance_score=0.5
    )
    selected, _, dropped_by_budget = builder._select([big, small], "q", 10, 100, config)
    assert [packet.content for packet in selected] == ["small"]
    assert dropped_by_budget == 1


def test_select_drops_packets_below_min_relevance():
    config = _select_config(min_relevance=0.5)
    builder = ContextBuilder(config)
    weak = ContextPacket(
        content="weak", timestamp=datetime.now(tz=UTC), token_count=1, relevance_score=0.2
    )
    selected, dropped_by_relevance, _ = builder._select([weak], "q", 100, 100, config)
    assert selected == []
    assert dropped_by_relevance == 1


def test_select_always_keeps_system_instructions():
    """系统指令不参与评分，也永不被相关性阈值淘汰"""
    config = _select_config(min_relevance=0.99)
    builder = ContextBuilder(config)
    system = ContextPacket(
        content="你是助手",
        timestamp=datetime.now(tz=UTC),
        token_count=4,
        relevance_score=1.0,
        metadata={"type": "system_instruction"},
    )
    selected, dropped_by_relevance, _ = builder._select([system], "q", 100, 100, config)
    assert selected == [system]
    assert dropped_by_relevance == 0


def test_select_skips_scoring_when_system_instructions_exceed_budget():
    config = _select_config()
    builder = ContextBuilder(config)
    system = ContextPacket(
        content="很长的系统指令",
        timestamp=datetime.now(tz=UTC),
        token_count=50,
        relevance_score=1.0,
        metadata={"type": "system_instruction"},
    )
    other = ContextPacket(
        content="候选", timestamp=datetime.now(tz=UTC), token_count=1, relevance_score=1.0
    )
    selected, _, dropped_by_budget = builder._select([system, other], "q", 0, 10, config)
    assert selected == [system]
    assert dropped_by_budget == 1


def test_select_ranks_history_by_position():
    """修复 B14：历史消息按 position 而非时间戳计算新近性"""
    config = _select_config(relevance_weight=0.0, recency_weight=1.0)
    builder = ContextBuilder(config)
    older = ContextPacket(
        content="旧",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 0},
    )
    newer = ContextPacket(
        content="新",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "history", "position": 1},
    )
    selected, _, _ = builder._select([older, newer], "q", 100, 100, config)
    assert [packet.content for packet in selected] == ["新", "旧"]


def test_recency_decays_with_age():
    builder = ContextBuilder()
    fresh = builder._calculate_recency(datetime.now(tz=UTC))
    stale = builder._calculate_recency(datetime.now(tz=UTC) - timedelta(days=30))
    assert fresh > stale
    assert 0.1 <= stale <= 1.0
    assert 0.1 <= fresh <= 1.0


def test_recency_handles_timezone_aware_timestamp():
    builder = ContextBuilder()
    aware = datetime.now(tz=UTC) - timedelta(hours=1)
    score = builder._calculate_recency(aware)
    assert 0.1 <= score <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `AttributeError: 'ContextBuilder' object has no attribute '_select'`

- [ ] **Step 3: Write minimal implementation**

**先补齐导入区**：加一行 `import math`（isort 顺序：`hashlib`、`logging`、`math`、`tiktoken`）。

在 `ContextBuilder` 的 `_gather` 之后追加：

```python
    def _calculate_recency(self, timestamp: datetime) -> float:
        """指数衰减：24 小时内保持高分，之后逐渐衰减

        记忆层的 created_at 是 tz-aware UTC，而 ISO 字符串解析出的时间戳可能
        是 naive 的；统一按 UTC 归一后再求差，避免 aware / naive 相减报错。
        """
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        age_hours = max(
            0.0, (datetime.now(tz=UTC) - timestamp).total_seconds() / 3600
        )
        return max(0.1, min(1.0, math.exp(-0.1 * age_hours / 24)))

    def _recency_of(self, packet: ContextPacket, history_count: int) -> float:
        """历史消息按 position 线性映射到 [0.5, 1.0]，其余按时间戳衰减"""
        if packet.metadata.get("type") == "history":
            position = int(packet.metadata.get("position", 0))
            span = max(history_count - 1, 1)
            return max(0.5, min(1.0, 0.5 + 0.5 * (position / span)))
        return self._calculate_recency(packet.timestamp)

    def _select(
        self,
        packets: list[ContextPacket],
        user_query: str,
        available_tokens: int,
        scaled_max_tokens: int,
        config: ContextConfig,
    ) -> tuple[list[ContextPacket], int, int]:
        """选择最相关的信息包

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
            logger.warning(
                "系统指令占用 %d tokens，已超过预算 %d，跳过打分选择",
                system_tokens,
                scaled_max_tokens,
            )
            return system_packets, 0, len(other_packets)

        unscored = [
            packet for packet in other_packets if packet.relevance_score is None
        ]
        if unscored:
            scores = self.relevance_scorer.score_many(
                [packet.content for packet in unscored], user_query
            )
            for packet, score in zip(unscored, scores):
                packet.relevance_score = max(0.0, min(1.0, score))

        history_count = sum(
            1 for packet in other_packets if packet.metadata.get("type") == "history"
        )

        scored: list[tuple[float, ContextPacket]] = []
        dropped_by_relevance = 0
        for packet in other_packets:
            relevance = packet.relevance_score or 0.0
            if relevance < config.min_relevance:
                dropped_by_relevance += 1
                continue
            recency = self._recency_of(packet, history_count)
            combined = (
                config.relevance_weight * relevance + config.recency_weight * recency
            )
            scored.append((combined, packet))

        scored.sort(key=lambda item: item[0], reverse=True)

        selected = list(system_packets)
        current_tokens = 0
        dropped_by_budget = 0
        for _, packet in scored:
            if current_tokens + packet.token_count <= available_tokens:
                selected.append(packet)
                current_tokens += packet.token_count
            else:
                dropped_by_budget += 1

        return selected, dropped_by_relevance, dropped_by_budget
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 46 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/builder.py tests/test_context_builder.py
git commit -m "feat: implement select stage with budget, relevance and position-based recency"
```

---

## Task 10: 组织与压缩阶段（Structure / Compress）

**Files:**
- Modify: `hello_agents/context/builder.py`（追加 `_structure` / `_order` / `_render` / `_compress` / `_truncate_section` / `_truncate_text`）
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 10: Structure / Compress ---


def _packet(content: str, packet_type: str, tokens: int = 1) -> ContextPacket:
    return ContextPacket(
        content=content,
        timestamp=datetime.now(tz=UTC),
        token_count=tokens,
        relevance_score=1.0,
        metadata={"type": packet_type},
    )


def test_structure_routes_sources_to_sections():
    builder = ContextBuilder()
    selected = [
        _packet("你是助手", "system_instruction"),
        _packet("知识片段", "rag"),
        _packet("记忆命中", "memory"),
    ]
    sections = builder._structure(selected, "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Role & Policies"] == "你是助手"
    assert by_title["Evidence"] == "知识片段"
    assert by_title["Context"] == "记忆命中"
    assert by_title["Task"] == "问题"


def test_structure_keeps_template_order():
    builder = ContextBuilder()
    # 三包乱序传入：既钉模板顺序，也钉「不许按输入顺序吐出」。
    # 必须含 system_instruction 包 —— Role & Policies 段是 `if policies:` 有条件添加，
    # 少了它实际产出只有 4 段（Task/Evidence/Context/Output），期望的 5 段列表会直接红。
    selected = [
        _packet("记忆", "memory"),
        _packet("知识", "rag"),
        _packet("你是助手", "system_instruction"),
    ]
    sections = builder._structure(selected, "问题")
    assert [section.title for section in sections] == [
        "Role & Policies",
        "Task",
        "Evidence",
        "Context",
        "Output",
    ]


def test_structure_honours_evidence_section_override():
    builder = ContextBuilder()
    packet = ContextPacket(
        content="自定义证据",
        timestamp=datetime.now(tz=UTC),
        token_count=1,
        relevance_score=1.0,
        metadata={"type": "custom", "section": "evidence"},
    )
    sections = builder._structure([packet], "问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Evidence"] == "自定义证据"
    assert "Context" not in by_title


def test_structure_always_includes_task_and_output():
    builder = ContextBuilder()
    sections = builder._structure([], "唯一的问题")
    by_title = {section.title: section.body for section in sections}
    assert by_title["Task"] == "唯一的问题"
    assert "Output" in by_title
    assert "Role & Policies" not in by_title


def test_render_uses_bracketed_titles():
    builder = ContextBuilder()
    rendered = builder._render(
        [ContextSection("Task", "做什么"), ContextSection("Output", "回答")]
    )
    assert rendered == "[Task]\n做什么\n\n[Output]\n回答"


def test_compress_skips_when_under_budget():
    builder = ContextBuilder()
    sections = [ContextSection("Task", "简短"), ContextSection("Output", "回答")]
    kept, compressed = builder._compress(sections, 1000, builder.config)
    assert compressed is False
    assert kept == sections


def test_compress_respects_disabled_flag():
    """修复 B11：enable_compression=False 时不得压缩"""
    config = ContextConfig(enable_compression=False)
    builder = ContextBuilder(config)
    sections = [ContextSection("Task", "很长" * 500), ContextSection("Output", "回答")]
    kept, compressed = builder._compress(sections, 10, config)
    assert compressed is False
    assert kept == sections


def test_compress_truncates_oversized_elastic_section():
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Context", "内容" * 2000),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 100, builder.config)
    assert compressed is True
    assert builder._count_tokens(builder._render(kept)) <= 100
    assert [section.title for section in kept] == ["Task", "Context", "Output"]
    assert "内容已压缩" in kept[1].body


def test_compress_drops_elastic_section_when_no_room():
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务"),
        ContextSection("Context", "内容" * 2000),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 50, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]


def test_compress_never_truncates_task_or_output():
    builder = ContextBuilder()
    sections = [
        ContextSection("Task", "任务" * 300),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 10, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == ["Task", "Output"]


def test_compress_truncates_role_and_policies_when_constants_overflow():
    builder = ContextBuilder()
    sections = [
        ContextSection("Role & Policies", "指令" * 500),
        ContextSection("Task", "任务"),
        ContextSection("Output", "回答"),
    ]
    kept, compressed = builder._compress(sections, 200, builder.config)
    assert compressed is True
    assert [section.title for section in kept] == [
        "Role & Policies",
        "Task",
        "Output",
    ]
    assert "内容已压缩" in kept[0].body
    assert builder._count_tokens(builder._render(kept)) <= 200


def test_truncate_text_is_exact_in_tokens():
    """修复 B10：按 token 精确截断，不再按字符比例猜测"""
    builder = ContextBuilder()
    text = "用户喜欢深蓝色 hello world " * 20
    truncated = builder._truncate_text(text, 10)
    assert builder._count_tokens(truncated) <= 10
    assert builder._truncate_text("短文本", 100) == "短文本"
    assert builder._truncate_text("任意", 0) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `AttributeError: 'ContextBuilder' object has no attribute '_structure'`

- [ ] **Step 3: Write minimal implementation**

在 `hello_agents/context/builder.py` 的常量区（`logger` / `__all__` 之后）补上分隔符余量常量（渲染时段间会插入 `\n\n`，压缩预算需预留）：

```python
_SEPARATOR_MARGIN = 4
```

**先补齐导入区**：把 `from .base import _SOURCE_TYPES, ContextConfig, ContextPacket` 扩为 `from .base import _SOURCE_TYPES, _TEMPLATE_ORDER, ContextConfig, ContextPacket, ContextSection`。

在 `ContextBuilder` 的 `_select` 之后追加：

```python
    def _structure(
        self, selected: list[ContextPacket], user_query: str
    ) -> list[ContextSection]:
        """把入选包路由到模板段落，只组织不渲染"""
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
        sections.append(ContextSection("Output", "请基于以上信息，提供准确、有据的回答。"))
        return sections

    def _order(self, sections: dict[str, ContextSection]) -> list[ContextSection]:
        """把标题到段落的映射按模板顺序还原为列表"""
        return [sections[title] for title in _TEMPLATE_ORDER if title in sections]

    def _render(self, sections: list[ContextSection]) -> str:
        """把段落列表渲染为最终上下文字符串"""
        return "\n\n".join(
            f"[{section.title}]\n{section.body}" for section in sections
        )

    def _truncate_text(self, text: str, max_tokens: int) -> str:
        """按 token 精确截断文本"""
        if max_tokens <= 0:
            return ""
        tokens = self.encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return self.encoder.decode(tokens[:max_tokens])

    def _truncate_section(
        self, section: ContextSection, budget: int
    ) -> ContextSection | None:
        """把段落 body 截断到不超过 budget 个 token；空间过小则整段丢弃"""
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
            title: by_title[title]
            for title in ("Task", "Output")
            if title in by_title
        }
        mandatory_tokens = self._count_tokens(self._render(self._order(kept)))

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 59 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/builder.py tests/test_context_builder.py
git commit -m "feat: implement structure and structure-aware compression"
```

---

## Task 11: 编排入口与统计

**Files:**
- Modify: `hello_agents/context/builder.py`（追加 `build_result` / `build`）
- Modify: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 11: build_result / build ---

import logging

from hello_agents.context.experiment import ExperimentSpec


def test_build_returns_string_with_task_section():
    builder = ContextBuilder()
    context = builder.build("用户想了解什么？")
    assert isinstance(context, str)
    assert "[Task]\n用户想了解什么？" in context


def test_build_full_pipeline_has_no_type_error():
    """修复 B4-B7：四阶段调用签名必须一致"""
    builder = ContextBuilder()
    history = [Message(role=MessageRole.USER, content="你好")]
    custom = [ContextPacket(content="附加信息", timestamp=datetime.now(tz=UTC))]
    result = builder.build_result("问题", history, "你是助手", custom)
    assert result.context
    assert result.stats.candidates_total == 3


def test_build_result_stats_are_consistent():
    builder = ContextBuilder()
    result = builder.build_result("问题", system_instructions="你是助手")
    stats = result.stats
    assert stats.candidates_total > 0
    assert 0.0 < stats.token_utilization <= 1.0
    assert stats.budget.available_tokens == (
        stats.budget.scaled_max_tokens - stats.budget.reserved_tokens
    )
    assert stats.budget.requested_max_tokens == builder.config.max_tokens
    assert stats.duration_ms >= 0.0
    assert stats.experiment is None
    assert stats.variant is None


def test_build_result_counts_selected_by_source():
    builder = ContextBuilder()
    result = builder.build_result("问题", system_instructions="你是助手")
    assert result.stats.selected_by_source["system_instruction"] == 1
    assert result.stats.selected_tokens == 0  # 系统指令不计入打分包的 token


def test_second_build_hits_system_instruction_cache():
    builder = ContextBuilder()
    builder.build_result("问题", system_instructions="你是助手")
    second = builder.build_result("问题", system_instructions="你是助手")
    assert second.stats.cache_hits > 0


def test_build_result_records_experiment_variant():
    spec = ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
    )
    builder = ContextBuilder(ContextConfig(experiment=spec))
    result = builder.build_result("问题", session_id="session-42")
    assert result.stats.experiment == "scoring_v1"
    assert result.stats.variant in {"control", "variant_a"}


def test_experiment_without_session_id_falls_back_to_first_variant(caplog):
    spec = ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
    )
    builder = ContextBuilder(ContextConfig(experiment=spec))
    with caplog.at_level(logging.WARNING, logger="hello_agents.context"):
        result = builder.build_result("问题")
    assert result.stats.variant == "control"
    assert any("session_id" in record.message for record in caplog.records)


def test_log_stats_emits_info_summary(caplog):
    builder = ContextBuilder()
    with caplog.at_level(logging.INFO, logger="hello_agents.context"):
        builder.build_result("问题")
    assert any("candidates=" in record.message for record in caplog.records)


def test_log_stats_can_be_disabled(caplog):
    builder = ContextBuilder(ContextConfig(log_stats=False))
    with caplog.at_level(logging.INFO, logger="hello_agents.context"):
        builder.build_result("问题")
    assert not any("candidates=" in record.message for record in caplog.records)


def test_tool_failure_does_not_break_build():
    """修复 B13：工具失败必须降级而非抛出"""
    tool = _FakeTool(raises=RuntimeError("boom"))
    builder = ContextBuilder(memory_tool=tool)
    result = builder.build_result("问题")
    assert "[Task]" in result.context
    assert result.stats.candidates_by_source["memory"] == 0


def test_build_result_with_tools_populates_sources():
    memory_tool = _FakeTool(data={"hits": _memory_hits("用户喜欢爬山", score=0.9)})
    rag_tool = _FakeTool(data={"chunks": _memory_hits("向量库说明", score=0.9)})
    builder = ContextBuilder(
        ContextConfig(relevance_weight=0.5, recency_weight=0.5),
        memory_tool=memory_tool,
        rag_tool=rag_tool,
    )
    result = builder.build_result("爬山")
    assert result.stats.candidates_by_source["memory"] == 1
    assert result.stats.candidates_by_source["rag"] == 1
    assert "[Evidence]" in result.context
    assert "用户喜欢爬山" in result.context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: FAIL — `AttributeError: 'ContextBuilder' object has no attribute 'build_result'`

- [ ] **Step 3: Write minimal implementation**

**先补齐导入区**：加 `from time import perf_counter`（isort 顺序排在 `datetime` 之后）、把 `from typing import Any` 扩为 `from typing import TYPE_CHECKING, Any`、把 `from .base import ...` 扩为 `from .base import _SOURCE_TYPES, _TEMPLATE_ORDER, BuildResult, BuildStats, ContextConfig, ContextPacket, ContextSection`，并补上：

```python
if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from .experiment import ExperimentSpec
```

在 `ContextBuilder` 的 `_compress` 之后追加：

```python
    def build_result(
        self,
        user_query: str,
        conversation_history: list[Message] | None = None,
        system_instructions: str | None = None,
        additional_packets: list[ContextPacket] | None = None,
        *,
        session_id: str | None = None,
    ) -> BuildResult:
        """构建上下文并返回上下文与统计"""
        started = perf_counter()
        history = list(conversation_history or [])
        packets_input = list(additional_packets or [])
        config = self.config
        experiment_name: str | None = None
        variant: str | None = None

        # 阶段 0：实验分流
        if config.experiment is not None:
            experiment_name = config.experiment.name
            if session_id:
                config, variant = self._assigner.apply(
                    config, config.experiment, session_id
                )
            else:
                variant = next(iter(config.experiment.variants))
                logger.warning(
                    "配置了实验 %s 但未提供 session_id，使用变体 %s",
                    experiment_name,
                    variant,
                )

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
            selected_tokens=sum(
                packet.token_count
                for packet in selected
                if packet.metadata.get("type") != "system_instruction"
            ),
            budget=budget,
            token_utilization=(
                final_tokens / budget.scaled_max_tokens
                if budget.scaled_max_tokens
                else 0.0
            ),
            compressed=compressed,
            compression_ratio=(
                final_tokens / structured_tokens if structured_tokens else 1.0
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
        """构建上下文，仅返回上下文字符串（向后兼容入口）"""
        return self.build_result(
            user_query,
            conversation_history,
            system_instructions,
            additional_packets,
            session_id=session_id,
        ).context
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -v`
Expected: PASS — 69 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/builder.py tests/test_context_builder.py
git commit -m "feat: add build_result orchestration with BuildStats and logging"
```

---

## Task 12: 修复 RAGTool 丢弃 score

`MemoryTool.recall` 返回的命中带 `score`，而 `RAGTool` 的 `chunk.to_dict()` 丢掉了它，导致 RAG 相关性无法传入上下文层（缺陷 B15）。

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/test_rag_tool.py`（追加一条用例）

- [ ] **Step 1: 确认 RAGTool 的构造方式与参数名**

Run: `uv run python -c "import inspect; from hello_agents.tools.builtin import RAGTool; print(inspect.signature(RAGTool.__init__)); print(inspect.getsource(RAGTool._do_query))"`

Expected: 打印构造函数签名与 `_do_query` 当前实现（可见 `"chunks": [chunk.to_dict() for chunk in result.chunks]`，无 `score`）。

同时 Run: `uv run pytest tests/test_rag_tool.py -q` 确认既有用例通过，并记下它们构造 `RAGTool` 与调用 `ingest` 的写法。

- [ ] **Step 2: Write the failing test**

在 `tests/test_rag_tool.py` 末尾追加。**直接用该文件既有的 `tool` 夹具**（它构造的是
`RAGPipeline(memory_manager=manager, chunk_size=200, chunk_overlap=20)` → `RAGTool(pipeline=pipeline)`）：

```python
def test_query_chunks_include_score(tool):
    """修复 B15：chunk 字典必须带 score，与 MemoryTool.recall 对齐

    注意：ingest 只传 `content`，**不要**同时传 `source` —— `rag_tool.py` 的分派是
    `if source: ingest_file(source) / elif content: ingest_text(content)`，source 优先，
    `"demo"` 会被当成文件路径走 `ingest_file` 而失败，query 拿到 0 个 chunk，
    与本用例要钉的 score 无关地变红。
    """
    tool.run({"action": "ingest", "content": "向量库使用 Qdrant 存储"})
    resp = tool.run({"action": "query", "question": "向量库用什么存储"})
    assert resp.data["chunks"]
    # 不只钉键存在 —— `{"score": 0.0}` 也能满足 `"score" in chunk`。
    # 与 pipeline 的检索真值比对，钉住 score 是真带过来的，不是占位。
    result = tool.pipeline.query("向量库用什么存储")
    for chunk, item in zip(resp.data["chunks"], result.chunks):
        assert chunk["score"] == pytest.approx(item.score)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_rag_tool.py::test_query_chunks_include_score -v`
Expected: FAIL — `AssertionError: assert all("score" in chunk ...)`

- [ ] **Step 4: Write minimal implementation**

在 `hello_agents/tools/builtin/rag_tool.py` 的 `_do_query` 中，把 chunks 的构造改为与 `MemoryTool.recall` 一致：

```python
            "chunks": [chunk.to_dict() | {"score": chunk.score} for chunk in result.chunks],
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_rag_tool.py -v`
Expected: PASS — 既有用例 + 新增 1 条全绿

- [ ] **Step 6: Commit**

```bash
git add hello_agents/tools/builtin/rag_tool.py tests/test_rag_tool.py
git commit -m "fix: include retrieval score in RAGTool query chunks"
```

---

## Task 13: 公开导出

**Files:**
- Rewrite: `hello_agents/context/__init__.py`
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_context_builder.py` 末尾追加：

```python
# --- Task 13: 公开导出 ---


def test_public_api_is_importable_from_package_root():
    from hello_agents import context

    for name in (
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
    ):
        assert name in context.__all__, f"{name} 未导出"
        assert hasattr(context, name), f"{name} 不存在"


def test_public_api_all_is_sorted():
    from hello_agents import context

    assert context.__all__ == sorted(context.__all__)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_builder.py -k public_api -v`
Expected: FAIL — `AssertionError: ContextBuilder 未导出`

- [ ] **Step 3: Write minimal implementation**

用以下内容重写 `hello_agents/context/__init__.py`：

```python
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
from .builder import ContextBuilder
from .budget import BudgetInfo, BudgetPolicy, HeuristicBudgetPolicy
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_builder.py -k public_api -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add hello_agents/context/__init__.py tests/test_context_builder.py
git commit -m "feat: export public context API from package root"
```

---

## Task 14: 离线示例脚本

**Files:**
- Create: `examples/context_builder_demo.py`

- [ ] **Step 1: Write the script**

创建 `examples/context_builder_demo.py`：

```python
"""ContextBuilder 演示：离线可跑，无需任何 API Key

覆盖：动态预算、两种打分器、缓存命中、构建统计、A/B 稳定分流。

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
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def _knowledge_packets() -> list[ContextPacket]:
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


def demo_dynamic_budget() -> None:
    banner("1. 动态 token 预算")
    builder = ContextBuilder(ContextConfig(max_tokens=4000))
    simple = builder.build_result("你好")
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


def demo_scorers() -> None:
    banner("2. 两种相关性打分器")
    query = "如何配置 Qdrant 向量库"
    contents = [packet.content for packet in _knowledge_packets()]
    for scorer in (
        KeywordOverlapScorer(),
        EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64)),
    ):
        scores = scorer.score_many(contents, query)
        print(f"{scorer.name}: {[round(score, 3) for score in scores]}")


def demo_cache() -> None:
    banner("3. 缓存命中")
    builder = ContextBuilder()
    instructions = "你是严谨的技术助手。"
    first = builder.build_result("问题", system_instructions=instructions)
    second = builder.build_result("问题", system_instructions=instructions)
    print(f"首次构建 cache_hits={first.stats.cache_hits}")
    print(f"二次构建 cache_hits={second.stats.cache_hits}")


def demo_stats() -> None:
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


def demo_experiment() -> None:
    banner("5. A/B 稳定分流")
    spec = ExperimentSpec(
        name="scoring_v1",
        variants={
            "control": {"relevance_weight": 0.7, "recency_weight": 0.3},
            "variant_a": {"relevance_weight": 0.5, "recency_weight": 0.5},
        },
    )
    assigner = ExperimentAssigner()
    for session in ("session-42", "session-42", "session-7"):
        print(f"{session} -> {assigner.assign(spec, session)}")
    builder = ContextBuilder(ContextConfig(experiment=spec))
    result = builder.build_result("问题", session_id="session-42")
    print(f"构建时记录的实验: {result.stats.experiment}/{result.stats.variant}")


def main() -> None:
    demo_dynamic_budget()
    demo_scorers()
    demo_cache()
    demo_stats()
    demo_experiment()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

Run: `uv run python examples/context_builder_demo.py`
Expected: 五个小节全部打印完毕，无异常；第 1 节复杂查询的 `scaled` 大于简单查询；第 3 节二次构建 `cache_hits` 大于 0；第 5 节 `session-42` 两次输出同一变体

- [ ] **Step 3: Commit**

```bash
git add examples/context_builder_demo.py
git commit -m "docs: add offline ContextBuilder demo script"
```

---

## Task 15: README

仓库根目录 `README.md` 当前是空文件。

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 写入 README**

用下面「README 正文」一节的内容填充 `README.md`。注意：正文中的代码围栏是三个反引号，写入文件时保持原样（不要带上本计划用于包裹它的四个反引号）。

**README 正文**

````markdown
# hello-agents

一个从零实现的 Agent 框架，包含 LLM 客户端、四种 Agent 模式、工具系统、
四类认知记忆与 RAG 管道，以及生产化的上下文构建器。

## 安装

```bash
uv sync
```

可选能力通过 extras 安装：

```bash
uv sync --extra qdrant --extra neo4j --extra rag --extra local
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

builder = ContextBuilder(ContextConfig(max_tokens=4096))
context = builder.build("用户想了解什么？", conversation_history=history)
```

需要构建统计时改用 `build_result()` —— `build()` 保持返回 `str` 不变：

```python
result = builder.build_result("用户想了解什么？", conversation_history=history)
print(result.context)
print(result.stats.summary())
# candidates=5 selected=3 tokens=412/2160 utilization=0.19 complexity=0.42 ...
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

`create_relevance_scorer("auto")` 会在向量后端不可用时自动降级为关键词重叠。

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
result = builder.build_result(query, session_id=user_session_id)
print(result.stats.experiment, result.stats.variant)
```

同一 `session_id` 恒定落在同一变体（基于 SHA-256 稳定分流，无随机状态）。
指标随 `BuildStats` 落入日志，聚合分析交给外部日志平台。

### 日志

模块使用标准库 `logging`，logger 名为 `hello_agents.context`：

```python
import logging

logging.basicConfig(level=logging.INFO)
```

`DEBUG` 输出各阶段明细，`INFO` 输出单行统计摘要（受 `log_stats` 控制），
`WARNING` 输出检索失败、预算占满与压缩触发。

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
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document project modules and ContextBuilder usage"
```

---

## Task 16: 全量验证

**Files:**
- Modify: `hello_agents/memory/embedding.py`（Step 0，执行期发现的既有缺陷）
- 其余无改动，仅验证

> **执行期修订**：执行开始时发现两处**既有**问题会让本任务的原始门槛无法达成，
> 已在下方显式处理，而非静默放宽。
> ① `tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing`
> 在本分支未改动任何 embedding 文件时即失败（基线 `1 failed, 75 passed, 1 skipped`）。
> ② `uv run ruff check hello_agents tests examples` 基线为 39 个告警，其中 13 个散落在
> `search.py` / `core/llm.py` / `chain.py` / `memory_tool.py` / `calculator.py` /
> `neo4j_store.py` / `simple_agent.py` / `react_agent.py` 等本次不涉及的模块。

- [ ] **Step 0: 修复既有失败用例**

`create_embedding` 把 API key 校验限死在 `auto` 分支：

```python
if not cfg.dashscope_api_key and backend == "auto":
    raise RuntimeError("DASHSCOPE_API_KEY 未配置")
```

而 `DashScopeEmbedding.__init__` 也不校验 key。于是显式 `backend="dashscope"` 且
key 为 `None` 时会静默返回一个调用时才失败的嵌入器 —— 与该函数 docstring
「显式指定 backend 时失败会抛出异常，不做静默降级」直接矛盾。该用例原本依赖
`dashscope` 未安装时的 `ImportError`，而 c0e26ff 已把 `dashscope` 提为核心依赖，
这条路径随之失效。

改为：

```python
if not cfg.dashscope_api_key:
    raise RuntimeError("DASHSCOPE_API_KEY 未配置")
```

`auto` 分支行为不变：该 `raise` 位于 `try` 内，会被 `except Exception` 捕获并继续
降级到 `local` → `tfidf`。

Run: `uv run pytest tests/test_embedding.py -q`
Expected: PASS —— 3 passed

```bash
git add hello_agents/memory/embedding.py
git commit -m "fix: raise when explicit dashscope backend has no api key"
```

- [ ] **Step 1: 运行全部测试**

Run: `uv run pytest tests/ -q`
Expected: PASS —— `0 failed`，新增 5 个 context 测试文件与既有 10 个测试文件全绿

- [ ] **Step 2: 检查 lint**

Run: `uv run ruff check hello_agents tests examples`
Expected: 仅剩既有 13 个告警，且**无一落在本次触碰的文件**上。
`hello_agents/context/`（含重写后的 `base.py`）、`hello_agents/memory/__init__.py`、
`hello_agents/core/__init__.py`、`hello_agents/tools/builtin/rag_tool.py`、
`tests/test_context_*.py`、`examples/context_builder_demo.py` 必须全部零告警。
既有 13 个告警超出本计划范围，记录在案不修。

- [ ] **Step 3: 检查格式**

Run: `uv run ruff format --check hello_agents tests examples`
Expected: 无文件需要重新格式化

- [ ] **Step 4: 确认示例离线可跑**

Run: `uv run python examples/context_builder_demo.py`
Expected: 五节全部输出，无异常

- [ ] **Step 5: 逐条核对验收标准**

对照 Spec §7.4 的 7 条验收标准逐条确认：

1. `uv run pytest tests/ -q` 全绿且既有 memory/tools 测试无回归 → 由 Step 1 覆盖
2. `ruff check` 与 `ruff format --check` 通过 → 由 Step 2、3 覆盖
3. 无 `DASHSCOPE_API_KEY` 时示例可跑通 → 由 Step 4 覆盖
4. `build()` 返回 `str` 且含 `[Task]` 段 → 由 `test_build_returns_string_with_task_section` 覆盖
5. `stats` 满足 `candidates_total > 0`、`token_utilization > 0`（**不设上界**：`Task`/`Output` 段恒不截断，可能超出预算；上界由压缩契约兜底）、`budget.available_tokens == budget.scaled_max_tokens - budget.reserved_tokens` → 由 `test_build_result_stats_are_consistent` 覆盖
6. 同一 `session_id` 两次 `assign()` 结果相同 → 由 `test_assignment_is_stable_for_same_unit` 覆盖
7. 缓存二次构建 `cache_hits > 0` → 由 `test_second_build_hits_system_instruction_cache` 覆盖

- [ ] **Step 6: 最终提交**

```bash
git status
git commit -m "chore: verify context module against acceptance criteria" --allow-empty
```

---

## 附录 A：缺陷回归点对照

| 缺陷 | 覆盖用例 |
|---|---|
| B1 | `test_build_full_pipeline_has_no_type_error`（`_gather` 返回非空） |
| B2 | `test_gather_skips_missing_tools` |
| B3 | `test_gather_calls_memory_tool_with_real_contract`、`test_gather_calls_rag_tool_with_real_contract` |
| B4–B7 | `test_build_full_pipeline_has_no_type_error` |
| B8 | `test_select_does_not_rescore_explicit_half_score`、`test_gather_keeps_explicit_relevance_score_on_custom_packets` |
| B9 | `test_select_keeps_small_packet_when_large_one_does_not_fit` |
| B10 | `test_count_tokens_uses_tiktoken`、`test_truncate_text_is_exact_in_tokens` |
| B11 | `test_budget_reserves_ratio_for_system_instructions`、`test_compress_respects_disabled_flag` |
| B12 | `test_config_rejects_bad_weights`、`test_config_rejects_out_of_range_ratio`、`test_config_rejects_negative_limits` |
| B13 | `test_gather_calls_memory_tool_with_real_contract`、`test_tool_failure_does_not_break_build` |
| B14 | `test_gather_marks_history_position_and_type`、`test_select_ranks_history_by_position`、`test_recency_handles_timezone_aware_timestamp` |
| B15 | `test_query_chunks_include_score`、`test_build_result_with_tools_populates_sources` |

## 附录 B：相对 Spec 的三处细化

以下三处实现细节比 Spec 更具体，均为在 Spec 意图内的收紧，不改变行为契约：

1. **`ExperimentSpec` 在构造时即校验白名单字段**（Spec §6 表格把它记在 `ExperimentAssigner.apply`）。构造即校验属 fail-fast，且 `apply` 仍会在 `dataclasses.replace` 抛 `TypeError` 时包装为 `ConfigError`，两条路径都覆盖。
2. **自定义信息包只计算 `token_count`、不进缓存**（Spec §5.2 第 5 点提到「并缓存」）。P3 的缓存目标是「不变的系统指令与知识库内容」，自定义包通常每次调用都不同，缓存无收益；系统指令包与 embedding 仍照常缓存。
3. **`logger` 使用 `logging.getLogger(__name__)`**，`base.py` 中实际 logger 名为 `hello_agents.context.base`（Spec §5.6 写作 `hello_agents.context`）。它是 `hello_agents.context` 的子 logger，因此 `logging.getLogger("hello_agents.context")` 仍能捕获全部输出。

