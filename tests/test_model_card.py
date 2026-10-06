"""M8：声明式 YAML ModelCard —— 卡片校验、加载器与注册表改造（全部离线，不发网络请求）"""

import pytest

from hello_agents.model import (
    Capabilities,
    MissingAPIKeyError,
    ModelCard,
    ModelCardError,
    Provider,
    UnknownModelError,
    _registry,
    get_model_config,
)
from hello_agents.model.providers import build_model

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

MISSING_NAME_YAML = """\
provider: deepseek
base_url: https://x
context_size: 1
api_key_env: K
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


def test_capabilities_defaults_are_conservative():
    """没实测声明就默认不支持：只有 tool_calls 默认 True。"""
    caps = Capabilities()
    assert caps.thinking is False
    assert caps.tool_calls is True
    assert caps.native_json_schema is False


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("yaml 语法错", "provider: deepseek\nname: [未闭合\n"),
        ("缺必填字段 name", MISSING_NAME_YAML),
        (
            "context_size 非正",
            DEEPSEEK_YAML.replace("context_size: 1000000", "context_size: 0"),
        ),
        (
            "output_size 非正",
            DEEPSEEK_YAML.replace("output_size: 384000", "output_size: -1"),
        ),
        (
            "未知 provider",
            DEEPSEEK_YAML.replace("provider: deepseek", "provider: openai"),
        ),
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


class _FakeResource:
    """替身：只实现 `_collect` 用到的 `.name` 与 `.read_text()`。"""

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


def test_collect_skips_non_yaml_and_keys_by_provider_name():
    cards = _registry._collect(
        [
            _FakeResource("__init__.py", ""),
            _FakeResource("deepseek.yaml", DEEPSEEK_YAML),
        ]
    )
    assert set(cards) == {"deepseek:deepseek-flash"}
    assert cards["deepseek:deepseek-flash"].context_size == 1_000_000


def test_collect_reports_file_name_on_bad_card():
    with pytest.raises(ModelCardError) as exc:
        _registry._collect([_FakeResource("broken.yaml", "provider: nope\n")])
    assert "broken.yaml" in str(exc.value)


def test_collect_rejects_duplicate_key():
    with pytest.raises(ModelCardError) as exc:
        _registry._collect(
            [
                _FakeResource("deepseek.yaml", DEEPSEEK_YAML),
                _FakeResource("deepseek-copy.yaml", DEEPSEEK_YAML),
            ]
        )
    assert "deepseek:deepseek-flash" in str(exc.value)


def test_get_cards_discovers_all_shipped_cards():
    cards = _registry.get_cards()
    assert set(cards) == {
        "dashscope:qwen3.7-plus",
        "deepseek:deepseek-flash",
        "zhipu:glm-5.2",
    }


def test_get_cards_loads_only_once(monkeypatch):
    calls = []

    def fake_load():
        calls.append(1)
        return {}

    monkeypatch.setattr(_registry, "_load_cards", fake_load)
    _registry.get_cards.cache_clear()
    try:
        _registry.get_cards()
        _registry.get_cards()
        assert len(calls) == 1
    finally:
        _registry.get_cards.cache_clear()


def test_get_model_config_defaults_to_provider_card():
    cfg = get_model_config(Provider.DEEPSEEK)
    assert cfg.model == "deepseek-flash"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.output_size == 384_000


def test_get_model_config_unknown_model_lists_available_cards():
    with pytest.raises(UnknownModelError) as exc:
        get_model_config(Provider.DEEPSEEK, "no-such-model")
    msg = str(exc.value)
    assert "deepseek:no-such-model" in msg
    assert "deepseek:deepseek-flash" in msg


def test_build_model_unknown_model_raises_before_touching_credentials():
    """未知模型必须在读环境变量/建 client 之前就报错。"""
    with pytest.raises(UnknownModelError):
        build_model("zhipu:no-such-model")


def test_get_api_key_uses_env_var_declared_by_card(monkeypatch):
    monkeypatch.setattr(_registry, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-card")
    assert _registry.get_api_key(Provider.DEEPSEEK) == "sk-from-card"


def test_get_api_key_missing_names_the_card_env_var(monkeypatch):
    monkeypatch.setattr(_registry, "load_dotenv", lambda: None)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError) as exc:
        _registry.get_api_key(Provider.ZHIPU)
    assert "ZHIPU_API_KEY" in str(exc.value)
