# M8 声明式 YAML ModelCard —— 设计规格

> 对应需求：`docs/model/08-milestone-8-model-card.md`
> 实现计划：`docs/plans/2026-10-06-m8-model-card-plan.md`
> 编写时间：2026-10-06
> 状态：**待审核**（审核通过后才开始实施）
> 前置：M0–M7 已合入（注册表 / 调用 / 流式 / 重试取消 / 工具闭环 / 结构化输出）

---

## 1. 目标与边界

**目标**：把硬编码在 `_registry.py::REGISTRY` 里的模型事实（provider / base_url /
model 名 / context_size / 能力位 / 凭据来自哪个环境变量）搬到**一模型一个 YAML 文件**，
代码只保留「扫描包内 YAML → 校验 → 构造 `ModelConfig` → 建 client」这套与模型无关的通用逻辑。
新增或修改模型只改 YAML，不碰 Python，也不用重新理解代码结构。

**边界**：

- **这是一次内部重构，不是接口升级**。M3–M7 的公开接口与调用方式一字不改：
  `build_model(spec, stream=, max_retries=, retry_delay=)` 签名与语义不变；
  `Provider` / `ModelConfig` / `build_client` / `get_api_key` / `get_client` /
  `get_model_config` / `parse_spec` / `MissingAPIKeyError` 全部保持可用。
- **凭据仍然只从环境变量读**。YAML 里只写 `api_key_env`（环境变量**名**），
  绝不写 key 本体；并且用 `extra="forbid"` 在加载期把多写的字段直接拒掉，
  让「误把 key 写进 YAML」变成一次响亮的加载失败，而不是静默泄漏。
- **不引入新依赖**：`pyyaml>=6.0.3` 已在 `pyproject.toml:18`。
- **不做 client 缓存**（决策 1，见 §2）。
- **不做参数表单所需的元数据**：`parameter_schema` / `input_types` / `output_types` /
  `label` / `status` 这类字段是给前端动态渲染调参面板用的（按 MIME 类型显隐控件），
  与「注册一个可调用模型」无关，M8 裁剪（见 §11）。
- **不动**与 M8 无关的既有未提交改动（`hello_agents/core/message.py`、
  `hello_agents/model/_tool.py`、`.gitignore`、`uv.lock`、
  `docs/model/02-milestone-2-registry.md`）。

**已确认的两个决策**（本次澄清结论）：

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | client 是否做单例缓存 | **不做**，`build_client` 每次返回新的 `AsyncOpenAI` | 现状本就没有缓存（需求文档 §4/§8 写的「client 仍单例缓存」与代码不符）。加缓存会引入全局状态：测试里 monkeypatch 环境变量后可能拿到用旧 key 建的 client，需要额外的清理钩子。本次重构的目标是「配置与代码分离」，不是改连接生命周期——保持现状、修正文档措辞。 |
| 2 | `output_size` 是否进 `ModelConfig` | **进**，作为可选字段 `int \| None = None` | 卡片声明的最大输出 token 若无处可去就是只写不读的死数据。加可选字段是纯增量、向后兼容（现有测试都经 `get_model_config` 拿配置，无直接构造），且 M9+ 做上下文预算时可直接取用。 |

---

## 2. 模块结构

| 文件 | 动作 | 职责 | 不该做什么 |
|---|---|---|---|
| `hello_agents/model/_model_card.py` | 新建（当前是 0 字节占位） | `Capabilities` / `ModelCard` / `ModelCardError`：**YAML 文本 → 校验后的卡片 → `ModelConfig`** | 不读文件、不碰 `importlib.resources`、不知道文件叫什么 |
| `hello_agents/model/providers/_models/__init__.py` | 新建 | 让 `_models` 成为**包**，使 `importlib.resources.files("...providers._models")` 可用 | 无代码 |
| `hello_agents/model/providers/_models/{dashscope,deepseek,zhipu}.yaml` | 填充（当前是 0 字节占位） | 三张卡片的事实声明，含 M7 实测结论的注释 | 不写 key 本体、不写调用期策略（`stream` / `max_retries`） |
| `hello_agents/model/_registry.py` | 修改 | 删 `REGISTRY` / `_ENV_VAR`；加卡片加载器 + 缓存 + `UnknownModelError` / `get_card`；`get_model_config` / `get_api_key` 改为查卡片 | `parse_spec` / `build_client` / `get_client` 不动 |
| `hello_agents/model/providers/_openai_compat.py` | **不动** | `build_model` 走 `parse_spec → get_model_config → build_client`，链路自动切到卡片 | —— |
| `hello_agents/model/__init__.py` | 修改 | 新增导出 `Capabilities` / `ModelCard` / `ModelCardError` / `UnknownModelError` | 其余导出不动 |
| `tests/test_model_card.py` | 新建 | M8 全部离线测试 | 不发网络请求 |

