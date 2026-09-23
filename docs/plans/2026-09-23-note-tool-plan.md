# 结构化笔记 NoteTool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `hello_agents/notes/` 子系统（笔记数据模型、`.md` 原子读写、JSON 索引、元数据过滤与正文关键词检索）与 `NoteTool` 适配层，让 Agent 在长时程任务中拥有可持久化、人类可读、可回注上下文的外部记忆。

**Architecture:** `notes/` 拆四个模块 —— `base.py`（数据模型与容错解析）、`index.py`（只认字典的纯索引算术）、`search.py`（中英分词与覆盖率打分）、`store.py`（唯一同时接触内存/`.md`/索引三种状态、并为一致性负责的地方）。**文件是真相源，索引是派生缓存**：任何自动修复只改索引，绝不改写 `.md`；漂移按成本分三级检测（L1 集合级每次操作后、L2 单条级访问时、L3 全量级显式 `verify()`）。`tools/builtin/note_tool.py` 把 store 包成七个动作的 `BaseTool`。

**Tech Stack:** Python 3.13、PyYAML（读 frontmatter）、dataclasses、`StrEnum`、`pathlib`、`logging`、pytest、ruff。零 API key、离线可跑。

**Spec:** `docs/specs/2026-09-23-note-tool-design.md`

## Global Constraints

- **范围**：只新建 `hello_agents/notes/` 与 `hello_agents/tools/builtin/note_tool.py`。**不改动 `hello_agents/context/`、`hello_agents/memory/`、`hello_agents/agents/`、`hello_agents/__init__.py`**。
- **新增依赖仅 `pyyaml`**（`uv add pyyaml`）。读 frontmatter 用 `yaml.safe_load`；写 frontmatter 用自建序列化，键序固定 `id/title/type/tags/created_at/updated_at`，`tags` 写成 `[a, b]`。
- **时间戳一律带时区（UTC）**：`datetime.now(UTC)` / `datetime.fromtimestamp(ts, tz=UTC)`；解析无时区的朴素写法时按 UTC 解释。仓库 ruff 有效规则集含 `DTZ`，朴素 `datetime` 会告警。
- **文件是真相源**：自动修复只改 `notes_index.json`，**绝不改写 `.md`**；写 `.md` 只发生在 `create` / `update`。
- **原子写**：`.md` 与索引都走「同目录临时文件 + `os.replace`」。不做文件锁。
- **`Note.type` 是 `str`，不做取值校验**；`NoteType` 只导出标准取值常量。这与 `MemoryType` 的严格枚举刻意不同。
- **`file_path` 存相对 `notes_dir` 的路径**（即 `"{文件名}"`），不存绝对路径、不存相对 cwd 的路径。
- **泛型一律 PEP 695 语法**（`class Foo[T]:`，不用 `Generic[T]`）；`requires-python = ">=3.13"` 使 ruff 推出 `target-version = py313`，`Generic` 子类会触发 `UP046`。
- **中文模块 docstring 与中文用例名，纯 `assert`**，flat `tests/test_*.py`。
- **测试全部离线**：不触网、不需要任何 API key、不依赖嵌入服务。
- **每完成一个任务提交一次**；收尾时对本任务触碰的文件跑 `uv run ruff check` 清零告警，并 `uv run ruff format`。
- 本机 `python3` 是 Store 桩，一律用 `uv run ...` 执行。

## Review Focus

以下是 spec 隐含、但没有哪个任务的测试天然覆盖、且最容易让使用者踩坑的输入类别。每条都在**拥有该代码的任务**里配了断言：

