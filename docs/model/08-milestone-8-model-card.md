# 里程碑 8：把 Python 注册表重构为声明式 YAML ModelCard

> 目标：把硬编码在 `_registry.py` 里的模型信息（provider / base_url / model /
> context_size / 能力位）搬到**一模型一个 YAML 文件**，代码只负责"加载卡片 → 校验 →
> 构造 client/config"。实现**配置与代码分离**：新增/改模型只改 YAML，不碰 Python。
>
> 凭据仍然只从环境变量读取，YAML 里只写"用哪个环境变量"，绝不写 key。

---

## 1. 为什么要声明式卡片

现在的 `REGISTRY` 是一个 Python dict，问题：

- 加一个模型要改 Python 代码、要懂代码结构、要重新发布代码；
- 配置（"这个模型上下文多大、支不支持某能力"）和逻辑（"怎么发请求"）混在一起；
- 配置无法被非开发者（或别的工具/前端）单独读取。

声明式 YAML 把"**描述一个模型**"和"**怎么调用模型**"分开：

- YAML：声明事实（是什么、会什么）；
- 代码：通用加载器 + 调用逻辑，对所有模型一致。

> 这也是 agentscope 的做法：`src/agentscope/model/_model_card.py` 的 `ModelCard`，
> 每个 provider 的 `_models/*.yaml`。

### 1.1 借鉴什么、裁剪什么

agentscope 的 ModelCard 还带了 `parameter_schema` / `parameter_overrides` /
`input_types` / `output_types` / `label` / `status`——那些主要服务于**前端动态渲染调参
表单**（在网页上为每个模型生成参数面板、按 MIME 类型显隐控件）。这对我们理解
"模型注册"不是核心，M8 **裁剪掉**，只保留注册一个可调用模型所需的字段。能说出
"agentscope 为什么多这些字段"即可，不必照搬。

---

## 2. YAML 结构设计（我们的版本）

在 `hello_agents/model/providers/_models/` 下，一模型一个文件，如 `deepseek.yaml`：

```yaml
# 身份与端点
provider: deepseek            # 必须是已支持的 provider 之一
name: deepseek-flash          # 模型名（spec 里冒号后的部分）
base_url: https://api.deepseek.com

# 规格
context_size: 1000000
output_size: 384000           # 最大输出 token

# 能力位
capabilities:
  thinking: true
  tool_calls: true
  native_json_schema: false   # M7 probe 的实测结论

# 凭据：只写环境变量名，绝不写 key 本体
api_key_env: DEEPSEEK_API_KEY
```

dashscope / zhipu 各一份，取值直接沿用你 M7 已经实测确认的内容（DashScope
native_json_schema=true，另两家 false）。

> `output_size` 可选：没有实测值的模型直接省略该字段（卡片里为 `None`），不臆造数字。

> 字段设计原则：只放"描述模型/如何找到它"的事实；像 `stream`、`max_retries` 这种
> "调用时策略"不进卡片（它们是 build_model 的运行参数，不是模型固有属性）。

---

## 3. ModelCard 模型（写在 `_model_card.py`）

一个 Pydantic 模型，职责：加载 YAML 并做**结构校验**（字段缺失/类型错/未知 provider
在加载期就暴露，而不是等到调用时）：

```python
class Capabilities(BaseModel):
    thinking: bool = False
    tool_calls: bool = True
    native_json_schema: bool = False

class ModelCard(BaseModel):
    provider: Provider
    name: str
    base_url: str
    context_size: int = Field(gt=0)
    output_size: int = Field(gt=0)
    capabilities: Capabilities = Capabilities()
    api_key_env: str

    @classmethod
    def from_yaml(cls, text: str) -> "ModelCard":
        data = yaml.safe_load(text)
        return cls.model_validate(data)

    def to_config(self) -> ModelConfig: ...   # 卡片 → 现有 ModelConfig
```

要点：
- `from_yaml` 接收**文本**而不是文件路径——读文件的职责放加载器，ModelCard 只管
  "YAML 文本 → 校验后的卡片"，便于单测（直接喂字符串）；
- `to_config()` 让卡片适配你现有的 `ModelConfig`，从而 M3–M7 的调用链完全不用改；
- 加载错误（YAML 语法错 / 校验失败）应包装成带文件名的明确异常，方便定位是哪张卡片。

---

## 4. 加载器：用 importlib.resources（打包友好）

把所有 `_models/*.yaml` 一次性加载成注册表。**不要用 `open("相对路径")`**：
那依赖"当前工作目录"，一旦包被 `uv/pip install` 安装、或从别的目录运行，相对路径就失效。
用标准库 `importlib.resources` 读取**包内资源**：