**分层要点**：`ModelCard` 只管「文本 → 数据」，加载器只管「找到文件 → 交给 `ModelCard` → 建立索引」，
`_registry` 只管「按 spec 查卡片 → 转配置 → 取凭据 → 建 client」。三者可独立单测。

---

## 3. 数据模型

### 3.1 `Capabilities`

```python
class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thinking: bool = False
    tool_calls: bool = True
    native_json_schema: bool = False
```

默认值取「保守 + 兼容现状」：`tool_calls` 默认 `True`（三家都支持），另两项默认 `False`
（没实测声明就不声称支持）。与现有 `ModelConfig` 的三个 `supports_*` 字段一一对应。

### 3.2 `ModelCard`

```python
class ModelCard(BaseModel):
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
        try:
            data = yaml.load(text, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise ModelCardError(f"YAML 语法错误：{exc}") from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ModelCardError(f"字段校验失败：{exc}") from exc

    def to_config(self) -> ModelConfig:
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

四个设计点：

1. **解析用 `yaml.load(text, Loader=_UniqueKeyLoader)`，而 `_UniqueKeyLoader`
   继承 `yaml.SafeLoader`**：`SafeLoader` 只构造基本类型，不会因为 YAML 里写了
   `!!python/object/...` 就执行任意对象构造——卡片是配置文件、来源可能是别人提的
   PR，这个默认值必须安全。在此之上加一条：**同一映射内的重复 key 报错**。
   PyYAML 默认对
   ```yaml
   context_size: 1000000
   context_size: 999
   ```
   静默取**最后一个**；卡片是配置，复制粘贴写重了必须响，否则「改错了地方」
   看起来就像「改了没生效」——正是本设计要消灭的静默失效。
   异常分两段包装：`yaml.YAMLError`（语法）与 pydantic `ValidationError`（结构）
   都转成 `ModelCardError`，让调用方只需 catch 一种异常。
2. **`from_yaml` 接收文本而非路径**。读文件是加载器的职责；卡片只管
   「YAML 文本 → 校验后的卡片」。好处是单测可以直接喂字符串，不需要临时目录或
   真实文件。
3. **`extra="forbid"`**。多写的字段（最典型是误加 `api_key: sk-...`）在加载期
   报错而不是被静默忽略。这是「YAML 里没有 key 本体」这条约束的**机器化保证**，
   不靠人自觉。
4. **`to_config()` 是卡片与调用链之间唯一的适配点**。`model = name`、
   `capabilities.thinking → supports_thinking` 等一一映射，无损。
   因为适配点只有这一个，M3–M7 的 `_base.py` / `_openai_compat.py` 完全不用改。

### 3.3 `ModelConfig` 的唯一改动

```python
class ModelConfig(BaseModel):
    provider: Provider
    base_url: str
    model: str
    context_size: int
    output_size: int | None = None  # ← 新增（决策 2）
    supports_thinking: bool = False
    supports_tool_calls: bool = True
    supports_native_json_schema: bool = False