1. **Windows 文本编辑器产物：CRLF 行尾与 UTF-8 BOM**（本机是 Windows，git 已提示 LF→CRLF）→ 解析必须容忍 `\r\n` 与 BOM，否则第一次 checkout 就全线读不出 frontmatter。→ Task 1
2. **标题含 YAML 特殊字符**（`依赖: httpx 与 #注释`、以 `-` 开头）→ 写出的文件必须仍能被 `yaml.safe_load` 逐字读回，否则"人类可读可改"的卖点当场破产。→ Task 1
3. **正文里出现 `---` 水平线** → frontmatter 切分不能把它当成结束围栏。→ Task 1
4. **笔记目录不存在、`.md` 是 0 字节、或只有 frontmatter 没有正文** → `create` 自动建目录；`read` 返回空 body 而不崩；`summary` 返回空 `sections`。→ Task 4 / Task 5
5. **工具入参 `tags` 传成字符串**（LLM 常这么传：`"tags": "deps,phase1"`）→ 必须按逗号/空格切分，而不是拆成单个字符。→ Task 6

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `hello_agents/notes/__init__.py` | 公开导出（中文 docstring +「典型用法：」+ 按字母序 `__all__`） | Task 1 建占位、Task 6 定稿 |
| `hello_agents/notes/base.py` | `Note` / `NoteMeta` / `NoteType` / `NoteConfig` / `NoteSummary` / `SectionPreview` / `DriftReport` / 异常 / 容错解析 | 新建 |
| `hello_agents/notes/index.py` | `NoteIndex`：`notes_index.json` 的加载、保存、增删改查、字段过滤 | 新建 |
| `hello_agents/notes/search.py` | `tokenize` / `coverage` / `score` | 新建 |
| `hello_agents/notes/store.py` | `NoteStore`：`.md` 原子读写、CRUD、`list` / `search` / `summary`、三级漂移检测与修复 | 新建（Task 4 建 CRUD，Task 5 加检索与完整性） |
| `hello_agents/tools/builtin/note_tool.py` | `NoteTool`：七个动作的 `BaseTool` 适配层 | 新建 |
| `hello_agents/tools/builtin/__init__.py` | `__all__` 补导出 `NoteTool` | 改 1 行 |
| `pyproject.toml` + `uv.lock` | `uv add pyyaml` | 改 |
| `.gitignore` | 忽略示例产物 `notes/` | 改 1 行 |
| `tests/conftest.py` | 追加 `note_config` / `note_store` 夹具 | 改 |
| `tests/test_note_base.py` | 数据模型、YAML 标量序列化、frontmatter 往返、容错解析 | 新建 |
| `tests/test_note_index.py` | 索引纯算术 | 新建 |
| `tests/test_note_search.py` | 分词与打分 | 新建 |
| `tests/test_note_store.py` | CRUD、漂移三级、`list` / `search` / `summary` | 新建（Task 4 建前半，Task 5 追加后半） |
| `tests/test_note_tool.py` | 七动作契约与错误码 | 新建 |
| `examples/note_tool_demo.py` | 离线可跑示例 | 新建 |

**依赖方向（无环）**：`note_tool` → `notes.store` → {`notes.index`, `notes.search`, `notes.base`}；`notes.index` → `notes.base`；`notes.search` 不依赖任何模块。`notes` 不依赖 `memory` / `context` / `agents`（因此 `base.py` 自带 `utcnow()`，不复用 `memory.base.utcnow`——import 子模块会连带拉起 `MemoryManager`）。

**约定的重复代码**：`NoteStore` 的每个公开操作都先 `self._sync()`（L1 集合级对齐）再干活，`_sync()` 内部 `self._index.load()`。索引因此**不常驻内存**，永远从磁盘取最新状态。

---

## Task 1: 依赖与 `notes/base.py`

**Files:**
- Create: `hello_agents/notes/__init__.py`（占位，只有模块 docstring）
- Create: `hello_agents/notes/base.py`
- Test: `tests/test_note_base.py`
- Modify: `pyproject.toml`、`uv.lock`

**Interfaces:**
- Consumes: `hello_agents.core.exceptions.AgentError`、`ConfigError`
- Produces:
  - `utcnow() -> datetime`（带时区 UTC）
  - `parse_datetime(value: Any) -> datetime | None`（ISO 字符串或 `datetime` → 带时区；朴素写法按 UTC；不可解析返回 `None`）
  - `class NoteType(StrEnum)`：`TASK_STATE` / `DECISION` / `BLOCKER` / `FINDING` / `GENERAL`
  - `class NoteError(AgentError)`、`class NoteNotFoundError(NoteError)`
  - `@dataclass NoteConfig(notes_dir: Path = Path("./notes"), index_filename: str = "notes_index.json")`，方法 `index_path`（property）、`from_env()`
  - `@dataclass NoteMeta(id, title, type, tags, created_at, updated_at, file_path)`，方法 `to_dict() -> dict`、类方法 `from_index_entry(entry: dict) -> NoteMeta`
  - `@dataclass Note(NoteMeta)` 追加 `body: str = ""`，方法 `to_markdown() -> str`、类方法 `from_markdown(text: str, *, file_path: str, fallback_time: datetime) -> Note`
  - `@dataclass SectionPreview(heading: str, preview: str)`
  - `@dataclass NoteSummary(meta: NoteMeta, sections: list[SectionPreview])`，方法 `to_dict() -> dict`
  - `@dataclass DriftReport(missing_files, orphan_files, mismatched)`，`is_clean`（property）、`summary() -> str`

- [ ] **Step 1: 加依赖**

```bash
uv add pyyaml
```

Expected: `pyproject.toml` 的 `dependencies` 多出 `"pyyaml>=..."`，`uv.lock` 更新。

- [ ] **Step 2: 建包占位**

创建 `hello_agents/notes/__init__.py`，内容只有模块 docstring（Task 6 再补导出）：

```python
"""结构化笔记子系统（公开导出见 Task 6）"""
```

- [ ] **Step 3: 写失败测试**

创建 `tests/test_note_base.py`：

