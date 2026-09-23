"""轻量 A/B 实验

按 unit_id 稳定分流到实验变体，并把变体的参数覆盖应用到 ContextConfig 副本上。

典型用法：
    from hello_agents.context.experiment import ExperimentAssigner, ExperimentSpec

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
import math
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
        for name, overrides in self.variants.items():
            if not isinstance(overrides, dict):
                raise ConfigError(
                    f"ExperimentSpec.variants[{name!r}] 必须是 dict，"
                    f"得到 {type(overrides).__name__}"
                )
        unknown = {
            key for overrides in self.variants.values() for key in overrides
        } - _OVERRIDABLE_FIELDS
        if unknown:
            raise ConfigError(f"实验变体包含不可覆盖的字段: {sorted(unknown)}")
        if self.weights is not None:
            missing = set(self.variants) - set(self.weights)
            if missing:
                raise ConfigError(f"weights 缺少变体: {sorted(missing)}")
            extra = set(self.weights) - set(self.variants)
            if extra:
                raise ConfigError(f"weights 多余变体: {sorted(extra)}")
            invalid = [
                name
                for name, weight in self.weights.items()
                if not isinstance(weight, int | float) or not math.isfinite(weight)
            ]
            if invalid:
                raise ConfigError(f"weights 含非有限数值: {sorted(invalid)}")
            negative = [name for name, weight in self.weights.items() if weight < 0]
            if negative:
                raise ConfigError(f"weights 不能为负: {sorted(negative)}")
            if not math.isfinite(sum(self.weights.values())):
                raise ConfigError("weights 之和超出有限范围")


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
        digest = hashlib.sha256(f"{self.seed}:{spec.name}:{unit_id}".encode()).digest()
        bucket = int.from_bytes(digest[:8], "big") / 2**64
        cumulative = 0.0
        for name in names:
            cumulative += weights[name]
            if bucket < cumulative:
                return name
        # 兜底：float64 下 (2**64 - 1) / 2**64 == 1.0，bucket 理论可取 1.0，
        # 此时对任何 cumulative <= 1.0 都有 bucket < cumulative 为假。
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
        overrides = spec.variants[variant]
        # spec 是可变 dataclass：构造后就地塞字段可绕过 __post_init__。
        # 「不存在的」与「存在但不可覆盖的」字段都必须在 replace 前拦下，
        # 后者 dataclasses.replace 会静默接受，比 TypeError 更危险。
        illegal = set(overrides) - _OVERRIDABLE_FIELDS
        if illegal:
            raise ConfigError(
                f"实验变体 {variant} 含超出白名单的字段: {sorted(illegal)}"
            )
        try:
            updated = replace(config, **overrides)
        except TypeError as exc:
            # 残余防御：白名单外字段已在上方拦截，此处兜住 dataclasses 自身报错
            raise ConfigError(f"实验变体 {variant} 的字段覆盖非法: {exc}") from exc
        return updated, variant

    def _normalized_weights(self, spec: ExperimentSpec) -> dict[str, float]:
        names = list(spec.variants)
        raw = spec.weights or {name: 1.0 for name in names}
        total = sum(raw[name] for name in names)
        if total <= 0:
            raise ConfigError("实验权重之和必须为正数")
        return {name: raw[name] / total for name in names}
