# M8 声明式 YAML ModelCard 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `_registry.py` 里硬编码的 `REGISTRY` 换成「一模型一个 YAML 卡片 + 通用加载器」，实现配置与代码分离，同时保证 M3–M7 的公开接口与调用方式一字不改。

**Architecture:** `_model_card.py` 只负责「YAML 文本 → 校验后的 `ModelCard` → `ModelConfig`」（纯数据模型，不碰文件系统）；`_registry.py` 负责用 `importlib.resources` 扫描包内 `_models/*.yaml`、缓存成 `provider:name → ModelCard` 注册表，并把 `get_model_config` / `get_api_key` 改成查卡片。`build_model` 一行不改——它调用的 `parse_spec → get_model_config → build_client` 全部保持原签名。

**Tech Stack:** Python 3.13、Pydantic v2、PyYAML 6.0.3（已在 `pyproject.toml`）、`importlib.resources`、pytest（`asyncio_mode = "auto"`）。

**Spec:** `docs/model/08-milestone-8-model-card.md`

## Global Constraints

- **公开接口不得破坏**：`hello_agents.model` 导出的 `Provider` / `ModelConfig` / `build_client` / `get_api_key` / `get_client` / `get_model_config` / `parse_spec` / `MissingAPIKeyError` 签名与语义保持可用；`build_model(spec, stream=, max_retries=, retry_delay=)` 一行不改。
- **凭据只走环境变量**：YAML 里只写 `api_key_env`（环境变量**名**），绝不写 key 本体。卡片模型用 `extra="forbid"` 在加载期把多写的字段（例如误加的 `api_key:`）直接拒掉。
- **只用 `importlib.resources` 读包内资源**，不得用 `open("相对路径")`——安装成 wheel 后仍须可加载。
- **卡片加载只发生一次**：用 `functools.lru_cache(maxsize=1)` 缓存，且导入 `hello_agents.model` 时不得触发任何文件 I/O。
- **测试命令**（本机 `python3` 是 Store 桩，必须用项目 venv）：`.venv/Scripts/python.exe -m pytest`
- **提交纪律**：工作区里有与 M8 无关的未提交改动（`hello_agents/core/message.py`、`hello_agents/model/_tool.py`、`.gitignore`、`pyproject.toml` 的 pyyaml、`uv.lock`、`docs/model/02-milestone-2-registry.md`）。**每个 commit 只 `git add` 本任务列出的文件，禁止 `git add -A` / `git add .`。**
- **回归基线**：改动前全量 `pytest` = `561 passed, 1 failed, 9 skipped`。唯一失败是 `tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing`，**与 model 层无关的既有失败**，改动后必须仍是这一个失败。

## Review Focus

以下五类是 spec 隐含、但最容易在真实使用中咬人的输入/失败模式，各由对应任务用测试钉住：

1. **空文件 / 缺字段的 YAML** → 必须在**加载期**就抛出带文件名的 `ModelCardError`，而不是等到调用模型时才炸。（Task 1 + Task 2）
2. **未知 model 名（`deepseek:no-such-model`）** → 必须报明确错误并列出所有可用卡片，而不是静默回落到默认配置。（Task 3）
3. **包安装后卡片仍能被发现** → `_models` 必须是包（有 `__init__.py`），加载走 `importlib.resources`。（Task 2）
4. **`context_size` 为 0/负数、`provider` 不在枚举内** → 加载期拒绝。（Task 1）
5. **缺 API key** → `MissingAPIKeyError` 的消息仍须点名正确的环境变量（现在这个名字来自卡片而非硬编码表）。（Task 3）

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `hello_agents/model/_model_card.py` | 新建（当前是空占位） | `Capabilities` / `ModelCard` / `ModelCardError`：YAML 文本 → 校验 → `ModelConfig`。**不读文件**。 |
| `hello_agents/model/providers/_models/__init__.py` | 新建 | 让 `_models` 成为包，`importlib.resources.files()` 才能按包内资源访问。 |
| `hello_agents/model/providers/_models/{dashscope,deepseek,zhipu}.yaml` | 填充（当前是空占位） | 三张卡片的事实声明（含 M7 实测结论注释）。 |
| `hello_agents/model/_registry.py` | 修改 | 删 `REGISTRY`；加卡片加载器 + 缓存；`get_model_config` / `get_api_key` 改为查卡片；加 `UnknownModelError`。 |
| `hello_agents/model/__init__.py` | 修改 | 导出 `Capabilities` / `ModelCard` / `ModelCardError` / `UnknownModelError`。 |
| `tests/test_model_card.py` | 新建 | M8 全部离线测试。 |
| `docs/model/08-milestone-8-model-card.md` | 修改 | 修正「client 单例缓存」的错误措辞；补 `output_size` 可选说明。 |

