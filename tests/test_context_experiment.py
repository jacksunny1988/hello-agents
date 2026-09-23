"""实验分流测试：稳定分流 / 权重分布 / 配置覆盖与校验

覆盖计划 Task 6 的 12 个基线用例，并按必修清单 E1–E9 补齐零覆盖分支、
修掉恒真断言、补正例与 match= 钉消息，另加公式精确比对用例钉住
seed / 归一化 / 插入序 / bucket 公式本身。
"""

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
    # SHA-256 分流是确定性的，不是随机采样：n=2000 时 control 比例固定为 0.7570，
    # n=5000 时为 0.7554。此处保留 (0.70, 0.80) 作粗粒度健全性检查，
    # 精确算法由 test_assignment_matches_stable_hash_formula 钉住。
    assigner = ExperimentAssigner()
    spec = _spec(weights={"control": 3.0, "variant_a": 1.0})
    total = 2000
    controls = sum(
        1 for i in range(total) if assigner.assign(spec, f"u{i}") == "control"
    )
    assert 0.70 < controls / total < 0.80


def test_seed_changes_assignment():
    # 计划原断言 isinstance(a, str) and isinstance(b, str) 恒真，与「seed 改变分流」
    # 无关；单点 assert a != b 也不成立（session-1 在 seed-a / seed-b 下都落 variant_a）。
    # 改为向量级断言：两个 seed 在 32 个 unit 上的分配序列必须不同。
    spec = _spec()
    seq_a = [ExperimentAssigner(seed="seed-a").assign(spec, f"u{i}") for i in range(32)]
    seq_b = [ExperimentAssigner(seed="seed-b").assign(spec, f"u{i}") for i in range(32)]
    assert isinstance(seq_a[0], str) and isinstance(seq_b[0], str)
    assert seq_a != seq_b
    diff = sum(1 for a, b in zip(seq_a, seq_b, strict=True) if a != b)
    assert diff >= 5


def test_single_variant_spec_always_returns_it():
    # 契约文档用例：钉不住「单变体早退」分支（删掉早退后 bucket 累积到 1.0
    # 仍会命中唯一变体）。能钉住早退的是 test_single_variant_zero_weight_pins_early_return。
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(name="only", variants={"control": {}})
    assert assigner.assign(spec, "anything") == "control"


def test_single_variant_zero_weight_pins_early_return():
    # 钉住 assign() 的单变体早退：weights={"control": 0.0} 可构造（非负、无缺失、
    # 无多余），但若删掉早退走进 _normalized_weights，total=0.0 会抛「正数」。
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(
        name="only", variants={"control": {}}, weights={"control": 0.0}
    )
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
    # match= 钉住「和校验」而非裸字段名：relevance_weight=0.9 自身在 [0,1] 内，
    # 只有 recency+relevance 的和校验会抛，删掉和校验即 KILLED。
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(
        name="broken", variants={"control": {"relevance_weight": 0.9}}
    )
    with pytest.raises(ConfigError, match="必须等于 1.0"):
        assigner.apply(ContextConfig(), spec, "session-1")


def test_apply_rejects_mutated_illegal_field():
    # ExperimentSpec 是可变 dataclass：构造合法 spec 后就地塞非法字段名可绕过
    # __post_init__。apply() 的 except TypeError 必须转成 ConfigError。
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(name="mutated", variants={"control": {"max_tokens": 1000}})
    spec.variants["control"]["not_a_field"] = 1
    with pytest.raises(ConfigError, match="字段覆盖非法"):
        assigner.apply(ContextConfig(), spec, "session-1")


def test_spec_rejects_unknown_override_field():
    with pytest.raises(ConfigError, match="不可覆盖"):
        ExperimentSpec(name="bad", variants={"control": {"not_a_field": 1}})


def test_spec_rejects_empty_variants():
    with pytest.raises(ConfigError, match="variants 不能为空"):
        ExperimentSpec(name="empty", variants={})


def test_spec_rejects_empty_name():
    # E1：__post_init__ 的 name 非空分支原先零覆盖
    with pytest.raises(ConfigError, match="name 不能为空"):
        ExperimentSpec(name="", variants={"control": {}})


def test_spec_rejects_weights_missing_a_variant():
    with pytest.raises(ConfigError, match="缺少变体"):
        ExperimentSpec(
            name="partial",
            variants={"control": {}, "variant_a": {}},
            weights={"control": 1.0},
        )