```

带默认值 → 纯增量、向后兼容。

### 3.4 `ModelCardError` / `UnknownModelError`

| 异常 | 位置 | 触发时机 | 语义 |
|---|---|---|---|
| `ModelCardError(RuntimeError)` | `_model_card.py` / `_registry.py` | 加载期 | YAML 语法错、字段缺失/类型错/越界/空串、未知 provider、多余字段、同一文件内重复字段、跨文件重复 `provider:name`、文件非 UTF-8。消息**必须带文件名**。 |
| `UnknownModelError(RuntimeError)` | `_registry.py` | 查询期 | spec 指向的卡片不存在。消息**必须列出所有可用卡片**。 |

两者分开是有意的：一个是「你的配置文件写坏了」（部署/开发期问题），
一个是「你调用了不存在的模型」（使用期问题），排查路径完全不同。

---

## 4. YAML 卡片规格

### 4.1 Schema

```yaml
provider: deepseek            # 必填，必须是 Provider 枚举之一
name: deepseek-flash          # 必填，spec 里冒号后的部分
base_url: https://api.deepseek.com   # 必填

context_size: 1000000         # 必填，> 0
output_size: 384000           # 可选，> 0；无实测值就省略（→ None）

capabilities:                 # 可选，缺省即 Capabilities()
  thinking: true
  tool_calls: true
  native_json_schema: false

api_key_env: DEEPSEEK_API_KEY # 必填，只写变量名
```

**字段设计原则**：只放「描述模型 / 如何找到它」的**事实**。像 `stream`、`max_retries`
这类「调用时策略」不进卡片——它们是 `build_model` 的运行参数，不是模型的固有属性。

### 4.2 三份卡片的取值来源

| 卡片 | `base_url` | `context_size` | `output_size` | `thinking` | `tool_calls` | `native_json_schema` |
|---|---|---|---|---|---|---|
| `dashscope:qwen3.7-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 1000000 | **未声明** | true | true | **true** |
| `deepseek:deepseek-flash` | `https://api.deepseek.com` | 1000000 | 384000 | true | true | **false** |
| `zhipu:glm-5.2` | `https://open.bigmodel.cn/api/paas/v4/` | 1000000 | **未声明** | true | true | **false** |

- 能力位全部沿用 M7 用 `examples/model_m7_probe.py` 实测的结论（判定口径：
  端点接受参数**且**返回合法 JSON 才算 `true`；「接受但不遵守」算 `false`）。
  三条实测结论的原始注释随字段一起搬进对应 YAML，不丢历史。
- **`output_size` 只有 deepseek 有值**（来自需求文档）。dashscope / zhipu 无实测值，
  **不臆造**——省略该字段（卡片里为 `None`），并在 YAML 里用注释说明原因。
  拿到实测值后往 YAML 加一行即可，不碰 Python。这是本规格唯一的**遗留待办**。

---

## 5. 加载器

### 5.1 用 `importlib.resources`，不用相对路径