---

## Task 1: 卡片模型（`ModelCard` / `Capabilities` / `ModelCardError`）

**Files:**
- Create: `hello_agents/model/_model_card.py`
- Modify: `hello_agents/model/_registry.py`（`ModelConfig` 增加可选字段 `output_size`）
- Test: `tests/test_model_card.py`

**Interfaces:**
- Consumes: `hello_agents.model._registry` 的 `Provider`（StrEnum）与 `ModelConfig`（Pydantic）。
- Produces:
  - `class Capabilities(BaseModel)`：字段 `thinking: bool = False`、`tool_calls: bool = True`、`native_json_schema: bool = False`，`extra="forbid"`。
  - `class ModelCard(BaseModel)`：字段 `provider: Provider`、`name: str`、`base_url: str`、`context_size: int`（`gt=0`）、`output_size: int | None = None`（`gt=0`）、`capabilities: Capabilities = Capabilities()`、`api_key_env: str`，`extra="forbid"`。
  - `ModelCard.from_yaml(text: str) -> ModelCard`（类方法，抛 `ModelCardError`）。
  - `ModelCard.to_config() -> ModelConfig`。
  - `class ModelCardError(RuntimeError)`。
  - `ModelConfig.output_size: int | None = None`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_model_card.py`：

```python
"""M8：声明式 YAML ModelCard —— 卡片校验、加载器与注册表改造（全部离线，不发网络请求）"""

import pytest

from hello_agents.model import ModelCard, ModelCardError, Provider

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q`
Expected: FAIL —— `ImportError: cannot import name 'ModelCard' from 'hello_agents.model'`

- [ ] **Step 3: 给 `ModelConfig` 加可选字段**

`hello_agents/model/_registry.py`，把 `ModelConfig` 改成（只加一行）：

```python
class ModelConfig(BaseModel):
    provider: Provider
    base_url: str
    model: str
    context_size: int
    output_size: int | None = None  # 最大输出 token；卡片未实测声明时为 None
    supports_thinking: bool = False
    supports_tool_calls: bool = True
    supports_native_json_schema: bool = False
```

- [ ] **Step 4: 实现 `_model_card.py`**

`hello_agents/model/_model_card.py`：

```python
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
    name: str
    base_url: str
    context_size: int = Field(gt=0)
    output_size: int | None = Field(default=None, gt=0)
    capabilities: Capabilities = Capabilities()
    api_key_env: str

    @classmethod
    def from_yaml(cls, text: str) -> "ModelCard":
        """把 YAML 文本解析成卡片；任何问题都包装成 `ModelCardError`。

        文件名的补充由调用方（加载器）负责——本方法只拿到文本，不知道出处。
        """
        try:
            data = yaml.safe_load(text)
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q`
Expected: PASS（7 个用例：2 个正向 + 6 个参数化中的 6 个 + 1 个多余字段 = 参数化按 6 计，共 9 个）

> 注：此时 `hello_agents/model/__init__.py` 还没导出 `ModelCard`，Step 1 的
> `from hello_agents.model import ModelCard` 仍会失败。**本步先临时把测试
> 导入改成** `from hello_agents.model._model_card import ModelCard, ModelCardError`
> 与 `from hello_agents.model import Provider`，Task 4 再换回公开导入。

- [ ] **Step 6: 提交**

```bash
git add hello_agents/model/_model_card.py hello_agents/model/_registry.py tests/test_model_card.py
git commit -m "feat(model): M8 卡片模型（ModelCard/Capabilities + 校验错误包装）"
```

---

## Task 2: 三份 YAML 卡片 + `_models` 包 + 加载器

**Files:**
- Create: `hello_agents/model/providers/_models/__init__.py`
- Modify: `hello_agents/model/providers/_models/dashscope.yaml`、`deepseek.yaml`、`zhipu.yaml`（当前是 0 字节空文件）
- Modify: `hello_agents/model/_registry.py`（新增加载器）
- Test: `tests/test_model_card.py`

**Interfaces:**
- Consumes: Task 1 的 `ModelCard.from_yaml(text) -> ModelCard`、`ModelCardError`。
- Produces:
  - `_registry._collect(resources: Iterable[Traversable]) -> dict[str, ModelCard]`（纯函数，便于离线测试）。
  - `_registry.get_cards() -> dict[str, ModelCard]`（`@lru_cache(maxsize=1)`，key 为 `"provider:name"`）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_model_card.py` 末尾追加：

