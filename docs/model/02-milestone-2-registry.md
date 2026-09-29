# 里程碑 2：Python 模型注册表（配置 + 能力位 + 凭据 + 工厂）

> 目标：在不发任何请求的前提下，建立一份
>
> **Python 配置注册表**
>
> ，描述三家的
> `base_url`
>
> 、默认模型、
>
> `context_size`
>
>  与
>
> **能力位（capability flags）**
>
> ，并实现
> 「凭据解析」与「工厂函数」—— 给定一个模型标识，就能拿到配置并构造出
> `AsyncOpenAI`
>
>  客户端。M3 再用它组装真正的模型类。
> 本阶段
>
> **不写**
>
>  
>
> `ChatModelBase`
>
>  / 
>
> `ChatResponse`
>
>  / 
>
> `Formatter`
>
> ，也
>
> **不发请求**
>
> 。



***

## 1. 为什么需要「注册表 + 能力位」？

上层 Agent 不应写死某家的地址和「这家到底支不支持 thinking / 结构化输出」。

把这些**易变的、因厂商而异的事实**收敛到一份配置里，带来三个好处：



1. **切换 / 新增模型只改配置，不改调用逻辑**；

2. **能力差异变成可判断的布尔位**，调用代码按能力分支

   （`if cfg.supports_thinking: ...`），而不是散落的字符串硬编码；

3. 这份 Python 配置就是 **M8 演进到 YAML ModelCard 的前身**——

   先写代码配置，你才会切身感到「为什么需要声明式文件」。

> 关键认知：
>
> **配置是「数据」，不是「逻辑」**
>
> 。注册表里只放事实（地址、尺寸、能力），
> 不要放调用流程。



***

## 2. 三家事实（已按官方文档核对，2026-09；以官方为准）



| Provider     | base\_url                                           | 环境变量（Key）           | 建议默认模型           |
| ------------ | --------------------------------------------------- | ------------------- | ---------------- |
| DashScope 百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` | `qwen3.7-max`    |
| DeepSeek     | `https://api.deepseek.com`                          | `DEEPSEEK_API_KEY`  | `deepseek-flash` |
| 智谱 GLM       | `https://open.bigmodel.cn/api/paas/v4/`             | `ZHIPU_API_KEY`     | `glm-4.7`        |

来源：