```python
"""笔记数据模型测试：YAML 标量序列化 / frontmatter 往返 / 容错解析"""

from datetime import UTC, datetime

import pytest
import yaml

from hello_agents.core.exceptions import ConfigError
from hello_agents.notes.base import (
    DriftReport,
    Note,
    NoteConfig,
    NoteType,
    parse_datetime,
    utcnow,
)

STAMP = datetime(2026, 9, 23, 15, 30, tzinfo=UTC)
FILE = "note_20260923_153000_0.md"
BODY = "# 项目进展 - 第一阶段\n\n已完成数据模型层的重构。"


def make_note(**overrides) -> Note:
    """构造一条字段齐全的笔记"""
    fields = {
        "id": "note_20260923_153000_0",
        "title": "项目进展 - 第一阶段",
        "type": NoteType.TASK_STATE,
        "tags": ["refactoring", "phase1"],
        "created_at": STAMP,
        "updated_at": STAMP,
        "file_path": FILE,
        "body": BODY,
    }
    return Note(**(fields | overrides))


def split(text: str) -> tuple[dict, str]:
    """测试侧独立切分 frontmatter，不依赖被测实现的正则"""
    head, _, rest = text.partition("\n---\n")
    assert head.startswith("---\n")
    return yaml.safe_load(head[4:]), rest.strip("\n")


def test_to_markdown_写出的_frontmatter_可被_yaml_解析():
    text = make_note().to_markdown()
    meta, body = split(text)
    assert meta == {
        "id": "note_20260923_153000_0",
        "title": "项目进展 - 第一阶段",
        "type": "task_state",
        "tags": ["refactoring", "phase1"],
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    assert body == BODY


def test_to_markdown_tags_写成行内紧凑列表():
    assert "tags: [refactoring, phase1]" in make_note().to_markdown()


def test_to_markdown_空_tags_写成空列表():
    assert "tags: []" in make_note(tags=[]).to_markdown()


def test_to_markdown_标题含_yaml_特殊字符仍可解析():
    title = "依赖: httpx 与 #注释 - 以横线开头"
    meta, _ = split(make_note(title=title).to_markdown())
    assert meta["title"] == title


def test_from_markdown_往返保持全部字段():
    note = make_note()
    assert Note.from_markdown(note.to_markdown(), file_path=FILE, fallback_time=STAMP) == note


def test_from_markdown_正文含水平线不被当作围栏():
    body = "## 现象\n\n---\n\n## 结论\n\n锁定 httpx<0.28。"
    note = make_note(body=body)
    assert Note.from_markdown(note.to_markdown(), file_path=FILE, fallback_time=STAMP).body == body


def test_from_markdown_容忍_crlf_与_bom():
    text = "﻿" + make_note().to_markdown().replace("\n", "\r\n")
    note = Note.from_markdown(text, file_path=FILE, fallback_time=STAMP)
    assert note.id == "note_20260923_153000_0"
    assert note.body == BODY


def test_from_markdown_无_frontmatter_时用文件名与兜底时间():
    note = Note.from_markdown("# 随手记\n\n正文", file_path="my-notes.md", fallback_time=STAMP)
    assert note.id == "my-notes"
    assert note.title == "随手记"
    assert note.type == NoteType.GENERAL
    assert note.tags == []
    assert note.created_at == STAMP
    assert note.body == "# 随手记\n\n正文"


def test_from_markdown_空文本时全部走兜底():
    note = Note.from_markdown("", file_path="empty.md", fallback_time=STAMP)
    assert (note.id, note.title, note.body) == ("empty", "empty", "")


def test_from_markdown_缺字段逐项兜底():
    text = "---\ntype: blocker\n---\n\n## 阻塞\n\n等待上游修复。\n"
    note = Note.from_markdown(text, file_path="x.md", fallback_time=STAMP)
    assert note.id == "x"
    assert note.title == "x"
    assert note.type == "blocker"
    assert note.created_at == STAMP
    assert note.updated_at == STAMP
    assert note.body == "## 阻塞\n\n等待上游修复。"


def test_from_markdown_tags_写成字符串时包成单元素列表():
    text = "---\ntags: refactoring\n---\n\n正文\n"
    assert Note.from_markdown(text, file_path="x.md", fallback_time=STAMP).tags == ["refactoring"]


def test_from_markdown_frontmatter_不是映射时整篇当正文():
    text = "---\n这只是一段话\n---\n\n正文\n"
    note = Note.from_markdown(text, file_path="x.md", fallback_time=STAMP)
    assert note.id == "x"
    assert note.body == text


def test_from_markdown_frontmatter_语法错误时整篇当正文():
    text = "---\nkey: [未闭合\n---\n\n正文\n"
    assert Note.from_markdown(text, file_path="x.md", fallback_time=STAMP).body == text


def test_parse_datetime_朴素时间按_utc_解释():
    assert parse_datetime("2026-09-23T15:30:00") == STAMP


def test_parse_datetime_无法解析返回_none():
    assert parse_datetime("昨天") is None
    assert parse_datetime(None) is None


def test_note_meta_to_dict_与_from_index_entry_往返():
    meta = make_note().to_dict()
    assert list(meta) == [
        "id",
        "title",
        "type",
        "tags",
        "created_at",
        "updated_at",
        "file_path",
    ]
    assert Note.from_markdown("", file_path=FILE, fallback_time=STAMP).to_dict()["file_path"] == FILE


def test_note_config_空值抛_config_error():
    with pytest.raises(ConfigError):
        NoteConfig(notes_dir="")
    with pytest.raises(ConfigError):
        NoteConfig(index_filename="   ")


def test_note_config_index_path_与_from_env(monkeypatch, tmp_path):
    config = NoteConfig(notes_dir=str(tmp_path / "notes"))
    assert config.index_path == tmp_path / "notes" / "notes_index.json"
    monkeypatch.setenv("NOTES_DIR", str(tmp_path / "env_notes"))
    assert NoteConfig.from_env().notes_dir == tmp_path / "env_notes"


def test_drift_report_清洁与摘要():
    assert DriftReport().is_clean is True
    assert DriftReport(missing_files=["a"]).is_clean is False
    assert DriftReport(orphan_files=["b.md"]).summary() == "缺失 0 / 孤儿 1 / 不一致 0"


def test_utcnow_带时区():
    assert utcnow().tzinfo is not None
```