```python
from hello_agents.model import _registry


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q -k "collect or get_cards"`
Expected: FAIL —— `AttributeError: module 'hello_agents.model._registry' has no attribute '_collect'`

- [ ] **Step 3: 让 `_models` 成为包**

创建 `hello_agents/model/providers/_models/__init__.py`：

```python
"""模型卡片数据目录（一模型一个 YAML）。

这里没有 Python 代码，只有 `*.yaml`。`__init__.py` 的存在是为了让它成为一个
**包**，从而能用 `importlib.resources.files("...providers._models")` 按包内资源
读取——安装成 wheel 后依然可访问，不依赖「当前工作目录」。
"""
```

- [ ] **Step 4: 写三份 YAML 卡片**

`hello_agents/model/providers/_models/deepseek.yaml`：

```yaml
# DeepSeek 官方 OpenAI 兼容端点。规格参考：
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
provider: deepseek
name: deepseek-flash
base_url: https://api.deepseek.com

context_size: 1000000
output_size: 384000        # 最大输出 token

capabilities:
  thinking: true
  tool_calls: true
  # 实测（2026-10-01，examples/model_m7_probe.py）：端点直接回 400
  # "This response_format type is unavailable now"。json_object 可用，
  # 但 prompt 里必须出现 json 一词，否则同样 400。
  native_json_schema: false

# 凭据：只写环境变量名，绝不写 key 本体
api_key_env: DEEPSEEK_API_KEY
```

`hello_agents/model/providers/_models/dashscope.yaml`：

```yaml
# 阿里云百炼 DashScope 的 OpenAI 兼容端点。参考：
# https://bailian.console.aliyun.com/cn-beijing/model/market/detail/qwen3.7-plus
provider: dashscope
name: qwen3.7-plus
base_url: https://dashscope.aliyuncs.com/compatible-mode/v1

context_size: 1000000
# output_size（最大输出 token）尚无实测值，故不声明 —— 卡片里为 None。

capabilities:
  thinking: true
  tool_calls: true
  # 实测（2026-10-01）：strict json_schema 请求返回合法 JSON，字段全对。
  native_json_schema: true

# 凭据：只写环境变量名，绝不写 key 本体
api_key_env: DASHSCOPE_API_KEY
```

`hello_agents/model/providers/_models/zhipu.yaml`：

```yaml
# 智谱 GLM 的 OpenAI 兼容端点。参考：
# https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2
provider: zhipu
name: glm-5.2
base_url: https://open.bigmodel.cn/api/paas/v4/

context_size: 1000000
# output_size（最大输出 token）尚无实测值，故不声明 —— 卡片里为 None。

capabilities:
  thinking: true
  tool_calls: true
  # 实测（2026-10-01）：**失败方式很坑**——端点不报错，静默忽略 response_format，
  # 返回的还是散文。能力位按「是否真的约束了输出」判定，所以是 false。
  # 哪天它开始真遵守，重跑 examples/model_m7_probe.py 再改。
  native_json_schema: false

# 凭据：只写环境变量名，绝不写 key 本体
api_key_env: ZHIPU_API_KEY
```

- [ ] **Step 5: 在 `_registry.py` 里实现加载器**

**为什么 `_model_card` 只能在函数内延迟导入：** `_model_card` 顶层要 `from
._registry import ModelConfig, Provider`，而 `_registry` 又要用 `ModelCard`。
若 `_registry` 在顶层导入 `_model_card`，那么「先 `import
hello_agents.model._model_card`」这条路径会拿到一个**只执行到一半**的
`_registry`，`ModelCard` 尚未定义 → `ImportError`。把导入放进 `_collect()`
函数体即可彻底断环（此时 `_registry` 早已执行完毕）。因此 **`_registry.py`
顶层不得导入 `_model_card` 的任何名字**；`ModelCard` 只在 `TYPE_CHECKING`
里导入供标注用。