* DashScope：阿里云百炼《OpenAI Chat 接口兼容》 [https://docs.bailian.console.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope](https://docs.bailian.console.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)

* DeepSeek：DeepSeek API Docs《首次调用 API / Models》 [https://api-docs.deepseek.com/zh-cn/](https://api-docs.deepseek.com/zh-cn/)

* 智谱：智谱开放文档《OpenAI API 兼容》 [https://docs.bigmodel.cn/cn/guide/develop/openai/introduction](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)

> 注意：
>
> `base_url`
>
>  末尾的 
>
> `/v1`
>
>  与斜杠各家写法略有差异；OpenAI SDK 通常能容忍，
> 但请按上表统一。三家在兼容模式下都支持 
>
> **function calling**
>
> 。

### 2.1 关于 `context_size` 与能力位（要你亲手核对，不要抄）



* `context_size`：每个模型不同（例如千问部分模型可达百万级）。**请到各厂商官方**

  **「模型详情 / 价格」页查你实际要用的那个模型，亲手填进配置**—— 能力元数据必须准确，

  这也是 M8 卡片字段的一次预演。文档不替你编数字。

* **能力位&#x20;**`supports_native_json_schema`：各家在兼容模式下对

  `response_format={"type":"json_schema"}` 的支持会变化，**M2 先保守设为&#x20;**`False`

  **（或 Unknown），M7 接入结构化输出时再逐家核对并打开**。

* `supports_thinking`：DashScope qwen3.7、DeepSeek v4 设 `True`；智谱按你选的具体

  模型判断（推理模型为 `True`）。

* `supports_tool_calls`：三家均 `True`。



***

## 3. 前置知识：凭据与配置分离



* **配置（不敏感）**：base\_url、模型名、context\_size、能力位 → 可硬编码在注册表。

* **凭据（敏感）**：API Key → **只从环境变量 /&#x20;**`.env`**&#x20;读取**，绝不写进注册表、不进 git。

`AsyncOpenAI` 构造时需要 `api_key` 与 `base_url`。本阶段把「读 key」单独做成一个

凭据函数 / 类，与「配置」分开，便于将来扩展（多种 key、轮转、不同环境）。



***

## 4. 接口契约（你要实现的内容）

文件：`hello_agents/model/_registry.py`

### 4.1 Provider 标识

用一个 `StrEnum`（或 Literal）：`DASHSCOPE = "dashscope"`、`DEEPSEEK = "deepseek"`、

`ZHIPU = "zhipu"`。

### 4.2 模型配置模型 `ModelConfig`（建议用 Pydantic 或 dataclass）

字段（职责）：



| 字段                            | 类型          | 说明                                    |
| ----------------------------- | ----------- | ------------------------------------- |
| `provider`                    | Provider 枚举 | 哪家                                    |
| `base_url`                    | `str`       | 兼容端点                                  |
| `model`                       | `str`       | 默认模型名                                 |
| `context_size`                | `int`       | 上下文长度（你亲手核对）                          |
| `supports_thinking`           | `bool`      | 是否支持思考链                               |
| `supports_tool_calls`         | `bool`      | 是否支持函数调用                              |
| `supports_native_json_schema` | `bool`      | 是否支持原生 json\_schema（M2 先 False，M7 再开） |

> 一个 
>
> `ModelConfig`
>
>  描述「
>
> **某家的一个（或默认）模型**
>
> 」。如果你想让一家挂多个模型，
> 可再加 
>
> `models: dict[str, ModelConfig]`
>
>  或单独的模型条目 —— 本阶段保持简单：
> 一家一份默认配置即可，多模型在 M8 用卡片自然展开。

### 4.3 凭据解析



```
def get\_api\_key(provider: Provider) -> str:

&#x20;   """从环境变量读取对应厂商 key；缺失时抛出明确错误（指出缺哪个变量）。"""
```

### 4.4 注册表与工厂



* `REGISTRY: dict[Provider, ModelConfig]`（或 `MODELS`）：三家的静态配置。

* `get_model_config(provider: Provider, model: str | None = None) -> ModelConfig`：

  取配置；`model` 给了就覆盖默认模型名。

* `build_client(cfg: ModelConfig) -> AsyncOpenAI`：

  用 `get_api_key(cfg.provider)` + `cfg.base_url` 构造一个 `AsyncOpenAI` 客户端

  （`from openai import AsyncOpenAI`）。

* 可选便捷入口：`get_client("dashscope") -> AsyncOpenAI`，内部串起上面两步。

> 工厂只负责「装配」，不负责调用。返回的 client 在 M3 才会真正 
>
> `await`
>
> 。

### 4.5 模型标识字符串（为 M3/M8 铺路，可选但推荐）

支持形如 `"dashscope:qwen3.7-max"`、`"deepseek:deepseek-flash"` 的单一字符串，

写一个 `parse_spec(spec) -> (Provider, model | None)`。M3 的上层用起来会很干净。



***

## 5. 留给你的动手任务（M2）



1. 新建 `_registry.py`，实现 `Provider`、`ModelConfig`、`get_api_key`、`REGISTRY`、

   `get_model_config`、`build_client`（及可选的 `parse_spec` / `get_client`）。

2. **亲手查并填好三家的&#x20;**`context_size`（官方模型页），不要用猜测值；在代码注释里

   标注取值日期 / 来源。

3. `supports_native_json_schema` 先全部 `False`，留注释说明 M7 再核对。

4. 写一个**不发请求**的自检脚本：遍历三家，打印 provider、base\_url、model、

   context\_size、能力位，以及 key 是否就绪（只打印 set/MISSING，不打印明文）。

5. （可选）构造一次 `build_client(...)`，确认能拿到 `AsyncOpenAI` 实例并正确持有

   `base_url`，但**不要**调用它。

> demo / 测试由你写：很适合为 
>
> `parse_spec`
>
> 、
>
> `get_api_key`
>
>  缺失报错写单测（不依赖网络）。

## 6. M2 自检清单



* [ ] 能用一个标识（枚举或 `"provider:model"` 字符串）拿到三家配置；

* [ ] 配置与凭据分离：注册表里没有任何明文 key；

* [ ] key 缺失时报错信息能指出缺哪个环境变量；

* [ ] `build_client` 返回的 `AsyncOpenAI` 持有正确 base\_url；

* [ ] 能解释每个能力位将来在哪一步被使用（thinking→M4、tool\_calls→M6、

  native\_json\_schema→M7）；

* [ ] `context_size` 来自官方来源而非臆测。

## 7. agentscope 源码对照



| 你的实现                       | agentscope 位置                                         | 对照要点             |
| -------------------------- | ----------------------------------------------------- | ---------------- |
| `get_api_key` / 凭据         | `src/agentscope/credential/`（`CredentialBase` 及各厂商凭证） | 凭据独立成层、惰性校验      |
| `ModelConfig` + `REGISTRY` | `src/agentscope/model/_model_card.py`（M8 才完整对照）       | 能力元数据；M2 是其代码态雏形 |
| `base_url` / 模型条目          | 各 provider 的 `_models/*.yaml`                         | M8 你会把这些迁进 YAML  |
| `build_client` 工厂          | 各 provider `_model.py` 的 client 构造                    | SDK 客户端的装配位置     |



***

## 8. 完成后

把 `_registry.py` 贴给我，并附上「遍历三家打印配置 + key 状态」的运行结果，

我 review 配置字段、能力位、凭据与工厂。通过后进入

**M3：最小调用闭环（**`ChatUsage`**&#x20;/&#x20;**`ChatResponse`**&#x20;/&#x20;**`Formatter`**&#x20;/&#x20;**`ChatModelBase`

**骨架 /&#x20;**`_call_api`**&#x20;/&#x20;**`__call__`**，非流式三家一问一答）**，届时会现用现讲

asyncio 与异步客户端。