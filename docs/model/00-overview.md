# Model 模块学习总览（hello_agents/model）

> 目标：在**现有 `hello-agents` 工程内**，新建一个**独立子包 `hello_agents/model/`**，
> 对标 agentscope 的 model 层，亲手实现「统一模型抽象 + 多 provider 接入 + 流式 +
> 工具调用 + 结构化输出」，从而**吃透 Agent 系统中 model 模块的设计与实现原理**。
>
> 本学习包与旧代码（`hello_agents/core/llm.py`、`core/message.py`、根目录
> `llm_client.py` 等）**并存、互不依赖、互不修改**。

---

## 1. 你最终要拥有的能力

完成后，你应当能**脱稿回答**：

1. 为什么 model 层要在「厂商 SDK」和「上层 Agent」之间再插一层？这层到底负责什么？
2. 为什么消息要建模成 `Msg + 多个 Block`，而不是一个 `role + content 字符串`？
3. 一次流式请求里，零散的 SSE chunk 是如何被**累加**成一个完整响应的？
   tool_call 的参数是分片到达的，怎么拼？
4. Function Calling 的完整闭环：模型如何声明要调工具 → 你如何执行 → 结果如何回灌 → 模型如何据此再回答？
5. 当某家 API 不支持「结构化输出」时，如何用「工具调用 + JSON Schema 校验/修复」模拟出来？
6. 重试、取消（中断）这类横切逻辑，为什么应该收敛在基类、用「模板方法」实现？
7. 为什么模型配置会从「Python 代码配置」演进到「YAML 声明式卡片（ModelCard）」？

> 学习判据不是「写完了」，而是「每个设计决策你都能讲出**为什么这样、不那样会怎样**」。

---

## 2. 一个必须先想清楚的问题：OpenAI 兼容路线下，抽象层的价值在哪？

你选择让 DashScope、DeepSeek、智谱 GLM **统一走 OpenAI 兼容协议**（同一个
`openai` SDK，只切 `base_url / api_key / model`）。这带来一个风险：

> 三家共用一套调用代码，抽象层很容易写成「薄得没有存在感」，
> 看起来只是把 `client.chat.completions.create(...)` 包了一层。

在这条路线下，统一抽象的价值**不在协议转换**，而在以下四件事，请在整个学习过程中反复对照：

| 价值点 | 说明 | 对应里程碑 |
|---|---|---|
| **① 能力差异被元数据收敛** | 三家的 `context_size`、是否支持 thinking、是否支持原生 `json_schema`、模型名各不相同。用「模型注册表 / ModelCard」描述这些**能力位**，让调用代码按能力分支，而不是写死某家。 | M2、M8 |
| **② 流式 chunk → 完整响应的聚合** | 无论哪家，流式返回的都是增量碎片；统一的 `ChatResponse` 负责把文本、tool_call 参数、thinking、usage 累加为完整结果。 | M4 |
| **③ 横切逻辑在基类收口** | 重试、取消/中断、计时、token 估算，对所有厂商都一样，放在基类用「模板方法」统一处理，子类只实现「真正调 API」这一步。 | M3、M5 |
| **④ 为非兼容协议留扩展点** | 将来接 Ollama、vLLM、或某家原生（非兼容）协议时，只需新增一个子类实现 `_call_api`，上层 Agent 与消息模型完全不动。 | 全程的接口约束 |

**结论**：判断你的抽象层有没有白写，就看上面四点是否都在你的代码里有落点。

---

## 3. 目标分层架构

```
你的 demo / 实验脚本（只依赖统一接口，不感知厂商）
        │
        ▼
┌──────────────────────────────────────────────────────┐
│ ChatModelBase（抽象基类）                               │
│   __call__                 : 重试 + 取消 + 流式聚合（模板方法）│
│   generate_structured_output : 工具兜底 / 原生 override     │
│   count_tokens / 凭证 / 注册表加载                        │
│   抽象方法 _call_api        : 子类只需实现这一步             │
└──────────────────────────────────────────────────────┘
        ▲ 继承
┌────────────────┬────────────────┬──────────────────┐
│ DashScopeModel │ DeepSeekModel  │ ZhipuModel        │  （极薄：差异=配置+能力位）
└────────────────┴────────────────┴──────────────────┘
        │  通过 Formatter 双向转换
        ▼
Msg / Block 统一中间模型  ←→  OpenAI 兼容 dict（SDK 实际收发）
        ▲
模型注册表 / ModelCard：base_url、context_size、能力位（先 Python，后 YAML）
```

