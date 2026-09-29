# 里程碑 0 & 1：环境骨架 + 统一消息模型

> 阅读顺序：先做完 **M0**（很短），再精读 **M1 的知识讲解**，最后按「动手任务」亲手实现。
> 本文配套总览见 `00-overview.md`。

---

# M0：环境与包骨架

你的工程已经初始化好（`pyproject.toml`、`.venv`、`uv.lock` 都在），依赖里已有
`openai`、`python-dotenv`、`pyyaml`（Pydantic 会随 openai 一并安装）。
所以 M0 只做三件事：**确认环境 → 配 key → 建 model 包骨架**。

## M0.1 确认环境

在项目根目录 `D:\projects\hello-agents` 下执行（PowerShell）：

```powershell
uv run python --version          # 期望 3.13.x
uv run python -c "import openai, dotenv, yaml, pydantic; print('deps ok')"
```

两条都正常即环境就绪，**不要重复 `uv init` / 不要重建虚拟环境**。

## M0.2 配置三家 API Key

编辑根目录 `.env`（该文件已存在且已被 `.gitignore` 忽略，**不要提交真实 key**），
追加三个键（值替换为你自己的）：

```dotenv
DASHSCOPE_API_KEY=sk-你的dashscope密钥
DEEPSEEK_API_KEY=sk-你的deepseek密钥
ZHIPU_API_KEY=你的智谱密钥
```

> `base_url`、模型名等到 **M2** 再配置（届时会先核对官方文档），M0/M1 只需要 key。

## M0.3 新建 model 包骨架

只建一个最小骨架（其余文件随里程碑再加）：

```
hello_agents/model/
├─ __init__.py     # 先留空，或只写包 docstring
└─ message.py      # M1 的实现放这里（本阶段重点）
```

## M0 自检清单

- [ ] `uv run python` 能导入 openai/dotenv/yaml/pydantic；
- [ ] 下面这段能打印出三个 key 的**是否存在**（不要打印明文）：

```python
from dotenv import load_dotenv
import os

load_dotenv()
for name in ["DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "ZHIPU_API_KEY"]:
    print(name, "set" if os.getenv(name) else "MISSING")
```

- [ ] `uv run python -c "import hello_agents.model; print('package ok')"` 输出 `package ok`。

---

# M1：统一消息模型（本阶段核心）

## 1. 先理解：为什么不能用 `role + content 字符串`？

你旧的 `core/message.py` 是这样：

```python
class Message(BaseModel):
    role: MessageRole
    content: str          # 内容只能是一段纯文本
```

这在「纯聊天」时够用，但 Agent 场景下，**一条消息的内容往往不是一段纯文本**：

- 一条 **assistant** 消息，可能**同时**包含：
  1. 一段内部思考（thinking / reasoning）；
  2. 一段给用户看的文字；
  3. 两个并行的工具调用请求。
- 一条 **tool** 结果消息，要带「对应哪个工具调用（tool_call_id）」「成功还是失败」「结构化结果」。

用一个 `str` 无法表达这些**并列、异构**的内容。解决办法是把消息内容建模成
**一个「块（Block）」的有序列表**，每块是一种明确类型：

```
Msg(role="assistant", content=[
    ThinkingBlock(...),      # 块1：思考
    TextBlock(...),          # 块2：对用户说的话
    ToolCallBlock(...),      # 块3：请求调用工具A
    ToolCallBlock(...),      # 块4：并行请求调用工具B
])
```

这就是 agentscope 的核心思路：**Msg 是信封，content 里的 Block 才是内容。**

> 设计要点：**消息结构必须「流式友好」**。流式时 tool_call 的参数是分片到达的，
> 所以 `ToolCallBlock` 的参数要设计成**可逐步拼接**的形态（见下）。这是 M4 的伏笔，
> 现在先按这个约束建模。

---

## 2. 前置知识：Pydantic 速成（结合本模块讲）

Pydantic 用「带类型标注的类」来声明数据模型，运行时会**自动校验与转换**。

### 2.1 最小模型与字段

```python
from pydantic import BaseModel, Field

class TextBlock(BaseModel):
    text: str                                    # 必填字段，类型是 str
    id: str = Field(default_factory=lambda: "自动生成的id")  # 带默认值
```