- [ ] **Step 4: 跑测试确认失败**

```bash
uv run pytest tests/test_note_base.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'hello_agents.notes.base'`（`notes/__init__.py` 已存在，但 `base.py` 还没有）。

- [ ] **Step 5: 写实现**

创建 `hello_agents/notes/base.py`：

```python
"""笔记子系统基础数据结构

定义笔记条目（NoteMeta / Note）、配置（NoteConfig）、摘要（NoteSummary）、
漂移报告（DriftReport）与子系统异常，以及 Markdown + YAML frontmatter 的容错解析。
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from ..core.exceptions import AgentError, ConfigError

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_UNSAFE_START = set("-?[]{}|>*&!%@`\"'#,")
_UNSAFE_SUBSTRINGS = (": ", " #")
_UNSAFE_CHARS_RE = re.compile(r"[\"'\\\x00-\x1f\x7f]")


def utcnow() -> datetime:
    """返回带时区的当前 UTC 时间"""
    return datetime.now(UTC)


def parse_datetime(value: Any) -> datetime | None:
    """把 ISO 8601 字符串或 datetime 解析为带时区的 datetime

    无时区的朴素写法按 UTC 解释；无法解析时返回 None。
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class NoteType(StrEnum):
    """笔记类型的标准取值

    仅作常量导出，``Note.type`` 不做取值校验——文件是人可手改的，
    严格枚举会让手写的 ``type: progress`` 直接读不出来。
    """

    TASK_STATE = "task_state"
    DECISION = "decision"
    BLOCKER = "blocker"
    FINDING = "finding"
    GENERAL = "general"


class NoteError(AgentError):
    """笔记子系统异常基类"""


class NoteNotFoundError(NoteError):
    """笔记不存在"""


@dataclass
class NoteConfig:
    """笔记子系统配置

    ``notes_dir`` 缺省为 ``./notes``；``index_path`` 即 ``notes_dir / index_filename``，
    索引与笔记同目录，便于一起进 git。
    """

    notes_dir: Path = Path("./notes")
    index_filename: str = "notes_index.json"

    def __post_init__(self) -> None:
        if not str(self.notes_dir).strip():
            raise ConfigError("notes_dir 不能为空")
        if not self.index_filename.strip():
            raise ConfigError("index_filename 不能为空")
        self.notes_dir = Path(self.notes_dir)

    @property
    def index_path(self) -> Path:
        """索引文件路径"""
        return self.notes_dir / self.index_filename

    @classmethod
    def from_env(cls) -> "NoteConfig":
        """从环境变量构造配置（缺失项回落到默认值）"""
        return cls(
            notes_dir=Path(os.getenv("NOTES_DIR", "./notes")),
            index_filename=os.getenv("NOTES_INDEX_FILENAME", "notes_index.json"),
        )


@dataclass
class NoteMeta:
    """索引里的笔记元数据

    ``file_path`` 是相对 ``notes_dir`` 的路径，不是绝对路径、也不是相对 cwd 的路径。
    """

    id: str
    title: str
    type: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    file_path: str

    def to_dict(self) -> dict[str, Any]:
        """序列化为索引条目（字段顺序固定，便于 diff）"""
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "file_path": self.file_path,
        }

    @classmethod
    def from_index_entry(cls, entry: dict[str, Any]) -> "NoteMeta":
        """从索引条目反序列化（字段缺失时走兜底，不抛异常）"""
        return cls(
            id=str(entry["id"]),
            title=_as_str(entry.get("title")) or str(entry["id"]),
            type=_as_str(entry.get("type")) or NoteType.GENERAL,
            tags=_as_tags(entry.get("tags")),
            created_at=parse_datetime(entry.get("created_at")) or utcnow(),
            updated_at=parse_datetime(entry.get("updated_at")) or utcnow(),
            file_path=str(entry["file_path"]),
        )


@dataclass
class Note(NoteMeta):
    """一条笔记：元数据 + 正文"""

    body: str = ""

    def to_markdown(self) -> str:
        """渲染为 frontmatter + 正文的 Markdown 文本

        正文原样输出，不自动补 ``# 标题``（自动补会在改标题后留下两个不一致的 H1）。
        """
        lines = [
            "---",
            f"id: {_yaml_scalar(self.id)}",
            f"title: {_yaml_scalar(self.title)}",
            f"type: {_yaml_scalar(self.type)}",
            f"tags: [{', '.join(_yaml_scalar(tag) for tag in self.tags)}]",
            f"created_at: {self.created_at.isoformat()}",
            f"updated_at: {self.updated_at.isoformat()}",
            "---",
            "",
            self.body,
        ]
        return "\n".join(lines).rstrip("\n") + "\n"

    @classmethod
    def from_markdown(cls, text: str, *, file_path: str, fallback_time: datetime) -> "Note":
        """从 Markdown 文本解析笔记，缺失字段按 spec §2.5 兜底

        - 换行符统一为 ``\\n``（Windows / git autocrlf 写出的 CRLF 文件也能解析），
          BOM 由读取方用 ``utf-8-sig`` 处理。
        - 正文首尾的空行会被归一化（Markdown 语义不变）。
        - ``file_path`` 取真实文件名：手改过 frontmatter 的 ``id`` 可能与文件名不一致。
        """
        meta, body = _split_frontmatter(text.replace("\r\n", "\n"))
        body = body.strip("\n")
        fallback_id = Path(file_path).stem
        created_at = parse_datetime(meta.get("created_at")) or fallback_time
        return cls(
            id=_as_str(meta.get("id")) or fallback_id,
            title=_as_str(meta.get("title")) or _first_heading(body) or fallback_id,
            type=_as_str(meta.get("type")) or NoteType.GENERAL,
            tags=_as_tags(meta.get("tags")),
            created_at=created_at,
            updated_at=parse_datetime(meta.get("updated_at")) or created_at,
            file_path=file_path,
            body=body,
        )


@dataclass
class SectionPreview:
    """摘要里的一个小节：标题 + 紧随其后首个非空行的截断预览"""

    heading: str
    preview: str


@dataclass
class NoteSummary:
    """一条笔记的全库摘要项"""

    meta: NoteMeta
    sections: list[SectionPreview]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（元数据 + 小节预览）"""
        return self.meta.to_dict() | {
            "sections": [
                {"heading": section.heading, "preview": section.preview}
                for section in self.sections
            ]
        }