```python
def _collect(resources: "Iterable[Traversable]") -> "dict[str, ModelCard]":
    from ._model_card import ModelCard, ModelCardError  # 延迟导入，理由见 §6

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
            raise ModelCardError(f"重复的卡片 key {key!r}（来自 {resource.name}）")
        cards[key] = card
    return cards


def _load_cards() -> "dict[str, ModelCard]":
    """扫描包内 `_models/*.yaml`（每次都真扫；缓存由 `get_cards` 负责）。"""
    pkg = files("hello_agents.model.providers._models")
    return _collect(sorted(pkg.iterdir(), key=lambda r: r.name))


@lru_cache(maxsize=1)
def get_cards() -> "dict[str, ModelCard]":
    return _load_cards()
```

> 注解写成字符串（`"dict[str, ModelCard]"`）是必须的：`Iterable` / `Traversable` /
> `ModelCard` 只在 `TYPE_CHECKING` 下导入，而 Python 默认在**运行时**求值注解，
> 不加引号会 `NameError`。用引号而不是 `from __future__ import annotations`，
> 是为了不改变本模块其它注解（含 Pydantic `ModelConfig` 字段）的求值语义。

五个要点：

1. **`files(...)` 而非 `open("相对路径")`**。相对路径依赖「当前工作目录」，
   包被 `uv/pip install` 安装后、或从别的目录运行时必然失效。
   `importlib.resources` 读的是**包内资源**，wheel 里也能访问。
   这要求 `_models/` 有 `__init__.py`（成为包）。
   **分工**：`importlib.resources` 只负责「定位并读出包内资源」，
   YAML 的**解析一律走 pyyaml**——`resource.read_text()` 拿到文本后立刻交给
   `ModelCard.from_yaml` 里的 `yaml.safe_load`，没有第二套解析路径。
2. **`_collect` 抽成纯函数**。喂几个带 `.name` / `.read_text()` 的替身就能离线覆盖
   「过滤非 YAML」「报错带文件名」「重复 key」三种情形，不用碰真实包目录。
3. **`_load_cards` 是「扫描」这个动作的命名 seam**：它每次调用都真扫，
   `get_cards` 才是加缓存的门面。分开之后「只加载一次」这条断言才有明确的
   可 patch 对象（patch `_load_cards` 数调用次数）。
4. **`lru_cache(maxsize=1)` 做模块级缓存**：整个进程只扫描一次包资源。
   测试要重置用 `get_cards.cache_clear()`。
5. **失败快、且不静默**：第一张坏卡片就让整个加载失败（加载期暴露，不是调用时才炸）；
   重复 key 报错而不是静默覆盖——后者会让「改错文件」看起来像没生效。

### 5.2 加载与构造时序

```
进程启动 / 首次 build_model
  → importlib.resources 扫描 _models/*.yaml            （仅一次，lru_cache）
  → 每个 yaml：safe_load → ModelCard 校验 → 注册 key=provider:name
build_model("deepseek:deepseek-flash")
  → parse_spec → get_card(provider, model) → to_config
  → get_api_key：按 card.api_key_env 读环境变量
  → build_client（每次新建 AsyncOpenAI）→ OpenAICompatModel
```

导入 `hello_agents.model` 时**不发生任何文件 I/O**——加载推迟到首次查询卡片。

---

## 6. 循环导入的处理

`_model_card.py` 顶层需要 `from ._registry import ModelConfig, Provider`；
`_registry.py` 又需要用 `ModelCard`。若 `_registry` 在**顶层**导入 `_model_card`：

- 从 `hello_agents.model` 进入时能侥幸工作（只要导入语句放在类定义之后）；
- 但从 `hello_agents.model._model_card` 一侧先导入时，`_registry` 只执行到一半，
  `ModelCard` 尚未定义 → **必然 `ImportError`**。

**结论**：`_registry.py` 顶层**不得**导入 `_model_card` 的任何名字。
把导入放进 `_collect()` 函数体（调用时 `_registry` 早已执行完毕），
`ModelCard` 只在 `TYPE_CHECKING` 块里导入供类型标注使用。

---

## 7. 注册表改造

### 7.1 删除

- `REGISTRY: dict[Provider, ModelConfig]`——被卡片注册表取代。
  它未被 `__init__.py` 导出、项目内无其他引用，可安全删除。
- `_ENV_VAR: dict[Provider, str]`——环境变量名改由卡片的 `api_key_env` 提供。

原有两条实测结论的注释**搬进 YAML**，不随代码删除而丢失。

### 7.2 新增 `get_card`

```python
def get_card(provider: Provider, model: str | None = None) -> ModelCard:
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

`model=None` 这条分支是**为兼容而存在**的：`get_model_config(Provider.DEEPSEEK)`
（现有测试大量使用）要能拿到该 provider 的默认卡片。目前一 provider 一卡；
万一将来同名多卡，按 `name` 排序取第一张，保证结果确定。

### 7.3 改写两个查询函数（签名不变）

```python
def get_model_config(
    provider: Provider = Provider.DEEPSEEK, model: str | None = None
) -> ModelConfig:
    return get_card(provider, model).to_config()


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

**一处行为变化（有意）**：`get_model_config(Provider.DEEPSEEK, "某个不存在的模型")`
以前会回落到默认卡片并把 `model` 覆盖成那个名字，现在抛 `UnknownModelError`。
这正是「声明式」要的效果——**没声明的模型就是不存在**，不静默伪造一个配置。
影响面已核实为零：项目内所有实际使用的 spec 恰好就是三张卡
（`dashscope:qwen3.7-plus` / `deepseek:deepseek-flash` / `zhipu:glm-5.2`），
且测试对 `get_model_config` 的调用**从不传** `model` 参数。

`parse_spec` / `build_client` / `get_client` / `build_model` **一行不改**。
`build_model` 的调用顺序是 `parse_spec → get_model_config → build_client`，
所以未知模型在 `get_model_config` 就报错，**早于** `build_client` 去读环境变量。

---

## 8. 影响面清单

**新增**
- `hello_agents/model/_model_card.py`
- `hello_agents/model/providers/_models/__init__.py`
- `tests/test_model_card.py`

**修改**
- `hello_agents/model/_registry.py`——删两个字典、加加载器与 `get_card`、改写两个查询函数
- `hello_agents/model/providers/_models/{dashscope,deepseek,zhipu}.yaml`——从 0 字节填充
- `hello_agents/model/__init__.py`——新增四项导出

**新增导出**（`hello_agents/model/__init__.py`）：
`Capabilities`、`ModelCard`、`ModelCardError`、`UnknownModelError`。

**不动**
- `hello_agents/model/providers/_openai_compat.py`、`_base.py`、`_formatter.py`、
  `_response.py`、`_structured.py`、`_tool.py`、`_usage.py`、`message.py`
- `hello_agents/model/providers/{dashscope,deepseek,zhipu}.py`（0 字节占位，
  等某家出现独有能力时再分化，M8 用不到）
- `examples/` 全部脚本
- 与 M8 无关的既有未提交改动

---

## 9. 有意不做（不是遗漏）

1. **client 单例缓存**——决策 1，保持现状。
2. **前端参数表单元数据**（`parameter_schema` / `parameter_overrides` /
   `input_types` / `output_types` / `label` / `status`）——服务的是「在网页上为每个
   模型生成参数面板、按 MIME 类型显隐控件」，与「注册一个可调用模型」不是一回事。
3. **`providers/{dashscope,deepseek,zhipu}.py` 分化**——三家目前共用
   `OpenAICompatModel`，差异全在配置里；没有独有能力就没有分化的理由。
4. **卡片热重载 / 运行时改配置**——YAML 是部署期资产，改完重启即可。
5. **`default` 标记字段**——当前一 provider 一卡，用不上。
6. **卡片不可变 / 缓存不可变**——`get_cards()` 返回的是缓存本体，卡片与 dict 都可变；
   任何调用方一次 `get_card(...).base_url = ...` 或 `get_cards().clear()` 都会污染全进程。
   当前无调用方这么做，故不做。真要防，应把卡片设为 `frozen=True`——**不要**改成返回
   浅拷贝，那只会让「改了但别人看不见」更难查。
7. **一 provider 多卡时的 `api_key_env`**——`get_api_key(provider)` 与
   `build_client(cfg)` 手里只有 `Provider`，取的是该 provider **默认卡**的 env 名。
   当前一 provider 一卡，无影响；将来若同一 provider 出现 `api_key_env` 不同的多张卡，
   需要把 `api_key_env` 带进 `ModelConfig`（或让 `build_client` 接收已选中的卡片），
   否则会静默读到另一张卡的凭据。

---

## 10. 测试策略（全部离线，`tests/test_model_card.py`）

| 覆盖点 | 用例 |
|---|---|
| 合法 YAML → 卡片 → `ModelConfig` 字段全对 | `test_card_to_config_maps_every_field` |
| 可选字段缺省（`output_size` / `capabilities`） | `test_card_optional_fields_default` |
| 坏 YAML 六类：语法错 / 缺必填 / `context_size` 非正 / `output_size` 非正 / 未知 provider / 空文本 | `test_from_yaml_rejects_bad_input`（参数化） |
| 多写字段被拒（防误写 key 本体） | `test_card_rejects_extra_fields` |
| 加载器：过滤非 YAML、key 为 `provider:name` | `test_collect_skips_non_yaml_and_keys_by_provider_name` |
| 加载器：坏卡片报错**带文件名** | `test_collect_reports_file_name_on_bad_card` |
| 加载器：重复 key 被拒 | `test_collect_rejects_duplicate_key` |
| 真实包内三张卡全部被发现、key 正确 | `test_get_cards_discovers_all_shipped_cards` |
| 缓存：只加载一次 | `test_get_cards_loads_only_once` |
| `get_model_config` 向后兼容（不传 model） | `test_get_model_config_defaults_to_provider_card` |
| 未知 model 报错并列出可用卡片 | `test_get_model_config_unknown_model_lists_available_cards` |
| `build_model` 未知模型在建 client 前报错 | `test_build_model_unknown_model_raises_before_touching_credentials` |
| `get_api_key` 用卡片声明的 env 名 | `test_get_api_key_uses_env_var_declared_by_card` |
| 缺 key 时消息点名正确 env 变量 | `test_get_api_key_missing_names_the_card_env_var` |
| 同一 YAML 内重复字段被拒 | `test_from_yaml_rejects_duplicate_field_in_one_file` |
| 必填字符串为空串被拒（`name` / `base_url` / `api_key_env`） | `test_from_yaml_rejects_empty_required_strings` |
| 非 UTF-8 卡片报错带文件名 | `test_collect_reports_file_name_on_non_utf8_card` |
| 随包三张卡片的实测取值（护栏） | `test_shipped_cards_declare_measured_facts` |
| 能力位报错指向卡片 YAML 而非已删的注册表 | `tests/test_model_structured.py::test_json_schema_mode_requires_the_capability_bit`（扩展断言） |

**回归**：全量 `.venv/Scripts/python.exe -m pytest -q` 必须回到
「**561 + N passed / 1 failed / 9 skipped**」，唯一失败仍是
`tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing`
（与本工作线无关的既有失败）。

**在线 demo**（M3–M7，需 API key 且产生费用）：单独征求同意后运行，
确认可见行为与 M7 提交时一致。

---

## 11. 验收标准

1. `tests/test_model_card.py` 全绿；`test_model_retry.py` / `test_model_tools.py` /
   `test_model_structured.py` / `test_model_examples.py` 全绿；
2. 全量 `pytest -q` 不新增失败（既有 `test_embedding.py` 那条与本工作线无关）；
3. 新增一个模型只需加一个 YAML、不改任何 Python——可用「加一张假卡片 → 
   `get_cards()` 里出现 → 删掉」验证；
4. 加载走 `importlib.resources`，`_models/` 是包；无任何相对路径 `open`；
5. YAML 里只有 `api_key_env`、没有 key 本体，且 `extra="forbid"` 让违规写法在加载期报错；
6. 卡片加载只发生一次（`lru_cache`）；client 仍按现有方式每次新建、无缓存；
7. M3–M7 公开接口未破坏：`build_model` 与四个 demo 的调用方式原样可用；
8. 规格 §10 自检清单逐条可答：声明式配置的收益与代价、agentscope 为何多那些字段、
   新增模型只改 YAML、`importlib.resources` 而非相对 open、YAML 无 key 本体、
   回归通过、加载只一次。

---

## 12. 与 agentscope 的对照

| 本实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| `ModelCard` / `Capabilities` | `src/agentscope/model/_model_card.py` | Pydantic 卡片、`from_yaml`、字段校验 |
| `_models/*.yaml` | 各 provider 的 `_models/*.yaml` | 一模型一文件，声明 context / output / 能力 |
| `_collect` / `get_cards` | provider 包的加载逻辑 | 扫描 `_models`、建立 `name → card` |
| `api_key_env` | provider 配置里的凭据字段 | 只存变量名，key 从环境读 |
| **裁剪**：`parameter_schema` / `parameter_overrides` / `input_types` / `output_types` / `label` / `status` | `ModelCard.from_yaml` 合并 `parameter_class` | 服务前端动态渲染调参表单（按 MIME 类型显隐控件），与「注册可调用模型」无关，M8 不做 |
