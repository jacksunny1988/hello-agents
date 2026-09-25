# 结构化笔记 NoteTool 设计 Spec

| 项 | 值 |
|---|---|
| 日期 | 2026-09-23 |
| 范围 | 新建 `hello_agents/notes/` 与 `hello_agents/tools/builtin/note_tool.py` |
| 状态 | 待评审 |
| 前置讨论 | 结构化笔记 NoteTool（Markdown + YAML frontmatter + JSON 索引） |

---

## 1. 背景与目标

### 1.1 背景

长时程任务里，Agent 的上下文会被反复压缩与截断，任务状态、已得结论、阻塞项与行动项需要一个**外部的、可持久化的、可回注的**载体。`hello_agents` 现有的记忆系统（`memory/`）面向"检索相关片段"，条目的内容是扁平文本，没有"一份可持续更新的任务文档"这一形态；`context/` 负责在预算内组装 prompt，但需要一个结构化的外部信息源。

NoteTool 补的就是这一层：**每条笔记是一个 Markdown 文件**，头部用 YAML frontmatter 记录元数据（id / title / type / tags / 时间戳），正文记录状态、结论、阻塞与行动项；另有一个 `notes_index.json` 做快速检索、元数据集中管理与完整性校验。三者结合得到：人类可读、版本控制友好、易于回注上下文。

### 1.2 目标

1. 提供 `hello_agents/notes/` 子系统：笔记数据模型、`.md` 文件读写、索引维护、元数据过滤与正文关键词检索。
2. 提供 `NoteTool`：把上述能力包成 `BaseTool`，暴露 `create` / `read` / `update` / `search` / `list` / `summary` / `delete` 七个动作，供 Agent 在长时程任务中调用。
3. 索引与文件**永不静默失配**：文件是真相源，索引是派生缓存，漂移可检测、可自愈、可全量重建。

### 1.3 非目标

- **不改动 `context/` 与 `memory/`**。"回注上下文"由调用方用现有的 `ContextBuilder.build(additional_packets=...)` 自行拼装；把笔记自动喂进上下文的接入契约留待下一轮设计（与 `context/` 那份 spec 把 Agent 接入留到下一轮的做法一致）。
- **不改动 `hello_agents/agents/`**。
- **不做向量语义检索**。检索只用"索引元数据过滤 + 正文关键词打分"，离线零 API key 可跑。
- **不做文件锁与多进程写入协调**。单进程写入假定，多进程并发只读安全。
- **不写 `README.md`**。README 由 `docs/plans/2026-09-23-context-management-plan.md` 的收尾任务统一撰写，避免两份计划争同一文件。
- **不碰 `hello_agents/__init__.py`**。顶层只导出 `core`，`memory` / `context` 均未进顶层，`notes` 保持一致。

### 1.4 设计原则

- **零配置可跑**：不配置任何东西时，`NoteStore()` 在 `./notes/` 下落盘、离线工作。
- **文件是真相源**：`.md` 是人可以手改的；手改之后系统以文件为准修正索引，而不是反过来。
- **自动修复只碰索引**：任何兜底与修正都不改写用户的 `.md` 文件；写文件只发生在 `create` / `update`。
- **模块边界可独立理解与测试**：`NoteIndex` 脱离文件系统可单测，`NoteStore` 只管"文件与索引之间的一致性"这一件事。

---

## 2. 数据模型与文件格式

### 2.1 `Note` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `str` | 形如 `note_20260923_153000_0`：`note_` + UTC `YYYYMMDD_HHMMSS` + 同秒序号（0 起） |
| `title` | `str` | 标题 |
| `type` | `str` | 建议取 `NoteType` 常量，但不做校验 |
| `tags` | `list[str]` | 标签 |
| `created_at` / `updated_at` | `datetime` | 带时区（UTC） |
| `file_path` | `str` | 相对 `notes_dir` 的路径，即 `"{id}.md"` |
| `body` | `str` | Markdown 正文，原样保存 |