- 字段名后的 `str / int / list[...]` 是**类型注解**；传入错误类型时 Pydantic 会尝试转换，无法转换则报 `ValidationError`。
- `Field(...)` 用来补充默认值、校验（`gt=0`、`min_length=1`）、描述（`description=`）等。

### 2.2 枚举（用于角色、块类型）

```python
from enum import StrEnum        # Python 3.11+：成员本身就是 str

class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
```

`StrEnum` 的成员既能当枚举用，又能直接当字符串（`Role.USER == "user"` 为真），
序列化成 JSON 时天然就是 `"user"`，非常贴合 API。

### 2.3 联合类型与列表

```python
content: list[TextBlock | ToolCallBlock | ThinkingBlock]
```

表示「一个列表，元素可以是这几种 Block 之一」。Pydantic 会根据每个 Block 的
**判别字段（discriminator）**判断它具体是哪种——这就是为什么建议每个 Block
都带一个固定取值的 `type` 字段（见契约）。

### 2.4 常用方法（后面里程碑会反复用）

```python
block = TextBlock(text="hi")
block.model_dump()                 # -> dict：{"type": "text", "text": "hi", ...}
block.model_dump_json()            # -> JSON 字符串
block.model_copy(deep=True)        # -> 深拷贝
TextBlock.model_json_schema()      # -> JSON Schema（结构化输出 M7 会用到）
```

### 2.5 给每个模型一个固定 `type`（判别字段）

```python
class TextBlock(BaseModel):
    type: Literal["text"] = "text"     # 取值恒定为 "text"
```

`Literal["text"]` 表示该字段**只能**是 `"text"`。它的两个作用：
1. 作为联合类型的**判别标识**，让 Pydantic（和你自己）一眼分清是哪种 Block；
2. 序列化成 dict 后自带类型标签，与 OpenAI 的块结构（`{"type": "text", ...}`）对齐。

---

## 3. 接口契约（你要实现的类与字段）

> 以下是**契约**（类名、字段、职责）。字段的具体默认值、辅助方法由你按职责补全；
> 不要照抄，先想清楚每个字段「为什么需要」。

### 3.1 角色枚举 `Role`

| 成员 | 值 | 用途 |
|---|---|---|
| `SYSTEM` | `"system"` | 系统指令 |
| `USER` | `"user"` | 用户输入 |
| `ASSISTANT` | `"assistant"` | 模型回复（可能含 thinking/text/tool_call） |
| `TOOL` | `"tool"` | 工具执行结果回灌 |

### 3.2 Block（内容块）

**`TextBlock`** —— 一段普通文本
- `type: Literal["text"] = "text"`
- `text: str`
- `id: str`（块的唯一标识，给流式累加/去重用）

**`ThinkingBlock`** —— 模型的思考链（DeepSeek-R1 的 `reasoning_content`、千问 thinking）

- `type: Literal["thinking"] = "thinking"`
- `thinking: str`（思考内容）
- `id: str`

**`ToolCallBlock`** —— 模型请求调用某个工具

- `type: Literal["tool_call"] = "tool_call"`
- `id: str`（工具调用的唯一 id，结果回灌时要用它对应；对应 OpenAI 的 `tool_call.id`）
- `name: str`（要调用的工具/函数名）
- `arguments: str`（**注意是字符串，不是 dict**）

> 为什么 `arguments` 用字符串？流式时参数 JSON 是**分片**到达的（先到 `'{"locatio'`，
> 再到 `'n": "Xi\'an"}'`），字符串可以直接拼接；等流结束再 `json.loads`。
> agentscope 里同名字段叫 `input`，你可以自选命名，但要理解这个「先字符串累加、后解析」的设计。

**`ToolResultBlock`** —— 工具执行结果（回灌给模型）

- `type: Literal["tool_result"] = "tool_result"`
- `tool_call_id: str`（对应上面那个 `ToolCallBlock.id`）
- `output: str`（执行结果，统一先转成字符串/JSON 文本）
- `is_error: bool = False`（是否执行失败）

### 3.3 消息信封 `Msg`

- `role: Role`
- `content: list[TextBlock | ThinkingBlock | ToolCallBlock | ToolResultBlock]`
- `name: str | None = None`（可选：发起者名字，部分场景 API 会用到）

需要提供的**辅助方法/能力**（方法名可自定，职责要到位）：

1. **便捷构造**：能从一段纯文本快速造消息，例如概念上等价于
   `Msg.user("你好")` / `Msg.system("你是…")`（可用 `@classmethod` 实现）；