@dataclass
class DriftReport:
    """索引与文件的漂移报告（只报告，不改动）"""

    missing_files: list[str] = field(default_factory=list)
    orphan_files: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """三类漂移是否都为空"""
        return not (self.missing_files or self.orphan_files or self.mismatched)

    def summary(self) -> str:
        """单行摘要，供日志与示例输出"""
        return (
            f"缺失 {len(self.missing_files)} / 孤儿 {len(self.orphan_files)} / "
            f"不一致 {len(self.mismatched)}"
        )


def _yaml_scalar(value: str) -> str:
    """序列化一个 YAML 标量

    安全时裸写；否则用 JSON 双引号形式——YAML 双引号标量的转义规则与 JSON 一致，
    因此可以借 json.dumps 处理引号、控制字符与中文（不会像 yaml.safe_dump 那样转义成 \\uXXXX）。
    """
    plain = (
        value
        and value[0] not in _UNSAFE_START
        and value == value.strip()
        and not _UNSAFE_CHARS_RE.search(value)
        and not any(token in value for token in _UNSAFE_SUBSTRINGS)
    )
    return value if plain else json.dumps(value, ensure_ascii=False)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 frontmatter 映射与正文

    无 frontmatter、语法错误或不是映射时，返回空映射与全文。
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        logger.warning("frontmatter 解析失败（%s），按无 frontmatter 处理", error)
        return {}, text
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        logger.warning("frontmatter 不是映射，按无 frontmatter 处理")
        return {}, text
    return meta, text[match.end() :]


