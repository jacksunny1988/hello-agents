"""压缩配置：两条比率阈值。"""

from pydantic import BaseModel, ConfigDict, Field


class ContextConfig(BaseModel):
    """上下文压缩的配置。

    两个 ratio 都乘 `model.context_size` 换算成绝对 token 数——**用比率而不是
    绝对数**，换模型（128K → 1M）时配置不用动。
    """

    model_config = ConfigDict(extra="forbid")

    trigger_ratio: float = Field(default=0.8, gt=0, lt=0.9)
    """估算 token 达到 `trigger_ratio × context_size` 就压缩。

    上限 0.9 不是随手取的：压缩**本身要花 token**——得把「system + 旧摘要 +
    待压消息 + 压缩指令」一次性喂回模型。留出 10% 才够这次"读"。
    """

    reserve_ratio: float = Field(default=0.1, gt=0, lt=0.9)
    """压缩后要保留的**预算**比例：`reserve_ratio × context_size`。

    是预算语义，不是"保留几条消息"——条数不是稳定单位（一条消息可能 3 个字，
    也可能 5 万字）。消费者是 C4 的切分算法。
    """