`NoteType`（`StrEnum`，标准取值）：`task_state`（任务状态）、`decision`（决策记录）、`blocker`（阻塞）、`finding`（结论）、`general`（通用）。

**id 生成**：取当前 UTC 时刻格式化为 `note_%Y%m%d_%H%M%S`，再扫描目录内同前缀的 `.md` 文件名与索引内同前缀的 id，取序号最大值 +1；若目标文件已存在则继续 +1 重试。扫描同时覆盖目录与索引，因此在索引漂移状态下也不会撞名。

### 2.2 文件格式

```markdown
---
id: note_20260923_153000_0
title: 项目进展 - 第一阶段
type: task_state
tags: [refactoring, phase1, backend]
created_at: 2026-09-23T15:30:00+00:00
updated_at: 2026-09-23T15:30:00+00:00
---

# 项目进展 - 第一阶段

已完成数据模型层的重构，主要改动包括：

1. 统一了实体类的命名规范
2. 引入了类型提示，提升代码可维护性
```

**读用 `yaml.safe_load`，写用自建序列化**（约 20 行）。理由：`yaml.safe_dump` 会把中文转义成 `\uXXXX`、把键按字母重排、把 `tags` 拆成块状列表——三样都不适合进 git 的 diff。自写序列化的规则：

- 键序固定为 `id` / `title` / `type` / `tags` / `created_at` / `updated_at`。
- `tags` 写成行内列表 `[a, b]`；空列表写 `[]`。
- 时间戳写 `datetime.isoformat()`（带时区）。
- 字符串仅在必要时加引号：含 `: `、` #`、引号、控制字符、或首字符属于 `-?[]{}|>*&!%@\`` 之一、或有前导/尾随空格时，用 `json.dumps(s, ensure_ascii=False)` 产出双引号标量；否则裸写。**YAML 双引号标量的转义规则与 JSON 一致**，借标准库是这里最不容易写错的做法。
- frontmatter 与正文之间恰好一个空行；正文原样追加，不做任何加工。

### 2.3 索引格式

`notes_index.json` 与笔记同目录（一起进 git），条目如下：

```json
{
  "note_20260923_153000_0": {
    "id": "note_20260923_153000_0",
    "title": "项目进展 - 第一阶段",
    "type": "task_state",
    "tags": ["refactoring", "phase1", "backend"],
    "created_at": "2026-09-23T15:30:00+00:00",
    "updated_at": "2026-09-23T15:30:00+00:00",
    "file_path": "note_20260923_153000_0.md"
  }
}
```

落盘用 `indent=2, ensure_ascii=False`；**顶层按 id 排序**（id 自带时间戳即时间序，git diff 稳定），条目内字段顺序固定与上例一致；写入走 tmp 文件 + `os.replace` 原子替换；空索引写成 `{}`。

索引的三项作用与本设计的对应关系：

| 作用 | 落点 |
|---|---|
| 快速检索（无需打开每个文件） | `NoteStore.list()` 与 `NoteIndex.filter()` 纯走索引，零文件读 |
| 元数据管理 | `NoteIndex` 集中持有全部条目；`NoteMeta` 是唯一元数据视图 |
| 完整性校验 | §5.2 的三级漂移检测 + `verify()` / `rebuild_index()` |

### 2.4 与原始示例的四处偏离

| # | 原始示例 | 本设计 | 理由 |
|---|---|---|---|
| D1 | `created_at: 2025-01-19T15:30:00`（无时区） | 写带时区 UTC（`...+00:00`）；读时把无时区的朴素写法按 UTC 解释 | 仓库 ruff 有效规则集含 `DTZ`，朴素 `datetime` 会被判违规；跨机器、跨时区排序也才正确 |
| D2 | `file_path: "./notes/note_....md"`（相对 cwd） | 存相对 `notes_dir` 的路径（默认即 `note_....md`） | 笔记与索引一起进 git；整体 clone 到别的机器或别的目录后，相对 cwd 的路径立刻失效，相对 `notes_dir` 的永不失效 |
| D3 | `type` 无取值约束 | `Note.type` 是 `str`，`NoteType` 只导出常量**不做校验** | 文件是人可手改的；严格枚举会让手写的 `type: progress` 直接读不出来。这与 `MemoryType` 的严格枚举刻意不同 |
| D4 | 正文首行重复了 H1 标题 | 正文原样保存，不自动补 `# {title}` | 自动补会在 `update` 改标题后留下两个不一致的 H1。文档与示例照原写法演示，但代码不强制 |