def _as_str(value: Any) -> str:
    """非空标量转字符串；容器与 None 返回空串"""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _as_tags(value: Any) -> list[str]:
    """规整标签：列表逐项转字符串，单个字符串包成单元素列表"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _first_heading(body: str) -> str:
    """取正文里第一个一级标题的文本"""
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else ""
```

- [ ] **Step 6: 跑测试确认通过**

```bash
uv run pytest tests/test_note_base.py -v
```

Expected: PASS（20 passed）。

- [ ] **Step 7: 格式化并提交**

```bash
uv run ruff format hello_agents/notes tests/test_note_base.py
uv run ruff check hello_agents/notes tests/test_note_base.py
git add pyproject.toml uv.lock hello_agents/notes tests/test_note_base.py
git commit -m "feat: add note data model with tolerant frontmatter parsing"
```

Expected: ruff 零告警。

---

## Task 2: `notes/index.py`

**Files:**
- Create: `hello_agents/notes/index.py`
- Test: `tests/test_note_index.py`

**Interfaces:**
- Consumes: `base.NoteError`、`base.parse_datetime`
- Produces: `class NoteIndex(path: Path)`，方法 `path`（property）、`load()`、`save()`、`get(note_id) -> dict | None`、`all() -> list[dict]`、`filter(*, type=None, tags=None, since=None, until=None) -> list[dict]`、`upsert(entry: dict) -> None`、`remove(note_id) -> bool`、`replace_all(entries) -> None`、`__len__()`
- 契约：条目是含 `id` 与 `file_path` 的字典，以 `id` 为键；`all` / `filter` 返回顺序为 `updated_at` 降序、同值按 `id` 升序；`filter` 的 `tags` 是 AND（全部包含），`since` / `until` 比较 `updated_at`（闭区间）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_note_index.py`：

```python
"""NoteIndex 测试：加载 / 落盘 / 过滤 / 排序 / 损坏兜底"""

import json

import pytest

from hello_agents.notes.base import NoteError, parse_datetime
from hello_agents.notes.index import NoteIndex


def entry(note_id: str, *, updated: str, type: str = "general", tags=None) -> dict:
    """构造一个合法的索引条目"""
    return {
        "id": note_id,
        "title": f"标题 {note_id}",
        "type": type,
        "tags": tags or [],
        "created_at": "2026-09-23T15:30:00+00:00",
        "updated_at": updated,
        "file_path": f"{note_id}.md",
    }


@pytest.fixture
def index(tmp_path) -> NoteIndex:
    return NoteIndex(tmp_path / "notes" / "notes_index.json")


def test_load_文件不存在时为空索引(index):
    index.load()
    assert len(index) == 0


def test_save_与_load_往返(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.save()
    reloaded = NoteIndex(index.path)
    reloaded.load()
    assert reloaded.get("note_a") == entry("note_a", updated="2026-09-23T15:30:00+00:00")


def test_save_顶层按_id_排序(index):
    index.load()
    index.upsert(entry("note_b", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.save()
    assert list(json.loads(index.path.read_text(encoding="utf-8"))) == ["note_a", "note_b"]


def test_save_不残留临时文件(index):
    index.load()
    index.save()
    assert [p.name for p in index.path.parent.iterdir()] == ["notes_index.json"]


def test_load_json_损坏时按空索引处理(index, caplog):
    index.path.parent.mkdir(parents=True)
    index.path.write_text("{ 不是 json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        index.load()
    assert len(index) == 0
    assert "不可读" in caplog.text


def test_load_顶层不是对象时按空索引处理(index):
    index.path.parent.mkdir(parents=True)
    index.path.write_text("[1, 2]", encoding="utf-8")
    index.load()
    assert len(index) == 0


def test_load_丢弃缺_id_或_file_path_的条目(index):
    index.path.parent.mkdir(parents=True)
    payload = {
        "note_a": {"id": "note_a", "file_path": "note_a.md"},
        "note_b": {"id": "note_b"},
        "note_c": {"file_path": "note_c.md"},
    }
    index.path.write_text(json.dumps(payload), encoding="utf-8")
    index.load()
    assert [e["id"] for e in index.all()] == ["note_a"]


def test_upsert_覆盖同_id_条目(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_a", updated="2026-09-24T10:00:00+00:00", type="blocker"))
    assert len(index) == 1
    assert index.get("note_a")["type"] == "blocker"


def test_upsert_缺_id_或_file_path_抛_note_error(index):
    index.load()
    with pytest.raises(NoteError):
        index.upsert({"file_path": "x.md"})
    with pytest.raises(NoteError):
        index.upsert({"id": "note_a"})


def test_remove_返回是否命中(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    assert index.remove("note_a") is True
    assert index.remove("note_a") is False
    assert len(index) == 0


def test_get_返回副本(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.get("note_a")["title"] = "改坏了"
    assert index.get("note_a")["title"] == "标题 note_a"
    assert index.get("note_x") is None


def test_replace_all_清空后重建(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.replace_all([entry("note_b", updated="2026-09-23T15:30:00+00:00")])
    assert [e["id"] for e in index.all()] == ["note_b"]


def test_all_按_updated_at_降序_同值按_id_升序(index):
    index.load()
    index.upsert(entry("note_c", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_b", updated="2026-09-24T15:30:00+00:00"))
    assert [e["id"] for e in index.all()] == ["note_b", "note_a", "note_c"]


def test_filter_按_type_过滤(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00", type="blocker"))
    index.upsert(entry("note_b", updated="2026-09-23T15:30:00+00:00", type="decision"))
    assert [e["id"] for e in index.filter(type="blocker")] == ["note_a"]


def test_filter_按_tags_全部包含(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00", tags=["deps", "phase1"]))
    index.upsert(entry("note_b", updated="2026-09-23T15:30:00+00:00", tags=["deps"]))
    assert [e["id"] for e in index.filter(tags=["deps", "phase1"])] == ["note_a"]
    assert [e["id"] for e in index.filter(tags=["deps"])] == ["note_a", "note_b"]


def test_filter_按_since_until_比较_updated_at(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_b", updated="2026-09-24T15:30:00+00:00"))
    since = parse_datetime("2026-09-24T00:00:00+00:00")
    assert [e["id"] for e in index.filter(since=since)] == ["note_b"]
    assert [e["id"] for e in index.filter(until=since)] == ["note_a"]


def test_filter_无参数返回全部(index):
    index.load()
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    assert [e["id"] for e in index.filter()] == ["note_a"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_note_index.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'hello_agents.notes.index'`。

- [ ] **Step 3: 写实现**

创建 `hello_agents/notes/index.py`：

```python
"""笔记索引 —— notes_index.json 的纯算术