在 `hello_agents/model/_registry.py` 顶部补导入：

```python
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from importlib.resources.abc import Traversable

    from ._model_card import ModelCard
```

在 `build_client` **之前**插入加载器：

```python
def _collect(resources: Iterable[Traversable]) -> dict[str, ModelCard]:
    """把一组包内资源收成 `"provider:name" → ModelCard`。

    抽成纯函数（而不是直接写在 `get_cards` 里）是为了能离线单测：喂几个
    带 `.name` / `.read_text()` 的替身就能覆盖「过滤非 YAML」「报错带文件名」
    「重复 key」三种情形，不用碰真实包目录。
    """
    from ._model_card import ModelCard, ModelCardError  # 延迟导入，理由见上

    cards: dict[str, ModelCard] = {}
    for resource in resources:
        if not resource.name.endswith((".yaml", ".yml")):
            continue
        try:
            card = ModelCard.from_yaml(resource.read_text(encoding="utf-8"))
        except ModelCardError as exc:
            raise ModelCardError(f"卡片 {resource.name} 加载失败：{exc}") from exc
        key = f"{card.provider.value}:{card.name}"
        if key in cards:
            # 静默覆盖会让「改错文件」看起来像没生效，宁可加载期就炸。
            raise ModelCardError(f"重复的卡片 key {key!r}（来自 {resource.name}）")
        cards[key] = card
    return cards


@lru_cache(maxsize=1)
def get_cards() -> dict[str, ModelCard]:
    """包内 `_models/*.yaml` 的注册表，key 为 `"provider:name"`。

    `lru_cache` 保证整个进程只扫描一次包资源；测试要重置用
    `get_cards.cache_clear()`。
    """
    pkg = files("hello_agents.model.providers._models")
    # 排序只为让加载顺序确定（进而让「取该 provider 的第一张卡」有确定结果）。
    return _collect(sorted(pkg.iterdir(), key=lambda r: r.name))
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q -k "collect or get_cards"`
Expected: PASS（5 个用例）

- [ ] **Step 7: 提交**

```bash
git add hello_agents/model/providers/_models/ hello_agents/model/_registry.py tests/test_model_card.py
git commit -m "feat(model): M8 三份 YAML 卡片 + importlib.resources 加载器（带缓存）"
```

---

## Task 3: 注册表改造（查卡片 / 凭据 / 未知模型报错）

**Files:**
- Modify: `hello_agents/model/_registry.py`（删 `REGISTRY` 与 `_ENV_VAR`，加 `UnknownModelError` / `get_card`，改写 `get_model_config` / `get_api_key`）
- Test: `tests/test_model_card.py`

**Interfaces:**
- Consumes: Task 2 的 `get_cards() -> dict[str, ModelCard]`；Task 1 的 `ModelCard.to_config() -> ModelConfig`、`ModelCard.api_key_env: str`。
- Produces:
  - `class UnknownModelError(RuntimeError)`
  - `_registry.get_card(provider: Provider, model: str | None = None) -> ModelCard`
  - `get_model_config(provider: Provider = Provider.DEEPSEEK, model: str | None = None) -> ModelConfig`（签名不变，语义改为查卡片）
  - `get_api_key(provider: Provider) -> str`（签名不变，环境变量名改由卡片提供）

- [ ] **Step 1: 写失败测试**

在 `tests/test_model_card.py` 末尾追加：

```python
from hello_agents.model import (
    MissingAPIKeyError,
    UnknownModelError,
    build_model,
    get_model_config,
)


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q -k "unknown or api_key or defaults"`
Expected: FAIL —— `ImportError: cannot import name 'UnknownModelError'`

- [ ] **Step 3: 改造 `_registry.py`**

**3a.** 删除整个 `REGISTRY` 字典（原第 25–62 行）与 `_ENV_VAR` 字典（原第 64–68 行）。那两条实测结论的注释已经搬进 Task 2 的 YAML 里了。

**3b.** 在 `MissingAPIKeyError` 旁边新增：

```python
class UnknownModelError(RuntimeError):
    """spec 指向的卡片不存在（`get_cards()` 里没有这个 key）。"""
```

**3c.** 把 `get_api_key` 改成：

```python
def get_api_key(provider: Provider) -> str:
    load_dotenv()
    var = get_card(provider).api_key_env
    key = os.environ.get(var)
    if not key:
        raise MissingAPIKeyError(
            f"缺少环境变量 {var}（provider={provider.value}）：请在 .env 中配置后重试"
        )
    return key
```

**3d.** 在 `get_model_config` 之前新增 `get_card`：

```python
def get_card(provider: Provider, model: str | None = None) -> ModelCard:
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
```

**3e.** 把 `get_model_config` 改成：

```python
def get_model_config(
    provider: Provider = Provider.DEEPSEEK, model: str | None = None
) -> ModelConfig:
    return get_card(provider, model).to_config()
```

**3f.** 给 `get_card` 的返回标注补上 `ModelCard` 的 `TYPE_CHECKING` 导入（Task 2 Step 5 已建好 `if TYPE_CHECKING:` 块，往里加一行）：

```python
    from ._model_card import ModelCard
```

`parse_spec` / `build_client` / `get_client` **一行不改**。`build_model` 也一行不改——它走 `parse_spec → get_model_config → build_client`，未知模型在 `get_model_config` 就抛 `UnknownModelError`，早于 `build_client` 读环境变量。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q`
Expected: PASS（全部）

- [ ] **Step 5: 跑 M3–M7 相关回归**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_retry.py tests/test_model_tools.py tests/test_model_structured.py tests/test_model_examples.py -q`
Expected: PASS（`get_model_config` / `build_client` 的既有用例全部照旧）

- [ ] **Step 6: 提交**

```bash
git add hello_agents/model/_registry.py tests/test_model_card.py
git commit -m "refactor(model): M8 注册表改为卡片驱动（删 REGISTRY，未知模型报可用卡片）"
```

---

## Task 4: 公开导出、文档措辞、全量回归

**Files:**
- Modify: `hello_agents/model/__init__.py`
- Modify: `docs/model/08-milestone-8-model-card.md`
- Test: `tests/test_model_card.py`（把 Task 1 的临时导入换回公开导入）

**Interfaces:**
- Consumes: 前三个任务的全部产出。
- Produces: `hello_agents.model` 新增导出 `Capabilities`、`ModelCard`、`ModelCardError`、`UnknownModelError`。

- [ ] **Step 1: 把测试导入换成公开路径**

`tests/test_model_card.py` 里 Task 1 的临时导入

```python
from hello_agents.model import ModelCard, ModelCardError, Provider
from hello_agents.model._model_card import Capabilities  # 若曾用过
```

换成：

```python
from hello_agents.model import Capabilities, ModelCard, ModelCardError, Provider
```

（Task 3 追加的那段 `from hello_agents.model import (...)` 保持不动。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q`
Expected: FAIL —— `ImportError: cannot import name 'Capabilities' from 'hello_agents.model'`

- [ ] **Step 3: 扩充 `__init__.py` 导出**

在 `hello_agents/model/__init__.py` 的 `from ._base import ChatModelBase` 之后插入：

```python
from ._model_card import Capabilities, ModelCard, ModelCardError
```

把 `from ._registry import (...)` 改成：

```python
from ._registry import (
    MissingAPIKeyError,
    ModelConfig,
    Provider,
    UnknownModelError,
    build_client,
    get_api_key,
    get_client,
    get_model_config,
    parse_spec,
)
```

`__all__` 里按现有字母序插入四项：`"Capabilities"`、`"ModelCard"`、`"ModelCardError"`、`"UnknownModelError"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_model_card.py -q`
Expected: PASS

- [ ] **Step 5: 修正文档里与代码不符的两处**

`docs/model/08-milestone-8-model-card.md`：

- §2 的 YAML 示例下方，在「字段设计原则」那段前加一句：
  `> `output_size` 可选：没有实测值的模型直接省略该字段（卡片里为 `None`），不臆造数字。`
- §4 第 127 行 `- 加载结果做**模块级缓存**（只加载一次），client 仍按现有方式单例缓存。`
  改成：
  `- 加载结果做**模块级缓存**（`functools.lru_cache`，只加载一次）；client 仍按现有方式**每次新建**——`build_client` 每次返回新的 `AsyncOpenAI`，M8 不引入 client 缓存。`
- §8 第 184 行 `- [ ] 卡片加载只发生一次，client 仍单例。`
  改成：
  `- [ ] 卡片加载只发生一次（`lru_cache`）；client 仍按现有方式每次新建、无缓存。`

- [ ] **Step 6: 全量回归**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `1 failed, 561+N passed, 9 skipped`，**唯一失败仍是 `tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing`**（与 M8 无关的既有失败）。N = 本计划新增用例数。

- [ ] **Step 7: 在线 demo 冒烟（需要 API key，会产生费用）**

**先问用户是否要跑**，得到同意后：

```bash
.venv/Scripts/python.exe examples/model_m3_demo.py
.venv/Scripts/python.exe examples/model_m4_stream_demo.py
.venv/Scripts/python.exe examples/model_m6_demo.py
.venv/Scripts/python.exe examples/model_m7_demo.py
```

（`model_m5_demo.py` 含不可达端点的重试演示，耗时较长，按需跑。）
Expected: 四份 demo 的可见行为与 M7 提交时一致。

- [ ] **Step 8: 提交**

```bash
git add hello_agents/model/__init__.py tests/test_model_card.py docs/model/08-milestone-8-model-card.md
git commit -m "docs(model): M8 公开卡片类型导出 + 修正 client 缓存措辞 + 全量回归"
```

---

## Self-Review

**1. Spec coverage**

| Spec 要求 | 落在哪个任务 |
|---|---|
| §2 三份 YAML（含 M7 实测结论、只写 `api_key_env`） | Task 2 Step 4 |
| §3 `Capabilities` / `ModelCard` / `from_yaml(text)` / `to_config()` | Task 1 Step 4 |
| §3 加载错误包装成带文件名的明确异常 | Task 2 Step 5（`_collect` 的 `raise ... from exc`） |
| §4 `importlib.resources.files()` 扫描、key=`provider:name`、模块级缓存 | Task 2 Step 5 |
| §4 需要 `pyyaml` | 已在 `pyproject.toml:18`，无需动作 |
| §5 `build_model` 从卡片构造、旧调用不破坏 | Task 3 Step 3（`build_model` 不改，链路自动切到卡片） |
| §5 删/替换硬编码 `REGISTRY`；`get_model_config` 保留 | Task 3 Step 3a/3e |
| §6 加载与构造时序 | Task 2 Step 5 + Task 3 Step 3d |
| §7.5 回归 M3–M7 | Task 3 Step 5 + Task 4 Step 6/7 |
| §7.6 四类测试 | Task 1（合法/坏 YAML）、Task 2（发现/缓存/文件名）、Task 3（未知 spec 列卡片） |
| §8 「YAML 里没有 key 本体」 | Task 1 Step 1 的 `test_card_rejects_extra_fields`（`extra="forbid"`） |
| §8 「client 仍单例」 | 与代码不符 → 已按用户决策改为「每次新建」，Task 4 Step 5 修文档 |

**2. Placeholder scan**：无 TBD / TODO；每个代码步骤都给了可直接粘贴的完整代码。循环导入的处理是明确结论而非待办：`_collect` 内部延迟导入 `ModelCard, ModelCardError`，`_registry.py` 顶层不导入 `_model_card` 任何名字。

**3. Type consistency**：`from_yaml(text: str) -> ModelCard`、`to_config() -> ModelConfig`、`get_card(provider, model=None) -> ModelCard`、`get_cards() -> dict[str, ModelCard]`、`_collect(resources) -> dict[str, ModelCard]` 在四个任务间一致；`ModelConfig.output_size`（Task 1 加）与 `ModelCard.output_size`（Task 1 定义）同名同类型。

**4. Review Focus**：五条各已指派测试——①Task 1 `test_from_yaml_rejects_bad_input` 的「空文本」「缺必填字段 name」+ Task 2 `test_collect_reports_file_name_on_bad_card`；②Task 3 `test_get_model_config_unknown_model_lists_available_cards` / `test_build_model_unknown_model_raises_before_touching_credentials`；③Task 2 `test_get_cards_discovers_all_shipped_cards`（真实包资源）；④Task 1 的「context_size 非正」「未知 provider」；⑤Task 3 两个 `get_api_key` 用例。

**遗留待办（需用户提供）**：`dashscope` / `zhipu` 的 `output_size` 无实测值，卡片暂不声明（为 `None`）。拿到实测值后只需往对应 YAML 加一行 `output_size: <值>`，不碰任何 Python。