### 2.5 容错解析（人手改过的文件）

| 缺失 / 异常 | 兜底 |
|---|---|
| 无 frontmatter，或正文为空 | 整篇当 `body`，元数据全走下面的兜底 |
| 缺 `id` | 用文件名 stem 当 id（`my-notes.md` → `my-notes`） |
| 缺 `title` | 正文首个 `# ` 标题 → 再退化到文件名 stem |
| 缺 `type` | `general` |
| 缺时间戳 | 文件 mtime（转 UTC） |
| `tags` 写成单个字符串 | 包成单元素列表 |
| frontmatter 不是 mapping（如整个是一段文本） | 视为无 frontmatter，走第一行的兜底 + `logger.warning` |

兜底值**只用于索引条目，绝不写回 `.md` 文件**。

---

## 3. 架构

### 3.1 模块划分

```
hello_agents/notes/
├── __init__.py   中文模块 docstring +「典型用法：」+ 按字母序 __all__
├── base.py       Note / NoteMeta / NoteType / NoteConfig / DriftReport / 异常
├── index.py      NoteIndex —— 只认 notes_index.json 的纯索引算术
├── search.py     正文关键词打分
└── store.py      NoteStore —— .md 原子读写 + 索引协同 + 漂移修复
hello_agents/tools/builtin/note_tool.py   NoteTool —— Agent 侧薄适配层
```

**为何这样拆**：`index` 是纯算术（字典进、字典出），`search` 是纯函数，两者都能脱离文件系统单测；`store` 是唯一同时接触"内存中的 Note、磁盘上的 `.md`、`notes_index.json`"三种状态的地方，也是唯一需要为一致性负责的地方。这与 `context/` 按横切能力拆、`memory/` 分子包拆的风格一致。

### 3.2 各单元职责边界

| 单元 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `base.NoteMeta` | 索引里的元数据值对象 | `to_dict` / `from_index_entry` | 无 |
| `base.Note` | 元数据 + 正文 | `to_markdown` / `from_markdown` | `NoteMeta` |
| `base.NoteConfig` | 目录与索引文件名配置 | 构造即配置；`from_env` | `core.exceptions.ConfigError` |
| `base.NoteType` | 标准 type 取值常量集 | 当字符串用 | 无 |
| `base.DriftReport` | 漂移报告（缺失 / 孤儿 / 字段不一致） | `is_clean` / `summary` | 无 |
| `base.NoteError` / `NoteNotFoundError` | 子系统异常 | 捕获 | `core.exceptions.AgentError` |
| `index.NoteIndex` | 索引文件的加载、保存、增删改查、字段过滤、整体替换 | `load` / `save` / `get` / `all` / `filter` / `upsert` / `remove` / `replace_all` | `base.NoteError` |
| `search` | 中英分词 + 覆盖率打分 | `tokenize` / `coverage` / `score` | 无 |
| `store.NoteStore` | `.md` 原子读写；CRUD；`list` / `search` / `summary`；三级漂移检测与修复；`verify` / `rebuild_index` | 见 §4.4 | `base` / `index` / `search` |
| `note_tool.NoteTool` | 把 `NoteStore` 包成 `BaseTool` 的七个动作 | `run({"action": ...}) -> ToolResponse` | `notes` / `tools.base` / `tools.response` |

### 3.3 依赖方向（无环）

```
note_tool → notes.store → { notes.index, notes.search, notes.base }
notes.index → notes.base
notes.search → （无）
```

`notes` **不依赖** `memory` / `context` / `agents`；三者也不依赖 `notes`。