2. **取文本 / 取工具调用**：例如 `get_text_blocks()`、`get_tool_calls()`，方便上层使用；
3. **序列化**：`model_dump()` 由 Pydantic 提供；**注意：M1 只做「自身结构」的序列化，
   转成 OpenAI 的 dict 是 M3 `Formatter` 的事，本阶段不要提前写 Formatter。**

> 建议用 Pydantic 的 `Field(discriminator="type")` 给 content 的联合类型加判别器，
> 体会「带 `type` 标签」带来的解析便利。

### 3.4 一个能表达完整 Agent 回合的示例（你实现后应能构造出来）

```python
# 模型这一轮：先思考，再并行请求两个工具
Msg(
    role=Role.ASSISTANT,
    content=[
        ThinkingBlock(thinking="用户在问天气，需要调用天气工具。"),
        ToolCallBlock(id="call_1", name="get_weather", arguments='{"city": "Xi\'an"}'),
        ToolCallBlock(id="call_2", name="get_time", arguments='{"tz": "Asia/Shanghai"}'),
    ],
)

# 工具结果回灌（两条 role=tool 消息，或按你设计的形态组织）
Msg(
    role=Role.TOOL,
    name="get_weather",
    content=[ToolResultBlock(tool_call_id="call_1", output='{"temp": 22}')],
)
```

> 思考点（写的时候带着问题）：OpenAI 协议里工具结果是**每条 tool_call 一条
> `role="tool"` 消息**，而你这里把 ToolResultBlock 放进 content。这个「你的模型 ↔
> OpenAI 形态」的差异，正是 M3 `Formatter` 要解决的——现在先记住这个张力。

---

## 4. 留给你的动手任务（M1）

1. 在 `hello_agents/model/message.py` 中实现：`Role`、`TextBlock`、`ThinkingBlock`、
   `ToolCallBlock`、`ToolResultBlock`、`Msg`，字段满足第 3 节契约。
2. 为 `Msg` 实现便捷构造（文本 → 消息）与「取文本块 / 取工具调用块」方法。
3. 给 content 联合类型加 `discriminator="type"`，并故意构造一次错误数据，
   观察 Pydantic 的 `ValidationError`，理解校验是怎么生效的。
4. 验证你能构造出第 3.4 节的「思考 + 并行两个工具调用」与「工具结果回灌」。
5. **不要**写 Formatter、不要真正发 API 请求（那是 M3）。

> demo / 测试由你自己写：建议写一个极小脚本构造上述消息并 `model_dump()` 打印，
> 顺手为「便捷构造」写一两个 pytest。

## 5. M1 自检清单

- [ ] 能解释「为什么 content 是 Block 列表而不是字符串」，并举出一条消息含多种 Block 的例子；
- [ ] 每个 Block 都有固定取值的 `type`，联合类型用 `type` 判别；
- [ ] 能说清 `ToolCallBlock.arguments` 为什么是字符串、什么时候才 `json.loads`；
- [ ] 能指出「ToolResultBlock 的组织方式」与「OpenAI 的 role=tool 消息」之间的差异，
      并知道这将由 Formatter 弥合；
- [ ] `model_dump()` 输出结构正确；错误输入会触发校验异常。

## 6. agentscope 源码对照（读完你的实现后回看）

| 你的实现 | agentscope 位置 | 对照要点 |
|---|---|---|
| `Msg` | `src/agentscope/message/`（`Msg`、`UserMsg` 等） | 信封与角色、便捷构造 |
| `TextBlock` / `ThinkingBlock` | `src/agentscope/message/` 内 Block 定义 | `type` 判别、thinking 字段 |
| `ToolCallBlock` | 同上 | agentscope 用 `input`（字符串）承载分片参数，对应你的 `arguments` |
| `ToolResultBlock` | 同上 | `output`、对应 `tool_call_id`、错误标记 |
| —（M3 才做） | `src/agentscope/formatter/` | 消息 → 各厂商 dict 的转换，M1 刻意不做 |

---

## 7. 完成后

把你的 `hello_agents/model/message.py` 完整贴给我，并附上你构造「3.4 节示例」的
运行结果。我会 review 字段设计、判别器、便捷构造与序列化，确认你吃透后，
再给 **M2：Python 模型注册表（含三家 base_url/能力位核对、凭据与工厂）**。