```python
from importlib.resources import files

def _load_cards() -> dict[str, ModelCard]:
    pkg = files("hello_agents.model.providers._models")
    cards = {}
    for resource in pkg.iterdir():
        if resource.name.endswith((".yaml", ".yml")):
            card = ModelCard.from_yaml(resource.read_text(encoding="utf-8"))
            cards[f"{card.provider.value}:{card.name}"] = card
    return cards
```

要点：
- `files(...).iterdir()` 遍历包内 yaml，安装后也能访问（wheel 里的数据文件）；
- key 用 `provider:name`，正好对应你的 spec；
- 加载结果做**模块级缓存**（`functools.lru_cache`，只加载一次）；client 仍按现有方式**每次新建**——`build_client` 每次返回新的 `AsyncOpenAI`，M8 不引入 client 缓存。

> 需要新依赖：`uv add pyyaml`（`import yaml`）。

---

## 5. 工厂改造（对外行为不变）

`build_model(spec, stream=..., max_retries=..., retry_delay=...)`：

1. `parse_spec(spec)` 拿到 provider、model 名；
2. 从卡片注册表取 `provider:name`（找不到→明确错误，并提示现有哪些卡片）；
3. `card.to_config()` 得到 ModelConfig；
4. 凭据：用 `card.api_key_env` 从环境变量取 key（沿用 dotenv）；
5. `build_client` + 构造 `OpenAICompatModel`。

**硬约束：M3–M7 的 spec 与 build_model 调用方式必须原样可用**——这是一次内部重构，
不允许破坏既有接口与 demo。`REGISTRY` 这个 Python dict 可以删除或退化为"由卡片加载
结果填充"，但 `get_model_config` 等被别处引用的函数要保留或给出兼容路径。

---

## 6. 加载与构造时序

```
进程启动 / 首次 build_model
  → importlib.resources 扫描 _models/*.yaml
  → 每个 yaml：safe_load → ModelCard 校验 → 注册 key=provider:name（缓存）
build_model("deepseek:deepseek-flash")
  → 取卡片 → to_config → 用 api_key_env 读 env → build_client → 返回 model
```

---

## 7. 留给你的动手任务（M8）

1. `uv add pyyaml`；写三份 YAML（dashscope/deepseek/zhipu），取值用 M7 实测结论。
2. 实现 `ModelCard` / `Capabilities`：`from_yaml`（文本入）、`to_config`、加载错误包装。
3. 用 `importlib.resources` 实现卡片加载器与模块级缓存。
4. 改造 `build_model` / `get_model_config`：从卡片构造，保持 spec 与旧调用不破坏；
   删除/替换硬编码 `REGISTRY`。
5. **回归验证**：重新跑 M3–M7 的 demo（非流式、流式、工具、结构化）与全部 model 相关
   测试，确认重构后行为一致。
6. 测试：
   - 合法 YAML → 卡片 → ModelConfig 字段正确；
   - 坏 YAML（语法错/缺字段/context_size 为负/未知 provider）→ 加载期报带文件名的错；
   - 加载器能发现全部卡片、key 正确、只加载一次（缓存）；
   - build_model 对未知 spec 报"有哪些可用卡片"。

## 8. M8 自检清单

- [ ] 能说清声明式配置相比硬编码 REGISTRY 的收益与代价；
- [ ] 能解释 agentscope ModelCard 里 parameter_schema/MIME 字段是服务什么场景、为何我们裁剪；
- [ ] 新增一个模型只需加一个 YAML、不改任何 Python；
- [ ] 用 importlib.resources 而非相对 open，包安装后仍能加载；
- [ ] YAML 里只有 api_key_env、没有 key 本体；
- [ ] M3–M7 全部 demo 与测试在重构后回归通过、公开接口未破坏；
- [ ] 卡片加载只发生一次（`lru_cache`）；client 仍按现有方式每次新建、无缓存。

## 9. agentscope 源码对照

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| ModelCard 模型 | `src/agentscope/model/_model_card.py` | Pydantic 卡片、`from_yaml`、字段校验 |
| 卡片 YAML | 各 provider `_models/*.yaml` | 一模型一文件、context/output/能力声明 |
| 卡片加载/注册 | provider 包的加载逻辑 | 扫描 `_models`、建立 name→card |
| 参数 schema（了解即可） | `ModelCard.from_yaml` 合并 parameter_class | 服务前端表单，M8 裁剪 |

---

## 10. 完成后

把三份 YAML、`_model_card.py`、加载器、`build_model` 改造，连同 M3–M7 回归结果
（demo 输出 + 全量 pytest）贴给我 review。通过后进入
**M9：脱稿复盘——不看 agentscope，从零口述/默写整套 model 模块的设计与取舍**。
