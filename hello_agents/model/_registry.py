import os
from enum import StrEnum

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel


class Provider(StrEnum):
    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"


class ModelConfig(BaseModel):
    provider: Provider
    base_url: str
    model: str
    context_size: int
    supports_thinking: bool = False
    supports_tool_calls: bool = True
    supports_native_json_schema: bool = False


REGISTRY: dict[Provider, ModelConfig] = {
    # 详细参考https://bailian.console.aliyun.com/cn-beijing/model/market/detail/qwen3.7-plus
    Provider.DASHSCOPE: ModelConfig(
        provider=Provider.DASHSCOPE,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-plus",
        context_size=1_000_000,
        supports_thinking=True,
        supports_native_json_schema=False,
    ),
    # 详细信息参考：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
    Provider.DEEPSEEK: ModelConfig(
        provider=Provider.DEEPSEEK,
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        context_size=1_000_000,
        supports_thinking=True,
        supports_native_json_schema=False,
    ),
    # 详细信息参考：https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2
    Provider.ZHIPU: ModelConfig(
        provider=Provider.ZHIPU,
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        model="glm-5.2",
        context_size=1_000_000,
        supports_thinking=True,
        supports_native_json_schema=False,
    ),
}

_ENV_VAR: dict[Provider, str] = {
    Provider.DASHSCOPE: "DASHSCOPE_API_KEY",
    Provider.DEEPSEEK: "DEEPSEEK_API_KEY",
    Provider.ZHIPU: "ZHIPU_API_KEY",
}


class MissingAPIKeyError(RuntimeError):
    """环境变量里没有该 provider 的 API key。"""


def get_api_key(provider: Provider) -> str:
    load_dotenv()
    var = _ENV_VAR[provider]
    key = os.environ.get(var)
    if not key:
        raise MissingAPIKeyError(
            f"缺少环境变量 {var}（provider={provider.value}）：请在 .env 中配置后重试"
        )
    return key


def get_model_config(
    provider: Provider = Provider.DEEPSEEK, model: str | None = None
) -> ModelConfig:
    cfg = REGISTRY[provider]
    return cfg if model is None else cfg.model_copy(update={"model": model})


def parse_spec(spec: str) -> tuple[Provider, str | None]:
    name, _, model = spec.partition(":")
    try:
        provider = Provider(name.strip())
    except ValueError:
        valid = "、".join(p.value for p in Provider)
        raise ValueError(f"未知 provider {name.strip()!r}，可选：{valid}") from None
    return provider, model.strip() or None


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