**刻意不从 `memory.base` 复用 `utcnow`**：import 子模块会连带执行 `hello_agents/memory/__init__.py`（拉起 `MemoryManager` 与存储后端），为两行辅助函数付这个代价不值得，也会让两个子系统产生语义上不该有的耦合。`base.py` 自带一个 `utcnow()`。

---

## 4. 公开 API

### 4.1 `hello_agents/notes/base.py`

```python
class NoteType(StrEnum):
    TASK_STATE = "task_state"
    DECISION = "decision"
    BLOCKER = "blocker"
    FINDING = "finding"
    GENERAL = "general"

class NoteError(AgentError): ...
class NoteNotFoundError(NoteError): ...

def utcnow() -> datetime: ...                         # 带时区 UTC
def parse_datetime(value: Any) -> datetime | None: ...
    # ISO 8601 字符串或 datetime → 带时区；朴素写法按 UTC；不可解析返回 None

@dataclass
class NoteConfig:
    notes_dir: Path = Path("./notes")
    index_filename: str = "notes_index.json"

    def __post_init__(self) -> None: ...            # 归一化 Path；空串 / 非法 → ConfigError
    @property
    def index_path(self) -> Path: ...               # notes_dir / index_filename
    @classmethod
    def from_env(cls) -> NoteConfig: ...            # NOTES_DIR / NOTES_INDEX_FILENAME

@dataclass
class NoteMeta:
    id: str
    title: str
    type: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    file_path: str

    def to_dict(self) -> dict[str, Any]: ...        # 即索引条目，字段顺序固定
    @classmethod
    def from_index_entry(cls, entry: dict[str, Any]) -> NoteMeta: ...

@dataclass
class Note(NoteMeta):
    body: str = ""

    def to_markdown(self) -> str: ...
    @classmethod
    def from_markdown(
        cls, text: str, *, file_path: str, fallback_time: datetime
    ) -> Note: ...                                   # 容错解析（§2.5）

@dataclass
class SectionPreview:
    heading: str
    preview: str

@dataclass
class NoteSummary:
    meta: NoteMeta
    sections: list[SectionPreview]

@dataclass
class DriftReport:
    missing_files: list[str]      # 索引有条目、文件已不存在
    orphan_files: list[str]       # 目录有 .md、索引无条目
    mismatched: list[str]         # frontmatter 与索引条目字段不一致

    @property
    def is_clean(self) -> bool: ...
    def summary(self) -> str: ...                    # 单行摘要，供日志
```

`Note` 继承 `NoteMeta`：`list()` 返回 `list[NoteMeta]`（索引里真有什么就返回什么），`read()` 返回带正文的 `Note`。构造入口刻意命名为 `from_index_entry` 而非 `from_dict`，避免与 `Note.from_markdown` 的语义打架。

`from_markdown` 收 `file_path` 而不是 `fallback_id`：手改过 frontmatter 的文件，其 `id` 可能与文件名不一致，而 `file_path` 必须指向真实文件；兜底 id 由文件名 stem 在方法内部推导。`parse_datetime` 是公开辅助函数，供 `base` / `index` / `store` 三处共用。

### 4.2 `hello_agents/notes/index.py`

```python
class NoteIndex:
    """notes_index.json 的纯索引算术：只认字典，不认识文件系统。"""

    def __init__(self, path: Path) -> None: ...
    @property
    def path(self) -> Path: ...

    def load(self) -> None: ...                      # 文件不存在 → 空索引；JSON 损坏 → warning + 空索引
    def save(self) -> None: ...                      # 原子落盘（tmp + os.replace）
    def get(self, note_id: str) -> dict[str, Any] | None: ...
    def all(self) -> list[dict[str, Any]]: ...       # updated_at 降序
    def filter(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]: ...
    def upsert(self, entry: dict[str, Any]) -> None: ...     # 以 entry["id"] 为键
    def remove(self, note_id: str) -> bool: ...
    def replace_all(self, entries: list[dict[str, Any]]) -> None: ...
    def __len__(self) -> int: ...
```