只认字典，不认识文件系统：加载、保存、增删改查、字段过滤、整体替换。
文件与索引是否一致由 NoteStore 负责。
"""

import json
import logging
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import NoteError, parse_datetime

logger = logging.getLogger(__name__)


class NoteIndex:
    """notes_index.json 的内存视图与落盘

    条目是含 ``id`` 与 ``file_path`` 的字典，以 ``id`` 为键。索引是派生物：
    损坏或畸形的条目会被丢弃并告警，随后由 NoteStore 按目录重建。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._entries: dict[str, dict[str, Any]] = {}

    @property
    def path(self) -> Path:
        """索引文件路径"""
        return self._path

    def load(self) -> None:
        """从磁盘读入内存

        文件不存在 → 空索引；JSON 损坏、顶层不是对象、条目缺 id/file_path
        → 丢弃该部分并告警（索引是派生物，可重建）。
        """
        self._entries = {}
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("索引文件 %s 不可读（%s），按空索引处理", self._path, error)
            return
        if not isinstance(raw, dict):
            logger.warning("索引文件 %s 顶层不是对象，按空索引处理", self._path)
            return
        for entry in raw.values():
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("file_path"):
                logger.warning("索引文件 %s 存在缺 id/file_path 的条目，已丢弃", self._path)
                continue
            self._entries[str(entry["id"])] = dict(entry)

    def save(self) -> None:
        """原子落盘：先写同目录临时文件，再 os.replace"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: self._entries[key] for key in sorted(self._entries)}
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def get(self, note_id: str) -> dict[str, Any] | None:
        """取单条副本，不存在返回 None"""
        entry = self._entries.get(note_id)
        return dict(entry) if entry else None

    def all(self) -> list[dict[str, Any]]:
        """全部条目，按 updated_at 降序、同值按 id 升序"""
        return self._ordered(self._entries.values())

    def filter(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """按元数据过滤

        ``type`` 等值；``tags`` 为全部包含（AND）；``since`` / ``until``
        比较 ``updated_at``（闭区间）。
        """
        return self._ordered(
            entry
            for entry in self._entries.values()
            if self._matches(entry, type, tags, since, until)
        )

    def upsert(self, entry: dict[str, Any]) -> None:
        """按 ``entry["id"]`` 写入或覆盖；缺 id/file_path 抛 NoteError"""
        note_id = entry.get("id")
        if not note_id or not entry.get("file_path"):
            raise NoteError("索引条目必须包含 id 与 file_path")
        self._entries[str(note_id)] = dict(entry)

    def remove(self, note_id: str) -> bool:
        """删除条目，返回是否命中"""
        return self._entries.pop(note_id, None) is not None

    def replace_all(self, entries: list[dict[str, Any]]) -> None:
        """清空后按给定条目重建"""
        self._entries = {}
        for entry in entries:
            self.upsert(entry)

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _ordered(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 updated_at 降序、同值按 id 升序（两趟稳定排序）"""
        by_id = sorted((dict(entry) for entry in entries), key=lambda e: str(e.get("id") or ""))
        return sorted(by_id, key=lambda e: str(e.get("updated_at") or ""), reverse=True)

    @staticmethod
    def _matches(
        entry: dict[str, Any],
        type: str | None,
        tags: list[str] | None,
        since: datetime | None,
        until: datetime | None,
    ) -> bool:
        """单个条目是否满足全部过滤条件"""
        if type is not None and entry.get("type") != type:
            return False
        entry_tags = entry.get("tags")
        if tags and not set(tags).issubset(entry_tags if isinstance(entry_tags, list) else []):
            return False
        stamp = parse_datetime(entry.get("updated_at"))
        if since is not None and (stamp is None or stamp < since):
            return False
        if until is not None and (stamp is None or stamp > until):
            return False
        return True
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_note_index.py -v
```

Expected: PASS（16 passed）。

- [ ] **Step 5: 格式化并提交**

```bash
uv run ruff format hello_agents/notes tests/test_note_index.py
uv run ruff check hello_agents/notes tests/test_note_index.py
git add hello_agents/notes/index.py tests/test_note_index.py
git commit -m "feat: add note index with atomic persistence and metadata filters"
```

---

## Task 3: `notes/search.py`

**Files:**
- Create: `hello_agents/notes/search.py`
- Test: `tests/test_note_search.py`

**Interfaces:**
- Produces: `tokenize(text: str) -> list[str]`（英文/数字按 `[0-9a-z_]+` 小写，CJK 连续段按 bigram，单字成段则取该字）、`coverage(needles: list[str], haystack: list[str]) -> float`（去重后的覆盖率，空 needles 为 `0.0`）、`score(title: str, tags: list[str], body: str, query: str) -> float`（`[0, 1]`，空查询为 `0.0`）、模块常量 `TITLE_WEIGHT = 0.6` / `BODY_WEIGHT = 0.4`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_note_search.py`：