---

## 4. 目标目录结构（随里程碑逐步长出来，不要一次建完）

```
hello-agents/
├─ pyproject.toml
├─ .env  / .env.example
└─ hello_agents/
   └─ model/
      ├─ __init__.py
      ├─ message.py          # M1：Msg + 各 Block
      ├─ _usage.py           # M3：ChatUsage
      ├─ _response.py        # M3/M4：ChatResponse + 流式累加 + FinishedReason
      ├─ _formatter.py       # M3：Msg/Block ↔ OpenAI dict
      ├─ _registry.py        # M2：Python 配置注册表 + 工厂
      ├─ _base.py            # M3/M5：ChatModelBase（模板方法、重试、取消、聚合）
      ├─ _structured.py      # M7：结构化输出（工具兜底 + 原生 override）
      ├─ _model_card.py      # M8：YAML ModelCard（由 _registry 重构而来）
      └─ providers/
         ├─ __init__.py
         ├─ _openai_compat.py # 三家共用的兼容基类（_call_api 主体）
         ├─ dashscope.py
         ├─ deepseek.py
         ├─ zhipu.py
         └─ _models/*.yaml   # M8：模型卡片
```

> 每进入一个里程碑，只创建该阶段需要的文件。**留白与渐进本身就是学习内容。**

---

## 5. 里程碑路线图

| # | 里程碑 | 你亲手实现的核心 | 验收（能讲清 / 跑通） |
|---|---|---|---|
| **M0** | 环境与包骨架 | 确认 uv 环境、`.env` 配三家 key、新建 `model/` 骨架、最小连通 | 能 `import hello_agents.model`，能读到 key |
| **M1** | 统一消息模型 | `Msg`、`Text/ToolCall/ToolResult/Thinking` Block、角色、序列化 | 能构造/组合消息并转 dict |
| **M2** | 模型注册（Python 配置） | 厂商配置表、能力位、凭据、工厂函数 | 用一个名字拿到三家客户端 |
| **M3** | 最小调用闭环 | `ChatResponse`、`Formatter`、`_call_api`、`__call__` | 非流式一问一答，三家都通 |
| **M4** | 流式聚合（重点难点） | SSE chunk 解析、delta 累加、usage 提取、`async for` | 边流式打印，结束拿到完整响应 |
| **M5** | 取消/中断 + 重试 | `CancelledError` 收口、可重试异常、退避 | 中断与限流重试行为正确 |
| **M6** | 工具调用闭环 | tools 定义、tool_calls 拼接、`role=tool` 回灌、tool_choice | 模型调工具并据结果再回答 |
| **M7** | 结构化输出 | 工具兜底 + schema 校验修复、原生 json_schema override | 两方式都产出合规对象并对比 |
| **M8** | 重构为 YAML ModelCard | 卡片化、目录扫描、schema 合并、能力驱动行为 | 同一能力从代码迁到声明式 |
| **M9** | 复盘 | 脱稿画全链路、回答本文第 1 节 7 个问题 | 每个决策都能讲出理由 |

前置知识（**Pydantic / asyncio / SSE / Function Calling**）不单独开教程，
而是在**第一次用到它的里程碑里现用现讲**：

- Pydantic、消息建模 → M1（见 `01-milestone-0-1.md`）
- asyncio、异步生成器 → M3/M4
- SSE 协议细节 → M4
- Function Calling 协议 → M6

---

## 6. 协作约定（引导式）

1. 每个里程碑我交付一份「图纸」：目标 → 前置知识讲解 → **接口契约（签名与职责）**
   → 关键流程 → **留给你的动手任务（明确留白）** → 自检清单 → agentscope 源码对照。
2. 你按契约**亲手写实现**，写完把代码/问题贴给我；我 review、纠错、追问，
   确认你吃透后再进入下一个里程碑。
3. demo 脚本与单元测试由你自己写；我负责设计文档与 review。
4. 三家的 `base_url`、模型名、能力支持情况会随时间变化，**在 M2 接入前以各厂商官方文档为准核对**，文档中凡涉及具体取值都会标注。

---

## 7. 下一步

阅读 `01-milestone-0-1.md`，完成 **M0（环境与骨架）** 与 **M1（统一消息模型）**，
然后把你的 `hello_agents/model/message.py` 与 `.env` 配置情况贴给我 review。