- `filter` 与 `all` 的返回顺序统一为 **`updated_at` 降序**（同值按 id 升序，保证确定性）。
- `tags` 过滤语义是 **AND（全部包含）**；`since` / `until` 比较的是 **`updated_at`**（闭区间）。两条都是刻意选择：摘要场景要的是"收窄到 phase1 的 backend 笔记"，而不是"沾边就要"；调用方需要 OR 时调两次即可。
- `upsert` 要求 `entry` 含 `id` 与 `file_path`，缺任一项抛 `NoteError`。
- `load()` 遇到 JSON 损坏时**不抛异常**：索引是派生物，视为空后由 §5.2 的 L1 扫描自动重建。

### 4.3 `hello_agents/notes/search.py`

```python
def tokenize(text: str) -> list[str]: ...            # 英文/数字按 \w+ 小写；CJK 按字符 bigram
def coverage(needles: list[str], haystack: list[str]) -> float: ...   # |交集| / |needles|
TITLE_WEIGHT = 0.6
BODY_WEIGHT = 0.4

def score(title: str, tags: list[str], body: str, query: str) -> float: ...   # [0, 1]
```

`score = TITLE_WEIGHT * coverage(Q, tokens(title) + tags) + BODY_WEIGHT * coverage(Q, tokens(body))`；查询 token 为空时返回 `0.0`。中文用字符 bigram（单字太散、整句太粗，零依赖下的经典折中），不做 TF-IDF 加权；两个权重常量放在模块级，便于单测与后续调参。

### 4.4 `hello_agents/notes/store.py`

```python
class NoteStore:
    def __init__(self, config: NoteConfig | None = None) -> None: ...
    @property
    def config(self) -> NoteConfig: ...

    def create(
        self,
        title: str,
        body: str = "",
        *,
        type: str = NoteType.GENERAL,
        tags: list[str] | None = None,
        note_id: str | None = None,
    ) -> Note: ...
    def read(self, note_id: str) -> Note: ...            # 不存在 → NoteNotFoundError
    def update(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
    ) -> Note: ...
    def delete(self, note_id: str) -> bool: ...          # 不存在 → False，不抛
    def exists(self, note_id: str) -> bool: ...

    def list(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[NoteMeta]: ...
    def search(self, query: str, *, limit: int = 10) -> list[tuple[NoteMeta, float]]: ...
    def summary(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[NoteSummary]: ...

    def verify(self) -> DriftReport: ...
    def rebuild_index(self) -> int: ...                  # 返回重建后的条目数
```

- **`list` 纯走索引，一次文件都不读**——这正是索引存在的意义；**`summary` 必须读正文**（要小节标题与每节首行），是 O(n) 文件读。两者成本差异写进 docstring。
- `search` 返回 `(meta, score)` 二元组，与 `MemoryTool.recall` 的 `hits` 携带 `score` 对称，不让分数在层间丢失。
- `search` 过滤 `score > 0`，按 `score` 降序、同分按 `updated_at` 降序。
- `summary` 的小节提取规则：取正文中 `##` 及以下级别的标题，每节取紧随其后的首个非空行并截断到 80 字符；无小节标题时，取正文首个非空行作为唯一预览（`heading` 为空串）。
- `create(note_id=...)` 允许指定 id（迁移 / 幂等写入场景）；该 id 已存在时抛 `NoteError`。
- `update` 全参数为 `None` 是合法 no-op（不刷新 `updated_at`，直接返回当前 `Note`）；"至少给一个字段"的校验放在工具层，分层清晰。
- 目录不存在时按需 `mkdir(parents=True, exist_ok=True)`。

### 4.5 `hello_agents/tools/builtin/note_tool.py` 动作契约

