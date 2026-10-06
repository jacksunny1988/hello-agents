"""M8：声明式 YAML ModelCard —— 卡片校验、加载器与注册表改造（全部离线，不发网络请求）"""

import pytest

from hello_agents.model import Provider
from hello_agents.model._model_card import ModelCard, ModelCardError

DEEPSEEK_YAML = """\
provider: deepseek
name: deepseek-flash
base_url: https://api.deepseek.com
context_size: 1000000
output_size: 384000
capabilities:
  thinking: true
  tool_calls: true
  native_json_schema: false
api_key_env: DEEPSEEK_API_KEY
"""

MINIMAL_YAML = """\
provider: zhipu
name: glm-5.2
base_url: https://open.bigmodel.cn/api/paas/v4/
context_size: 1000000
api_key_env: ZHIPU_API_KEY
"""


def test_card_to_config_maps_every_field():
    card = ModelCard.from_yaml(DEEPSEEK_YAML)
    cfg = card.to_config()
    assert cfg.provider is Provider.DEEPSEEK
    assert cfg.model == "deepseek-flash"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.context_size == 1_000_000
    assert cfg.output_size == 384_000
    assert cfg.supports_thinking is True
    assert cfg.supports_tool_calls is True
    assert cfg.supports_native_json_schema is False


def test_card_optional_fields_default():
    card = ModelCard.from_yaml(MINIMAL_YAML)
    assert card.output_size is None
    assert card.capabilities.thinking is False
    assert card.capabilities.tool_calls is True
    assert card.capabilities.native_json_schema is False
    assert card.to_config().output_size is None


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("yaml 语法错", "provider: deepseek\nname: [未闭合\n"),
        ("缺必填字段 name", "provider: deepseek\nbase_url: https://x\n"
                            "context_size: 1\napi_key_env: K\n"),
        ("context_size 非正", DEEPSEEK_YAML.replace("context_size: 1000000",
                                                    "context_size: 0")),
        ("output_size 非正", DEEPSEEK_YAML.replace("output_size: 384000",
                                                   "output_size: -1")),
        ("未知 provider", DEEPSEEK_YAML.replace("provider: deepseek",
                                                "provider: openai")),
        ("空文本", ""),
    ],
)
def test_from_yaml_rejects_bad_input(label, text):
    with pytest.raises(ModelCardError):
        ModelCard.from_yaml(text)


def test_card_rejects_extra_fields():
    """多写字段（典型是误把 key 本体写进 YAML）必须在加载期被拒。"""
    with pytest.raises(ModelCardError):
        ModelCard.from_yaml(DEEPSEEK_YAML + "api_key: sk-should-not-be-here\n")
