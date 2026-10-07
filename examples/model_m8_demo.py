"""Model M8 示例：声明式 YAML 卡片 —— 配置与代码分离，全部离线

四部分，都不发网络请求、不需要 API key：

1. **卡片事实表**——把随包发布的每张卡片（`provider:name` / `base_url` /
   `context_size` / `output_size` / 能力位 / 凭据来自哪个环境变量）打成一张表；
2. **加一个模型 = 加一个 YAML**——现场把一段 YAML 文本交给 `ModelCard.from_yaml`，
   得到卡片、转成 `ModelConfig`，全程不碰任何 Python；
3. **坏卡片在加载期就响**——六类坏输入各打印一条**带文件名**的报错，包括
   「误把 API key 本体写进 YAML」和「同一字段写了两遍」；
4. **未知模型报出可用卡片**——`build_model("deepseek:no-such-model")` 会列出
   所有可用卡片，而不是静默回落到默认配置。

**为什么这个 demo 不联网**：M8 的产物是**配置层**，它的行为（加载、校验、查表、
报错）本来就不依赖网络。真实的端到端调用由 M3–M7 的 demo 与
`RUN_MODEL_E2E=1` 的 e2e 套件覆盖——本 demo 的价值是让你**零成本**看清
「一张卡片是怎么变成一个可调用模型的」。

用法：
    python examples/model_m8_demo.py
"""

import textwrap

from hello_agents.model import ModelCard, ModelCardError, UnknownModelError
from hello_agents.model._registry import _collect, get_cards
from hello_agents.model.providers import build_model

NEW_MODEL_YAML = """\
provider: deepseek
name: deepseek-next
base_url: https://api.deepseek.com
context_size: 2000000
output_size: 512000
capabilities:
  thinking: true
  tool_calls: true
  native_json_schema: true
api_key_env: DEEPSEEK_API_KEY
"""

_VALID_YAML = """\
provider: deepseek
name: deepseek-flash
base_url: https://api.deepseek.com
context_size: 1000000
api_key_env: DEEPSEEK_API_KEY
"""

BAD_CARDS: list[tuple[str, str]] = [
    ("YAML 语法错", "provider: deepseek\nname: [未闭合\n"),
    ("缺必填字段", "provider: deepseek\nbase_url: https://x\n"),
    ("未知 provider", _VALID_YAML.replace("provider: deepseek", "provider: openai")),
    ("字段类型错", _VALID_YAML.replace("context_size: 1000000", "context_size: 很大")),
    ("多写字段（误写 key 本体）", _VALID_YAML + "api_key: sk-should-not-be-here\n"),
    ("同一字段写了两遍", _VALID_YAML + "context_size: 999\n"),
]


class _NamedText:
    """替身：模拟 `importlib.resources` 交给加载器的一个包内资源。

    `ModelCard.from_yaml` 只拿到文本、不知道出处，所以「哪张卡坏了」这个文件名
    是**加载器**补上的——这里复现的正是用户真正看到的那条消息。
    """

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self._text = text

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


def part1_card_table() -> None:
    """随包发布的卡片就是全部「事实」，一张表看完。"""
    cards = get_cards()
    header = (
        f"{'provider:name':<26} {'context':<9} {'output':<8} "
        f"{'think':<6} {'tools':<6} {'json_schema':<12} api_key_env"
    )
    print(header)
    print("-" * len(header))

    for key in sorted(cards):
        card = cards[key]
        caps = card.capabilities
        output = "-" if card.output_size is None else str(card.output_size)
        print(
            f"{key:<26} {card.context_size:<9} {output:<8} "
            f"{caps.thinking!s:<6} {caps.tool_calls!s:<6} "
            f"{caps.native_json_schema!s:<12} {card.api_key_env}"
        )

    print()
    print(
        f"共 {len(cards)} 张卡片。注意 output_size 为 '-' 的两张：没有实测值就不声明，"
    )
    print("不臆造数字——拿到实测值后往对应 YAML 加一行即可，不碰 Python。")


def part2_add_model_is_just_yaml() -> None:
    """新增一个模型 = 新增一个 YAML 文件，不改任何 Python。"""
    print("把下面这段 YAML 存成 providers/_models/deepseek-next.yaml，它就进注册表了：")
    print(textwrap.indent(NEW_MODEL_YAML, "    "))

    card = ModelCard.from_yaml(NEW_MODEL_YAML)
    cfg = card.to_config()
    print(f"卡片         : {card.provider.value}:{card.name}")
    print(f"→ ModelConfig: model={cfg.model} base_url={cfg.base_url}")
    print(
        f"               context_size={cfg.context_size} output_size={cfg.output_size}"
    )
    print(
        f"能力位映射    : thinking={cfg.supports_thinking} "
        f"tool_calls={cfg.supports_tool_calls} "
        f"native_json_schema={cfg.supports_native_json_schema}"
    )
    print(
        "（这一步没有任何模型专属代码：_model_card.py / _registry.py 一行都不用动。）"
    )


def part3_bad_cards_fail_at_load_time() -> None:
    """坏卡片必须在加载期就响，而且要点名是哪张卡。

    报错故意打全：pydantic 的 `N validation errors` 只是第一行，**哪个字段错了**
    在后面的行里。demo 的价值就在这些细节，截断掉等于没演。
    """
    for label, text in BAD_CARDS:
        print(f"--- {label} ---")
        try:
            _collect([_NamedText("my-card.yaml", text)])
        except ModelCardError as exc:
            lines = str(exc).splitlines()
            for line in lines[:6]:
                print(f"    {line}")
            if len(lines) > 6:
                print(f"    ...（共 {len(lines)} 行）")
        else:
            print("    !! 没有报错（不应该）")
        print()


def part4_unknown_model_lists_available_cards() -> None:
    """查不到就报错并列出可用卡片，不静默回落到默认配置。"""
    for spec in ("deepseek:no-such-model", "openai:gpt-4"):
        try:
            build_model(spec)
        except (UnknownModelError, ValueError) as exc:
            print(f"build_model({spec!r})")
            print(f"  -> {type(exc).__name__}: {exc}")
        else:
            print(f"build_model({spec!r}) -> !! 没有报错（不应该）")

    print()
    print("注意第一条报错发生在**读环境变量之前**——未知模型不会白白消耗一次鉴权。")


def main() -> None:
    print("=" * 92)
    print("[1] 随包发布的卡片（全部事实都在 YAML 里）")
    part1_card_table()

    print("=" * 92)
    print("[2] 加一个模型 = 加一个 YAML")
    part2_add_model_is_just_yaml()

    print("=" * 92)
    print("[3] 坏卡片在加载期就响（报错带文件名）")
    part3_bad_cards_fail_at_load_time()

    print("=" * 92)
    print("[4] 未知模型报出可用卡片")
    part4_unknown_model_lists_available_cards()

    print("=" * 92)
    print("ALL PARTS DONE")


if __name__ == "__main__":
    main()