| action | 必填 | 可选 | 成功 `data` | 失败 `code` |
|---|---|---|---|---|
| `create` | `title` | `body` `type` `tags` | `{**meta}` | `INVALID_PARAM` |
| `read` | `id` | — | `{**meta, "body": ...}` | `NOT_FOUND` |
| `update` | `id` + 至少一个字段 | `title` `body` `type` `tags` | `{**meta}` | `NOT_FOUND` / `INVALID_PARAM` |
| `delete` | `id` | — | `{id, deleted}` | `INVALID_PARAM` |
| `list` | — | `type` `tags` `limit` | `{"notes": [meta…]}` | `INVALID_PARAM` |
| `search` | `query` | `limit` | `{"hits": [{**meta, "score"}…]}` | `INVALID_PARAM` |
| `summary` | — | `type` `tags` `limit` | `{"notes": [{**meta, "sections": [{heading, preview}…]}…]}` | `INVALID_PARAM` |

- `text` 字段给人读：`read` 输出元信息头 + 全文；`list` 每行 `[type] 标题 (id, 更新时间)`；`search` 带分数；`summary` 按小节缩进；`create` / `update` / `delete` 输出一句确认。
- 入参三形态（dict / JSON 字符串 / 纯文本），复用 `MemoryTool._parse_input` 的思路；**纯文本默认 `action="search"`**，`query` 取该文本。
- `tags` 传成字符串时按逗号（含全角）与空白切分——LLM 常传 `"deps, phase1"`，直接 `list()` 会拆成单个字符。
- 意外异常统一 `ToolResponse.error(code="NOTE_ERROR", message=str(e))`，与 `MemoryTool` 的 `MEMORY_ERROR` 对称；`NoteNotFoundError` 转 `NOT_FOUND`，参数缺失转 `INVALID_PARAM`。
- 构造签名 `NoteTool(store: NoteStore | None = None)`，缺省自建 `NoteStore()`。笔记无连接资源，**不需要 `__enter__` / `__exit__`**（与 `MemoryTool` 不同，那圈上下文管理器是为 `MemoryManager` 的连接准备的）。

### 4.6 典型用法

```python
from hello_agents.notes import NoteStore, NoteType
from hello_agents.tools.builtin import NoteTool

store = NoteStore()                                  # 落盘到 ./notes/
note = store.create("依赖冲突排查", "## 现象\n...", type=NoteType.BLOCKER, tags=["deps"])
store.update(note.id, body=note.body + "\n## 结论\n锁定 httpx<0.28")
hits = store.search("依赖冲突")                       # [(NoteMeta, 0.83), ...]

tool = NoteTool(store)
tool.run({"action": "summary", "type": "blocker"})    # ToolResponse(data={"notes": [...]})
```

---

## 5. 一致性与错误处理

### 5.1 索引加载策略

**索引不缓存在内存里**：每次公开操作前 `load()`、变更后 `save()`。理由：多进程、人工编辑、`git checkout` 全都即时可见，组件无状态、不需要 `close()`。索引文件只有几 KB～几百 KB，这个成本换"永远新鲜"很值。

### 5.2 漂移检测分三级（成本不同）

| 级别 | 时机 | 成本 | 行为 |
|---|---|---|---|
| L1 集合级 | 每次操作后 | O(新增文件) | 目录 `*.md` 扫描 ↔ 索引键集合：索引有而文件没了的条目删除；文件有而索引没的解析 frontmatter 后补条目 |
| L2 单条级 | `read` / `update` 时 | O(1) 读 | 该文件的 frontmatter ↔ 索引条目逐字段比对（`title` / `type` / `tags` / 时间戳），不一致以文件为准修正索引 |
| L3 全量级 | 显式 `verify()` | O(n) 读 | 返回三类漂移清单，**只报告不改动**；`rebuild_index()` 清空后按目录全量重建 |

L1 只在发现集合差异时才解析新文件的 frontmatter，因此稳态下每次操作的额外成本是一次 `scandir`。**自动修复只改索引，永不改 `.md` 文件。**

### 5.3 原子性与并发

`.md` 与索引都走 tmp 文件 + `os.replace`；**不做文件锁**。docstring 写明：单进程写入假定；多进程并发只读安全（原子替换保证不会读到半个文件）；并发写最后落盘者胜。

### 5.4 错误处理

