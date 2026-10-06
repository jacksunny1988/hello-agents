"""模型卡片：一份 YAML 文本 → 校验后的 `ModelCard` → 现有 `ModelConfig`。

这里只做「文本 → 数据」的转换与校验，**不读文件**——扫描 `_models/*.yaml`
是 `_registry.py` 里加载器的职责。这样单测可以直接喂字符串。

与 `ModelConfig` 的分工：`ModelCard` 是「模型是什么」的事实声明（含
`output_size`、能力位、凭据来自哪个环境变量），`ModelConfig` 是调用链
（`_base.py` / `_openai_compat.py`）实际消费的配置。`to_config()` 是两者
之间唯一的适配点，M3–M7 的调用链因此完全不用改。
"""

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ._registry import ModelConfig, Provider


class ModelCardError(RuntimeError):
    """卡片加载失败：YAML 语法错、字段缺失/类型错、未知 provider、多余字段。"""


class _UniqueKeyLoader(yaml.SafeLoader):
    """`SafeLoader` + 「同一映射内重复 key 报错」。

    PyYAML 默认对
        context_size: 1000000
        context_size: 999
    这种情况静默取**最后一个**。卡片是配置，复制粘贴写重了必须响——否则
    「改错了地方」看起来就像「改了没生效」，正是本设计要消灭的静默失效。
    """

    def construct_mapping(self, node, deep=False):
        seen: list = []
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ModelCardError(f"重复的字段 {key!r}")
            seen.append(key)
        return super().construct_mapping(node, deep)


class Capabilities(BaseModel):
    """模型能力位。

    默认值取「保守 + 兼容现状」：`tool_calls` 默认 True（三家都支持），
    另两项默认 False（没实测声明就不敢说支持）。
    """

    model_config = ConfigDict(extra="forbid")

    thinking: bool = False
    tool_calls: bool = True
    native_json_schema: bool = False


class ModelCard(BaseModel):
    """一张模型卡片，对应 `_models/` 下的一个 YAML 文件。

    `extra="forbid"` 是有意的：多写的字段（最典型是把 API key 本体写进
    YAML）会在加载期直接报错，而不是被静默忽略——凭据只能通过
    `api_key_env` 指向环境变量。
    """

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    context_size: int = Field(gt=0)
    output_size: int | None = Field(default=None, gt=0)
    capabilities: Capabilities = Capabilities()
    api_key_env: str = Field(min_length=1)

    @classmethod
    def from_yaml(cls, text: str) -> "ModelCard":
        """把 YAML 文本解析成卡片；任何问题都包装成 `ModelCardError`。

        文件名的补充由调用方（加载器）负责——本方法只拿到文本，不知道出处。
        """
        try:
            data = yaml.load(text, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise ModelCardError(f"YAML 语法错误：{exc}") from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ModelCardError(f"字段校验失败：{exc}") from exc

    def to_config(self) -> ModelConfig:
        """卡片 → 现有 `ModelConfig`，字段一一对应、无损。"""
        return ModelConfig(
            provider=self.provider,
            base_url=self.base_url,
            model=self.name,
            context_size=self.context_size,
            output_size=self.output_size,
            supports_thinking=self.capabilities.thinking,
            supports_tool_calls=self.capabilities.tool_calls,
            supports_native_json_schema=self.capabilities.native_json_schema,
        )