```python
"""笔记检索测试：分词 / 覆盖率 / 综合打分"""

from hello_agents.notes.search import BODY_WEIGHT, TITLE_WEIGHT, coverage, score, tokenize


def test_tokenize_英文数字小写化():
    assert tokenize("HTTPX 0.28 RAG_pipeline") == ["httpx", "0", "28", "rag_pipeline"]


def test_tokenize_中文按_bigram():
    assert tokenize("依赖冲突") == ["依赖", "赖冲", "冲突"]


def test_tokenize_单字成段取该字():
    assert tokenize("好") == ["好"]


def test_tokenize_中文标点分段不产生跨段_bigram():
    assert tokenize("依赖 冲突") == ["依赖", "冲突"]


def test_tokenize_中英混合():
    assert tokenize("用 httpx 排查依赖冲突") == ["httpx", "依赖", "赖冲", "冲突"]


def test_tokenize_纯标点与空白为空():
    assert tokenize("，。！？ ——") == []


def test_coverage_空_needles_为_0():
    assert coverage([], ["a"]) == 0.0


def test_coverage_无交集与全交集():
    assert coverage(["a", "b"], ["c"]) == 0.0
    assert coverage(["a", "b"], ["a", "b", "c"]) == 1.0
    assert coverage(["a", "b"], ["a"]) == 0.5


def test_coverage_重复_needles_去重():
    assert coverage(["a", "a"], ["a"]) == 1.0


def test_score_空查询为_0():
    assert score("标题", ["tag"], "正文", "   ") == 0.0


def test_score_纯标点查询为_0():
    assert score("标题", [], "正文", "，。！") == 0.0


def test_score_标题命中高于正文命中():
    title_hit = score("依赖冲突", [], "无关正文", "依赖冲突")
    body_hit = score("无关标题", [], "依赖冲突", "依赖冲突")
    assert title_hit == TITLE_WEIGHT
    assert body_hit == BODY_WEIGHT
    assert title_hit > body_hit


def test_score_标签计入标题侧():
    assert score("无关", ["依赖冲突"], "无关", "依赖冲突") == TITLE_WEIGHT


def test_score_全命中为_1():
    assert score("依赖冲突", [], "依赖冲突", "依赖冲突") == 1.0


def test_score_取值在_0_到_1_之间():
    value = score("依赖冲突排查", ["deps"], "## 现象\n\nhttpx 版本冲突。", "依赖冲突 httpx")
    assert 0.0 < value < 1.0
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_note_search.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'hello_agents.notes.search'`。

- [ ] **Step 3: 写实现**

创建 `hello_agents/notes/search.py`：

```python
"""笔记关键词打分

零依赖的中英混合分词与覆盖率打分，供 NoteStore.search 使用。
不做 TF-IDF 加权：两个权重常量放在模块级，便于单测与后续调参。
"""

import re

_TOKEN_RE = re.compile(r"[0-9a-z_]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")

TITLE_WEIGHT = 0.6
BODY_WEIGHT = 0.4


def tokenize(text: str) -> list[str]:
    """分词：英文/数字按 ``[0-9a-z_]+`` 小写，CJK 连续段按 bigram

    中文没有词边界：单字太散、整句太粗，bigram 是零依赖下的折中。
    单字成段时取该字本身。
    """
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def coverage(needles: list[str], haystack: list[str]) -> float:
    """needles 被 haystack 覆盖的比例（去重后），空 needles 为 0.0"""
    unique = set(needles)
    if not unique:
        return 0.0
    return len(unique & set(haystack)) / len(unique)


def score(title: str, tags: list[str], body: str, query: str) -> float:
    """综合得分 = 0.6 × 标题/标签覆盖率 + 0.4 × 正文覆盖率，取值 ``[0, 1]``

    查询分词为空（空白、纯标点）时返回 0.0。
    """
    needles = tokenize(query)
    if not needles:
        return 0.0
    head = tokenize(f"{title} {' '.join(tags)}")
    return TITLE_WEIGHT * coverage(needles, head) + BODY_WEIGHT * coverage(
        needles, tokenize(body)
    )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_note_search.py -v
```

Expected: PASS（15 passed）。

- [ ] **Step 5: 格式化并提交**

```bash
uv run ruff format hello_agents/notes tests/test_note_search.py
uv run ruff check hello_agents/notes tests/test_note_search.py
git add hello_agents/notes/search.py tests/test_note_search.py
git commit -m "feat: add zero-dependency keyword scoring for note search"
```