| 情形 | 处理 | 是否向调用方抛出 |
|---|---|---|
| `NoteConfig` 非法（空 `notes_dir` / 空 `index_filename` / 类型不对） | 抛 `ConfigError` | 是（构造时） |
| `read` / `update` 目标不存在 | 抛 `NoteNotFoundError` | 是（工具转 `NOT_FOUND`） |
| `create(note_id=...)` 指定 id 已存在 | 抛 `NoteError` | 是 |
| `delete` 目标不存在 | 返回 `False` | 否（工具报 `deleted: false`） |
| `.md` 无 frontmatter / frontmatter 语法错 | 容错兜底（§2.5）+ `logger.warning` | 否 |
| `notes_index.json` 损坏 | `logger.warning` + 视为空索引，随即被 L1 扫描重建 | 否 |
| 索引条目缺 `id` / `file_path`（`upsert` 入参） | 抛 `NoteError` | 是 |
| 磁盘写入失败（权限 / 空间不足） | 抛 `NoteError` | 是（工具转 `NOTE_ERROR`） |
| 同一秒 id 冲突 | 序号 +1 重试 | 否 |

原则：**除配置错误、id 冲突与磁盘故障外，笔记子系统不向调用方抛异常**；文件格式类问题一律降级并记录日志。

---

## 6. 测试与验收标准

### 6.1 测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/test_note_base.py` | YAML 标量序列化（裸写 / 引号 / 中文 / 空值）、`tags` 行内紧凑风格、标题含 YAML 特殊字符的往返、正文含 `---` 水平线、CRLF 与 BOM 容错、`from_markdown` 的七种兜底、`parse_datetime`、`NoteConfig` 校验与 `from_env`、`DriftReport` |
| `tests/test_note_index.py` | `load` / `save` 往返、文件不存在与 JSON 损坏的兜底、`upsert` 覆盖与缺字段报错、`remove`、`replace_all`、`filter` 的 type / tags(AND) / since-until(updated_at) / 组合、`all()` 的 `updated_at` 降序、顶层键排序与原子落盘 |
| `tests/test_note_store.py` | CRUD 全链路、`create` 的 id 格式与同秒冲突递增、`create(note_id=...)` 幂等冲突、`delete` 幂等、L1/L2/L3 三级漂移检测与修复、`rebuild_index`、`list` 不读文件（用 monkeypatch 计数）、`summary` 的小节提取与截断、自动修复不改写 `.md` 字节 |
| `tests/test_note_search.py` | 分词（中英混合、大小写、标点、CJK bigram 边界）、`coverage` 边界（空 needles / 无交集 / 全交集）、`score` 空查询为 0、标题命中优先于正文命中、权重常量可调 |
| `tests/test_note_tool.py` | 七个动作的成功路径与 `ToolResponse` 契约、`NOT_FOUND` / `INVALID_PARAM` / `NOTE_ERROR` 三个错误码、dict / JSON 字符串 / 纯文本三种入参、纯文本默认 `action=search`、`update` 无字段时报 `INVALID_PARAM`、`get_parameters()` 与动作集一致 |

### 6.2 测试约定

- `tests/conftest.py` 追加 `note_config(tmp_path)` 与 `note_store(note_config)` 两个夹具；沿用既有的 `embedding` / `config` / `manager` 夹具不动。
- **全部离线零 API key**，不触网、不依赖嵌入服务。
- 中文模块 docstring 与中文用例名，纯 `assert`，flat `tests/test_*.py`。
- 泛型一律用 PEP 695 语法（`class Foo[T]:`，不用 `Generic[T]`）：`requires-python = ">=3.13"` 使 ruff 推出 `target-version = py313`，`Generic` 子类会触发 `UP046`。
- 时间断言使用带时区 UTC 的 `datetime`，避免 `DTZ` 告警。

### 6.3 验收标准

