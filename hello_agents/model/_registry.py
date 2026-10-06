import os
from enum import StrEnum
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterable
    from importlib.resources.abc import Traversable

    from ._model_card import ModelCard


class Provider(StrEnum):
    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"


class ModelConfig(BaseModel):
    provider: Provider
    base_url: str
    model: str
    context_size: int
    output_size: int | None = None  # 最大输出 token；卡片未实测声明时为 None
    supports_thinking: bool = False
    supports_tool_calls: bool = True
    supports_native_json_schema: bool = False


# 模型事实（base_url / context_size / 能力位 / 凭据环境变量名）不在这里硬编码，
# 而在 `providers/_models/*.yaml` 的卡片里声明——见本模块的 `get_cards`。
# 原先那个 `REGISTRY` 字典（以及 `_ENV_VAR`）已删除，实测结论的注释随字段
# 搬进了对应 YAML。


class MissingAPIKeyError(RuntimeError):
    """环境变量里没有该 provider 的 API key。"""


class UnknownModelError(RuntimeError):
    """spec 指向的卡片不存在（`get_cards()` 里没有这个 key）。"""


def get_card(provider: Provider, model: str | None = None) -> "ModelCard":
    """取卡片：给了 `model` 就精确查 `provider:name`，否则取该 provider 的卡。

    目前一 provider 一卡；万一将来同名多卡，按 name 排序取第一张，保证结果确定。
    """
    cards = get_cards()
    if model is not None:
        key = f"{provider.value}:{model}"
        if key not in cards:
            available = "、".join(sorted(cards)) or "（无）"
            raise UnknownModelError(f"没有模型卡片 {key!r}；可用：{available}")
        return cards[key]

    matches = sorted(
        (c for c in cards.values() if c.provider == provider), key=lambda c: c.name
    )
    if not matches:
        available = "、".join(sorted(cards)) or "（无）"
        raise UnknownModelError(
            f"provider {provider.value!r} 没有模型卡片；可用：{available}"
        )
    return matches[0]


def get_api_key(provider: Provider) -> str:
    load_dotenv()
    var = get_card(provider).api_key_env
    key = os.environ.get(var)
    if not key:
        raise MissingAPIKeyError(
            f"缺少环境变量 {var}（provider={provider.value}）：请在 .env 中配置后重试"
        )
    return key


def get_model_config(
    provider: Provider = Provider.DEEPSEEK, model: str | None = None
) -> ModelConfig:
    return get_card(provider, model).to_config()


def parse_spec(spec: str) -> tuple[Provider, str | None]:
    name, _, model = spec.partition(":")
    try:
        provider = Provider(name.strip())
    except ValueError:
        valid = "、".join(p.value for p in Provider)
        raise ValueError(f"未知 provider {name.strip()!r}，可选：{valid}") from None
    return provider, model.strip() or None


def _collect(resources: "Iterable[Traversable]") -> "dict[str, ModelCard]":
    """把一组包内资源收成 `"provider:name" → ModelCard`。

    抽成纯函数（而不是直接写在 `_load_cards` 里）是为了能离线单测：喂几个
    带 `.name` / `.read_text()` 的替身就能覆盖「过滤非 YAML」「报错带文件名」
    「重复 key」三种情形，不用碰真实包目录。

    `_model_card` 只能在函数内延迟导入：它顶层要 `from ._registry import
    ModelConfig, Provider`，而本模块又要用 `ModelCard`。若在**顶层**导入，
    「先 `import hello_agents.model._model_card`」这条路径会拿到一个只执行到
    一半的 `_registry`（`ModelCard` 尚未定义）→ ImportError。放进函数体后，
    调用时本模块早已执行完毕，环彻底断开。
    """
    from ._model_card import ModelCard, ModelCardError

    cards: dict[str, ModelCard] = {}
    for resource in resources:
        if not resource.name.endswith((".yaml", ".yml")):
            continue
        try:
            text = resource.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # 卡片存成了 GBK 之类：必须点名是哪张卡，否则「哪坏了」无从查起。
            raise ModelCardError(
                f"卡片 {resource.name} 不是 UTF-8 编码：{exc}"
            ) from exc
        try:
            card = ModelCard.from_yaml(text)
        except ModelCardError as exc:
            raise ModelCardError(f"卡片 {resource.name} 加载失败：{exc}") from exc
        key = f"{card.provider.value}:{card.name}"
        if key in cards:
            # 静默覆盖会让「改错文件」看起来像没生效，宁可加载期就炸。
            raise ModelCardError(f"重复的卡片 key {key!r}（来自 {resource.name}）")
        cards[key] = card
    return cards


def _load_cards() -> "dict[str, ModelCard]":
    """扫描包内 `_models/*.yaml`（每次都真扫；缓存由 `get_cards` 负责）。"""
    pkg = files("hello_agents.model.providers._models")
    # 排序只为让加载顺序确定（进而让「取该 provider 的第一张卡」有确定结果）。
    return _collect(sorted(pkg.iterdir(), key=lambda r: r.name))


@lru_cache(maxsize=1)
def get_cards() -> "dict[str, ModelCard]":
    """包内 `_models/*.yaml` 的注册表，key 为 `"provider:name"`。

    `lru_cache` 保证整个进程只扫描一次包资源；测试要重置用
    `get_cards.cache_clear()`。
    """
    return _load_cards()


def build_client(cfg: ModelConfig) -> AsyncOpenAI:
    # max_retries=0：关掉 SDK 自带的重试（默认 2 次，会重试 408/409/429/5xx，
    # 且带指数退避）。重试统一交给 ChatModelBase._with_retry——两层叠加会让
    # 实际请求次数不可预期（最坏 3×3=9 次），重试语义也没法单测。
    return AsyncOpenAI(
        api_key=get_api_key(cfg.provider),
        base_url=cfg.base_url,
        max_retries=0,
    )


def get_client(spec: str) -> AsyncOpenAI:
    provider, model = parse_spec(spec)
    return build_client(get_model_config(provider, model))