def test_spec_rejects_weights_extra_variant():
    # E8：多余键原先被静默忽略
    with pytest.raises(ConfigError, match="多余变体"):
        ExperimentSpec(
            name="extra",
            variants={"control": {}, "variant_a": {}},
            weights={"control": 1.0, "variant_a": 1.0, "ghost": 1.0},
        )


def test_spec_rejects_negative_weight():
    # E8：负权重原先只被「总和 > 0」间接拦住；{-0.5, 1.0} 和=0.5 会放行
    with pytest.raises(ConfigError, match="不能为负"):
        ExperimentSpec(
            name="neg",
            variants={"control": {}, "variant_a": {}},
            weights={"control": -0.5, "variant_a": 1.0},
        )


def test_assign_rejects_zero_weight_sum():
    # E2：_normalized_weights 的 total <= 0 分支原先零覆盖。
    # {0.0, 0.0} 非负、可构造，只有 assign() 时才会因总和为 0 抛「正数」。
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(
        name="zero",
        variants={"control": {}, "variant_a": {}},
        weights={"control": 0.0, "variant_a": 0.0},
    )
    with pytest.raises(ConfigError, match="正数"):
        assigner.assign(spec, "u0")


def test_zero_and_positive_weight_is_legal():
    # E2 附带：{0.0, 1.0} 总和=1 合法，且 control 权重 0 时不应被选中
    assigner = ExperimentAssigner()
    spec = ExperimentSpec(
        name="skew",
        variants={"control": {}, "variant_a": {}},
        weights={"control": 0.0, "variant_a": 1.0},
    )
    for uid in ("u0", "session-1", "anything"):
        assert assigner.assign(spec, uid) == "variant_a"


def test_assignment_matches_stable_hash_formula():
    # 按 spec §4.4 公式手算的期望变体（uv run python 跑出，非心算）：
    #   digest = sha256(f"{seed}:{spec.name}:{unit_id}")  取前 8 字节
    #   bucket = int.from_bytes(digest[:8], "big") / 2**64
    #   按 variants 键插入序累积归一化权重，bucket < cumulative 命中
    # 均匀权重下 control 累积到 0.5、variant_a 累积到 1.0。
    spec = _spec()
    assert ExperimentAssigner().assign(spec, "u0") == "control"  # bucket≈0.377
    assert ExperimentAssigner().assign(spec, "u1") == "variant_a"  # bucket≈0.964
    assert ExperimentAssigner().assign(spec, "session-42") == "variant_a"  # ≈0.542

    # 换 seed 后至少一个 unit_id 的期望变体不同（与 E3 向量断言互补）
    assert ExperimentAssigner().assign(spec, "u3") == "variant_a"  # ≈0.572
    assert ExperimentAssigner(seed="seed-b").assign(spec, "u3") == "control"  # ≈0.056

    # 权重 3:1 → 归一化后 control [0, 0.75)、variant_a [0.75, 1.0)
    weighted = ExperimentSpec(
        name="weighted",
        variants={"control": {}, "variant_a": {}},
        weights={"control": 3.0, "variant_a": 1.0},
    )
    assert ExperimentAssigner().assign(weighted, "u0") == "control"  # bucket≈0.020
    assert ExperimentAssigner().assign(weighted, "u1") == "variant_a"  # ≈0.933

    # 极端权重：control=0.0 → 归一化后 control 累积区间为空，恒返回 variant_a
    extreme = ExperimentSpec(
        name="extreme",
        variants={"control": {}, "variant_a": {}},
        weights={"control": 0.0, "variant_a": 1.0},
    )
    for uid in ("anything", "u0", "session-1"):
        assert ExperimentAssigner().assign(extreme, uid) == "variant_a"


def test_config_accepts_valid_experiment_spec():
    # E4：只有负例时，isinstance 校验被改成恒抛也能绿。补正例钉住「合法放行」。
    spec = _spec()
    config = ContextConfig(experiment=spec)
    assert config.experiment is spec


def test_config_rejects_non_spec_experiment():
    """experiment 必须是 ExperimentSpec 实例，None 表示不做实验"""
    with pytest.raises(ConfigError, match="ExperimentSpec"):
        ContextConfig(experiment="not-a-spec")