1. `uv run pytest tests/ -q` 全绿，**既有测试文件不回归**（实施前基线为 13 个 `tests/test_*.py`）。
2. `uv run ruff check hello_agents tests examples` 中，**本次触碰的文件零告警**；`uv run ruff format --check hello_agents tests examples` 通过。
3. 无任何环境变量时 `uv run python examples/note_tool_demo.py` 可完整跑通，并在 `./notes/` 下产出至少 3 条笔记与一份索引。
4. 写出的 `.md` 用 `yaml.safe_load` 能解析，且 frontmatter 字段与 §2.2 示例逐字段一致（含 `tags: [a, b]` 紧凑风格与带时区时间戳）。
5. 手改文件后（改 `title`、删文件、新增无 frontmatter 的 `.md`）`list` / `read` 结果以文件为准，索引被修正；`verify()` 能报告三类漂移。
6. 把 `notes_index.json` 改成非法 JSON 后，下一次操作能自愈（不抛异常，索引被重建）。
7. `NoteTool.run({"action": "summary"})` 返回的 `data["notes"][i]` 含 `sections` 列表，每项有 `heading` 与 `preview`。
8. 自动修复全程不改写任何 `.md` 文件（测试断言修复前后文件字节完全一致）。

---

## 7. 交付物清单

| 文件 | 动作 |
|---|---|
| `hello_agents/notes/__init__.py` | 新建（中文 docstring +「典型用法：」+ 按字母序 `__all__`） |
| `hello_agents/notes/base.py` | 新建 |
| `hello_agents/notes/index.py` | 新建 |
| `hello_agents/notes/search.py` | 新建 |
| `hello_agents/notes/store.py` | 新建 |
| `hello_agents/tools/builtin/note_tool.py` | 新建 |
| `hello_agents/tools/builtin/__init__.py` | 改 1 行：`__all__` 补导出 `NoteTool` |
| `pyproject.toml` + `uv.lock` | `uv add pyyaml` |
| `.gitignore` | 改 1 行：忽略示例产物 `notes/` |
| `tests/conftest.py` | 追加 2 个夹具 |
| `tests/test_note_base.py` | 新建 |
| `tests/test_note_index.py` | 新建 |
| `tests/test_note_store.py` | 新建 |
| `tests/test_note_search.py` | 新建 |
| `tests/test_note_tool.py` | 新建 |
| `examples/note_tool_demo.py` | 新建（新建 `examples/` 目录） |
| `docs/specs/2026-09-23-note-tool-design.md` | 本文件 |

**示例脚本覆盖点**（须离线可跑）：创建三条不同类型笔记 → 更新其中一条 → `list` 按 type 过滤 → `search` 关键词打分 → `summary` 全库摘要 → 手动删除一个 `.md` 后 `verify()` 报告漂移、再 `rebuild_index()` 自愈。

### 7.1 前置事项（实施前需处理）

1. **分支**：当前在 `feat/context-management`，其计划尚未执行完（`context/scoring.py`、`context/experiment.py` 与 `context/base.py` 重写仍未落地，`tests/test_context_scoring.py` 已先写好）。建议新开 `feat/note-tool` 分支实施本 spec，避免与未完成的上下文计划混在一起。
2. **依赖**：`uv add pyyaml` 会同时改 `pyproject.toml` 与 `uv.lock`；若同时切分支，先确认这两个文件在工作区是干净的，避免把上下文计划的依赖改动带进笔记分支。

---

## 8. 实施顺序

1. `uv add pyyaml` + `notes/base.py`（数据模型、配置、异常、容错解析）+ `tests/test_note_base.py`
2. `notes/index.py` + `tests/test_note_index.py`
3. `notes/search.py` + `tests/test_note_search.py`
4. `notes/store.py` 的 CRUD 与索引协同（L1 / L2）+ conftest 夹具 + `tests/test_note_store.py` 前半
5. `notes/store.py` 的检索与完整性（`list` / `search` / `summary` / `verify` / `rebuild_index`）+ `tests/test_note_store.py` 后半
6. `notes/__init__.py` 定稿 + `tools/builtin/note_tool.py` + `builtin/__init__.py` 导出 + `tests/test_note_tool.py`
7. `examples/note_tool_demo.py` + `.gitignore`
8. `ruff format` + `ruff check` + `pytest` 全量收尾
