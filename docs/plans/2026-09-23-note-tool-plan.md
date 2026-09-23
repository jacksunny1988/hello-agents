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

---

## Task 4: `notes/store.py` 的 CRUD 与索引协同

**Files:**
- Create: `hello_agents/notes/store.py`（本任务只写 CRUD 与私有辅助）
- Modify: `tests/conftest.py`（追加 2 个夹具）
- Test: `tests/test_note_store.py`

**Interfaces:**
- Consumes: `base`（`Note` / `NoteConfig` / `NoteError` / `NoteMeta` / `NoteNotFoundError` / `NoteType` / `utcnow`）、`index.NoteIndex`
- Produces: `class NoteStore(config: NoteConfig | None = None)`，方法 `config`（property）、`create(title, body="", *, type=NoteType.GENERAL, tags=None, note_id=None) -> Note`、`read(note_id) -> Note`、`update(note_id, *, title=None, body=None, type=None, tags=None) -> Note`、`delete(note_id) -> bool`、`exists(note_id) -> bool`
- 契约：每个公开操作先 `_sync()`（L1 集合级对齐）；`read` / `update` 做 L2 单条级修正；自动修复**绝不改写 `.md`**；`update` 全参数为 `None` 时是 no-op

- [ ] **Step 1: 追加 conftest 夹具**

在 `tests/conftest.py` 末尾追加（**用子模块路径导入**，`hello_agents.notes` 的公开导出在 Task 6 才定稿）：

```python
from hello_agents.notes.base import NoteConfig
from hello_agents.notes.store import NoteStore


@pytest.fixture
def note_config(tmp_path) -> NoteConfig:
    return NoteConfig(notes_dir=tmp_path / "notes")


@pytest.fixture
def note_store(note_config) -> NoteStore:
    return NoteStore(config=note_config)
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_note_store.py`：

```python
"""NoteStore 测试：CRUD / 原子写 / 索引漂移 L1-L2"""

import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from hello_agents.notes.base import Note, NoteError, NoteNotFoundError, NoteType
from hello_agents.notes.store import NoteStore


@pytest.fixture
def clock(monkeypatch):
    """可控时钟：每次调用前进 1 秒，避免依赖系统时钟精度"""

    def tick() -> datetime:
        tick.now += timedelta(seconds=1)
        return tick.now

    tick.now = datetime(2026, 9, 23, 15, 30, tzinfo=UTC)
    monkeypatch.setattr("hello_agents.notes.store.utcnow", tick)
    return tick


def write_note(note_config, name: str, updated: str, **fields) -> None:
    """直接手写一个笔记文件（用于构造确定性的时间戳与漂移场景）"""
    note_config.notes_dir.mkdir(parents=True, exist_ok=True)
    tags = ", ".join(fields.get("tags") or [])
    text = (
        "---\n"
        f"id: {name}\n"
        f"title: {fields.get('title', name)}\n"
        f"type: {fields.get('type', 'general')}\n"
        f"tags: [{tags}]\n"
        f"created_at: {updated}\n"
        f"updated_at: {updated}\n"
        "---\n\n"
        f"{fields.get('body', '正文')}\n"
    )
    (note_config.notes_dir / f"{name}.md").write_text(text, encoding="utf-8")


def test_create_写文件并登记索引(note_store, note_config):
    note = note_store.create("项目进展", "## 完成情况\n\n已完成。", tags=["phase1"])
    text = (note_config.notes_dir / note.file_path).read_text(encoding="utf-8")
    assert yaml.safe_load(text.partition("\n---\n")[0][4:])["title"] == "项目进展"
    assert json.loads(note_config.index_path.read_text(encoding="utf-8"))[note.id]["tags"] == ["phase1"]


def test_create_自动建目录(note_config):
    assert not note_config.notes_dir.exists()
    NoteStore(note_config).create("标题")
    assert note_config.notes_dir.is_dir()


def test_create_id_格式为_note_时间戳_序号(note_store, clock):
    note = note_store.create("标题")
    assert note.id == "note_20260923_153001_0"


def test_create_同一秒内序号递增(note_store, clock):
    clock.now = datetime(2026, 9, 23, 15, 30, tzinfo=UTC)

    def frozen() -> datetime:
        return clock.now

    import hello_agents.notes.store as store_module

    store_module.utcnow = frozen
    ids = [note_store.create(f"标题{index}").id for index in range(3)]
    assert ids == [
        "note_20260923_153000_0",
        "note_20260923_153000_1",
        "note_20260923_153000_2",
    ]


def test_create_指定已存在的_id_抛_note_error(note_store):
    note = note_store.create("标题")
    with pytest.raises(NoteError):
        note_store.create("另一条", note_id=note.id)


def test_create_不覆盖目录里的手写同名文件(note_store, note_config):
    write_note(note_config, "note_x", "2026-09-23T15:30:00+00:00", title="手写的")
    with pytest.raises(NoteError):
        note_store.create("新标题", note_id="note_x")


def test_create_空标题抛_note_error(note_store):
    with pytest.raises(NoteError):
        note_store.create("   ")


def test_read_返回正文(note_store):
    note = note_store.create("标题", "## 结论\n\n锁定 httpx<0.28。")
    assert note_store.read(note.id).body == "## 结论\n\n锁定 httpx<0.28。"


def test_read_不存在抛_note_not_found(note_store):
    with pytest.raises(NoteNotFoundError):
        note_store.read("note_missing")


def test_read_空文件与只有_frontmatter_的文件不崩(note_store, note_config):
    note_config.notes_dir.mkdir(parents=True, exist_ok=True)
    (note_config.notes_dir / "empty.md").write_text("", encoding="utf-8")
    (note_config.notes_dir / "head-only.md").write_text(
        "---\ntitle: 只有头\n---\n", encoding="utf-8"
    )
    assert note_store.read("empty").body == ""
    assert note_store.read("head-only").title == "只有头"
    assert note_store.read("head-only").body == ""


def test_read_做_l2_修正(note_store, note_config):
    note = note_store.create("旧标题")
    path = note_config.notes_dir / note.file_path
    path.write_text(path.read_text(encoding="utf-8").replace("旧标题", "新标题"), encoding="utf-8")
    assert note_store.read(note.id).title == "新标题"
    index = json.loads(note_config.index_path.read_text(encoding="utf-8"))
    assert index[note.id]["title"] == "新标题"


def test_update_只改传入字段并刷新_updated_at(note_store, clock):
    note = note_store.create("标题", "正文", tags=["a"])
    updated = note_store.update(note.id, body="新正文")
    assert (updated.title, updated.body, updated.tags) == ("标题", "新正文", ["a"])
    assert updated.created_at == note.created_at
    assert updated.updated_at > note.updated_at
    assert note_store.read(note.id).body == "新正文"


def test_update_全_none_是_noop(note_store):
    note = note_store.create("标题")
    assert note_store.update(note.id).updated_at == note.updated_at


def test_update_空标题抛_note_error(note_store):
    note = note_store.create("标题")
    with pytest.raises(NoteError):
        note_store.update(note.id, title="  ")


def test_update_不存在抛_note_not_found(note_store):
    with pytest.raises(NoteNotFoundError):
        note_store.update("note_missing", body="x")


def test_delete_删除文件与索引条目(note_store, note_config):
    note = note_store.create("标题")
    assert note_store.delete(note.id) is True
    assert not (note_config.notes_dir / note.file_path).exists()
    assert note_store.exists(note.id) is False


def test_delete_不存在返回_false(note_store):
    assert note_store.delete("note_missing") is False


def test_exists(note_store):
    note = note_store.create("标题")
    assert note_store.exists(note.id) is True
    assert note_store.exists("note_missing") is False


def test_sync_补录孤儿文件(note_store, note_config):
    write_note(note_config, "handwritten", "2026-09-23T15:30:00+00:00", title="手写笔记")
    assert note_store.exists("handwritten") is True
    assert note_store.read("handwritten").title == "手写笔记"


def test_sync_删除缺失条目的索引项(note_store, note_config):
    note = note_store.create("标题")
    (note_config.notes_dir / note.file_path).unlink()
    assert note_store.exists(note.id) is False


def test_自动修复不改写_md_文件字节(note_store, note_config):
    note = note_store.create("标题", "正文")
    write_note(note_config, "handwritten", "2026-09-23T15:30:00+00:00", title="手写")
    before = {path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")}
    note_store.read(note.id)
    note_store.exists("handwritten")
    after = {path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")}
    assert before == after
```

- [ ] **Step 3: 跑测试确认失败**

```bash
uv run pytest tests/test_note_store.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'hello_agents.notes.store'`。

- [ ] **Step 4: 写实现**

创建 `hello_agents/notes/store.py`：

```python
"""笔记存储 —— 文件与索引的协同

.md 文件的原子读写、CRUD，以及索引漂移的检测与修复。
文件是真相源，索引是派生缓存：任何自动修复只改索引，绝不改写 .md。
"""

import logging
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .base import (
    Note,
    NoteConfig,
    NoteError,
    NoteMeta,
    NoteNotFoundError,
    NoteType,
    utcnow,
)
from .index import NoteIndex

logger = logging.getLogger(__name__)


class NoteStore:
    """结构化笔记存储

    每个公开操作都先 ``_sync()``（L1 集合级对齐），因此索引不常驻内存，
    多进程与人工编辑即时可见。写 ``.md`` 只发生在 ``create`` / ``update``。

    ``read`` 之外的检索类方法返回的 ``NoteMeta`` 直接取自刚读到的文件
    （它本就是 ``Note``，可能带 ``body``）；``list`` 只走索引，不带正文。
    """

    def __init__(self, config: NoteConfig | None = None) -> None:
        self._config = config or NoteConfig()
        self._index = NoteIndex(self._config.index_path)

    @property
    def config(self) -> NoteConfig:
        """当前配置"""
        return self._config

    def create(
        self,
        title: str,
        body: str = "",
        *,
        type: str = NoteType.GENERAL,
        tags: list[str] | None = None,
        note_id: str | None = None,
    ) -> Note:
        """新建笔记并落盘

        ``note_id`` 可显式指定（迁移 / 幂等写入）；已存在时抛 NoteError。
        未指定时按 ``note_YYYYMMDD_HHMMSS_N`` 生成，同秒内序号递增。
        """
        if not title.strip():
            raise NoteError("title 不能为空")
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        self._sync()
        now = utcnow()
        new_id = note_id or self._next_id(now)
        if self._index.get(new_id) is not None or (self._config.notes_dir / f"{new_id}.md").exists():
            raise NoteError(f"笔记 id 已存在: {new_id}")
        note = Note(
            id=new_id,
            title=title,
            type=str(type),
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
            file_path=f"{new_id}.md",
            body=body,
        )
        self._write(note)
        return note

    def read(self, note_id: str) -> Note:
        """读取单条笔记；不存在抛 NoteNotFoundError

        顺带做 L2 单条级漂移检测：frontmatter 与索引条目不一致时以文件为准修正索引。
        """
        self._sync()
        note = self._read_note(note_id)
        if self._repair_one(note):
            self._index.save()
        return note

    def update(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
    ) -> Note:
        """按字段更新；全部为 None 时是 no-op（不刷新 updated_at）"""
        if title is not None and not title.strip():
            raise NoteError("title 不能为空")
        current = self.read(note_id)
        if title is None and body is None and type is None and tags is None:
            return current
        updated = replace(
            current,
            title=current.title if title is None else title,
            body=current.body if body is None else body,
            type=current.type if type is None else str(type),
            tags=current.tags if tags is None else list(tags),
            updated_at=utcnow(),
        )
        self._write(updated)
        return updated

    def delete(self, note_id: str) -> bool:
        """删除笔记文件与索引条目；不存在返回 False"""
        self._sync()
        entry = self._index.get(note_id)
        path = self._config.notes_dir / (str(entry["file_path"]) if entry else f"{note_id}.md")
        if entry is None and not path.is_file():
            return False
        path.unlink(missing_ok=True)
        self._index.remove(note_id)
        self._index.save()
        return True

    def exists(self, note_id: str) -> bool:
        """笔记是否存在（先做 L1 对齐，再以索引为准）"""
        self._sync()
        return self._index.get(note_id) is not None

    def _sync(self) -> None:
        """L1 集合级：目录 ↔ 索引对齐（缺失条目删除、孤儿文件补录）"""
        self._index.load()
        on_disk = self._markdown_names()
        indexed = {str(entry["file_path"]) for entry in self._index.all()}
        changed = False
        for entry in self._index.all():
            if entry["file_path"] not in on_disk:
                self._index.remove(str(entry["id"]))
                changed = True
        for file_name in sorted(on_disk - indexed):
            self._index.upsert(self._read_file(file_name).to_dict())
            changed = True
        if changed:
            self._index.save()
            logger.warning("笔记索引与目录不一致，已按目录修正（现 %d 条）", len(self._index))

    def _write(self, note: Note) -> None:
        """原子写 .md 并同步索引"""
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        path = self._config.notes_dir / note.file_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(note.to_markdown(), encoding="utf-8")
        os.replace(tmp, path)
        self._index.upsert(note.to_dict())
        self._index.save()

    def _read_note(self, note_id: str) -> Note:
        """按 id 读文件；不存在抛 NoteNotFoundError"""
        entry = self._index.get(note_id)
        file_name = str(entry["file_path"]) if entry else f"{note_id}.md"
        if not (self._config.notes_dir / file_name).is_file():
            raise NoteNotFoundError(f"笔记不存在: {note_id}")
        return self._read_file(file_name)

    def _read_file(self, file_name: str) -> Note:
        """读并解析一个笔记文件（BOM 由 utf-8-sig 处理）"""
        path = self._config.notes_dir / file_name
        return Note.from_markdown(
            path.read_text(encoding="utf-8-sig"),
            file_path=file_name,
            fallback_time=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )

    def _repair_one(self, note: Note) -> bool:
        """L2 单条级：以文件 frontmatter 为准修正索引条目，返回是否改动（不落盘）"""
        wanted = note.to_dict()
        changed = self._index.get(note.id) != wanted
        for entry in self._index.all():
            if entry["id"] != note.id and entry["file_path"] == note.file_path:
                self._index.remove(str(entry["id"]))
                changed = True
        if changed:
            self._index.upsert(wanted)
            logger.warning("笔记 %s 的索引条目与文件不一致，已按文件修正", note.file_path)
        return changed

    def _markdown_names(self) -> set[str]:
        """目录下的 .md 文件名集合（目录不存在时为空集）"""
        if not self._config.notes_dir.is_dir():
            return set()
        return {path.name for path in self._config.notes_dir.glob("*.md")}

    def _next_id(self, now: datetime) -> str:
        """生成 ``note_YYYYMMDD_HHMMSS_N``，同时避开目录与索引里的既有 id"""
        prefix = f"note_{now:%Y%m%d_%H%M%S}_"
        used = {path.stem for path in self._config.notes_dir.glob(f"{prefix}*.md")}
        used |= {
            str(entry["id"])
            for entry in self._index.all()
            if str(entry["id"]).startswith(prefix)
        }
        serial = 0
        while f"{prefix}{serial}" in used:
            serial += 1
        return f"{prefix}{serial}"
```

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run pytest tests/test_note_store.py -v
```

Expected: PASS（22 passed）。

- [ ] **Step 6: 格式化并提交**

```bash
uv run ruff format hello_agents/notes tests/test_note_store.py tests/conftest.py
uv run ruff check hello_agents/notes tests/test_note_store.py tests/conftest.py
git add hello_agents/notes/store.py tests/test_note_store.py tests/conftest.py
git commit -m "feat: add note store with atomic writes and index drift repair"
```

---

## Task 5: `NoteStore` 的检索与完整性

**Files:**
- Modify: `hello_agents/notes/store.py`（追加 `list` / `search` / `summary` / `verify` / `rebuild_index` 与模块级 `_sections_of`）
- Test: `tests/test_note_store.py`（追加后半）

**Interfaces:**
- Consumes: Task 4 的全部私有辅助（`_sync` / `_read_file` / `_repair_one` / `_markdown_names`）、`search.score`、`base` 的 `DriftReport` / `NoteSummary` / `SectionPreview`
- Produces: `list(*, type=None, tags=None, since=None, until=None, limit=None) -> list[NoteMeta]`、`search(query, *, limit=10) -> list[tuple[NoteMeta, float]]`、`summary(*, type=None, tags=None, limit=None) -> list[NoteSummary]`、`verify() -> DriftReport`、`rebuild_index() -> int`
- 契约：`list` **零文件读**；`search` / `summary` 为 O(n) 文件读并顺手做 L2 修正；`verify` **只报告不改动**（不写盘）

- [ ] **Step 1: 写失败测试**

在 `tests/test_note_store.py` 末尾追加：

```python
def test_list_只读索引不读文件(note_store, monkeypatch, clock):
    note_store.create("标题一", "正文")
    note_store.create("标题二", "正文")
    read_calls = []
    monkeypatch.setattr(
        NoteStore, "_read_file", lambda self, file_name: read_calls.append(file_name)
    )
    assert [meta.title for meta in note_store.list()] == ["标题二", "标题一"]
    assert read_calls == []


def test_list_按_type_与_tags_过滤(note_store, clock):
    note_store.create("阻塞", type=NoteType.BLOCKER, tags=["deps"])
    note_store.create("决策", type=NoteType.DECISION, tags=["deps"])
    assert [meta.title for meta in note_store.list(type=NoteType.BLOCKER)] == ["阻塞"]
    assert [meta.title for meta in note_store.list(tags=["deps"])] == ["决策", "阻塞"]


def test_list_limit(note_store, clock):
    for index in range(3):
        note_store.create(f"标题{index}")
    assert len(note_store.list(limit=2)) == 2
    assert note_store.list(limit=0) == []


def test_list_按_updated_at_过滤(note_store, note_config, clock):
    write_note(note_config, "old", "2026-09-23T10:00:00+00:00", title="旧的")
    write_note(note_config, "new", "2026-09-24T10:00:00+00:00", title="新的")
    since = datetime(2026, 9, 24, tzinfo=UTC)
    assert [meta.title for meta in note_store.list(since=since)] == ["新的"]
    assert [meta.title for meta in note_store.list(until=since)] == ["旧的"]


def test_search_标题命中优先于正文命中(note_store, note_config):
    write_note(note_config, "title-hit", "2026-09-23T15:30:00+00:00", title="依赖冲突", body="无关")
    write_note(note_config, "body-hit", "2026-09-23T15:30:00+00:00", title="无关", body="依赖冲突")
    hits = note_store.search("依赖冲突")
    assert [meta.title for meta, _ in hits] == ["依赖冲突", "无关"]
    assert hits[0][1] == 0.6
    assert hits[1][1] == 0.4


def test_search_无命中返回空(note_store, note_config):
    write_note(note_config, "a", "2026-09-23T15:30:00+00:00", title="向量检索选型")
    assert note_store.search("完全不相关的词") == []


def test_search_limit(note_store, note_config):
    write_note(note_config, "a", "2026-09-23T15:30:00+00:00", title="依赖冲突一")
    write_note(note_config, "b", "2026-09-23T15:30:00+00:00", title="依赖冲突二")
    assert len(note_store.search("依赖冲突", limit=1)) == 1


def test_search_顺手修正_l2_漂移(note_store, note_config):
    note = note_store.create("旧标题")
    path = note_config.notes_dir / note.file_path
    path.write_text(path.read_text(encoding="utf-8").replace("旧标题", "依赖冲突"), encoding="utf-8")
    note_store.search("依赖冲突")
    index = json.loads(note_config.index_path.read_text(encoding="utf-8"))
    assert index[note.id]["title"] == "依赖冲突"


def test_summary_提取小节标题与首行预览(note_store):
    note_store.create(
        "项目进展",
        "## 完成情况\n\n已完成数据模型层重构。\n\n## 下一步计划\n\n重构业务逻辑层。",
    )
    sections = note_store.summary()[0].sections
    assert [(item.heading, item.preview) for item in sections] == [
        ("完成情况", "已完成数据模型层重构。"),
        ("下一步计划", "重构业务逻辑层。"),
    ]


def test_summary_无小节时用首个非空行(note_store):
    note_store.create("随手记", "没有小节的正文\n\n第二行")
    sections = note_store.summary()[0].sections
    assert [(item.heading, item.preview) for item in sections] == [("", "没有小节的正文")]


def test_summary_空正文返回空_sections(note_store):
    note_store.create("空笔记", "")
    assert note_store.summary()[0].sections == []


def test_summary_预览截断到_80_字符(note_store):
    note_store.create("长笔记", "## 现象\n\n" + "字" * 200)
    assert len(note_store.summary()[0].sections[0].preview) == 80


def test_summary_按_type_过滤并支持_limit(note_store, clock):
    note_store.create("阻塞", type=NoteType.BLOCKER)
    note_store.create("决策", type=NoteType.DECISION)
    assert [item.meta.title for item in note_store.summary(type=NoteType.BLOCKER)] == ["阻塞"]
    assert len(note_store.summary(limit=1)) == 1


def test_summary_含_sections_的_to_dict(note_store):
    note_store.create("标题", "## 结论\n\n锁定 httpx<0.28。")
    payload = note_store.summary()[0].to_dict()
    assert payload["sections"] == [{"heading": "结论", "preview": "锁定 httpx<0.28。"}]
    assert payload["title"] == "标题"


def test_verify_报告三类漂移(note_store, note_config, clock):
    keep = note_store.create("保留")
    gone = note_store.create("删除")
    (note_config.notes_dir / gone.file_path).unlink()
    write_note(note_config, "orphan", "2026-09-23T15:30:00+00:00", title="孤儿")
    path = note_config.notes_dir / keep.file_path
    path.write_text(path.read_text(encoding="utf-8").replace("保留", "改过"), encoding="utf-8")
    report = note_store.verify()
    assert report.missing_files == [gone.id]
    assert report.orphan_files == ["orphan.md"]
    assert report.mismatched == [keep.id]
    assert report.is_clean is False
    assert report.summary() == "缺失 1 / 孤儿 1 / 不一致 1"


def test_verify_清洁时为_is_clean(note_store):
    note_store.create("标题")
    assert note_store.verify().is_clean is True


def test_verify_与_list_不改写任何文件(note_store, note_config):
    note_store.create("标题", "正文")
    write_note(note_config, "orphan", "2026-09-23T15:30:00+00:00", title="孤儿")
    before = {path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")}
    note_store.verify()
    note_store.list()
    after = {path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")}
    assert before == after


def test_rebuild_index_重建并返回条目数(note_store, note_config):
    note_store.create("一")
    note_store.create("二")
    note_config.index_path.write_text("{ 坏掉的索引", encoding="utf-8")
    assert note_store.rebuild_index() == 2
    assert note_store.verify().is_clean is True


def test_索引损坏后操作能自愈(note_store, note_config):
    note = note_store.create("标题")
    note_config.index_path.write_text("不是 json", encoding="utf-8")
    assert note_store.exists(note.id) is True
    assert [meta.title for meta in note_store.list()] == ["标题"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_note_store.py -v
```

Expected: FAIL —— `AttributeError: 'NoteStore' object has no attribute 'list'`。

- [ ] **Step 3: 写实现**

在 `hello_agents/notes/store.py` 的 `exists` 方法之后、`_sync` 之前插入五个公开方法：

```python
    def list(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[NoteMeta]:
        """按索引过滤列出笔记元数据——不读任何笔记文件

        这是索引存在的意义：目录里有上千条笔记时，本方法也只读一次索引。
        """
        self._sync()
        entries = self._index.filter(type=type, tags=tags, since=since, until=until)
        return [NoteMeta.from_index_entry(entry) for entry in entries[:limit]]

    def search(self, query: str, *, limit: int = 10) -> list[tuple[NoteMeta, float]]:
        """关键词检索：对全部笔记正文打分（O(n) 文件读），顺手做 L2 修正

        只返回分数大于 0 的命中；按分数降序，同分沿用索引的 ``updated_at`` 降序
        （索引本身已按此排序，``sort`` 稳定）。
        """
        self._sync()
        scored: list[tuple[NoteMeta, float]] = []
        changed = False
        for entry in self._index.all():
            note = self._read_file(str(entry["file_path"]))
            changed |= self._repair_one(note)
            value = score(note.title, note.tags, note.body, query)
            if value > 0:
                scored.append((note, value))
        if changed:
            self._index.save()
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def summary(
        self,
        *,
        type: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[NoteSummary]:
        """全库摘要：元数据 + 正文小节预览（O(n) 文件读）

        与 ``list`` 的成本差异是刻意的：摘要要正文小节，必须读文件。
        """
        self._sync()
        summaries: list[NoteSummary] = []
        changed = False
        for entry in self._index.filter(type=type, tags=tags)[:limit]:
            note = self._read_file(str(entry["file_path"]))
            changed |= self._repair_one(note)
            summaries.append(NoteSummary(meta=note, sections=_sections_of(note.body)))
        if changed:
            self._index.save()
        return summaries

    def verify(self) -> DriftReport:
        """L3 全量级：读所有 .md 逐字段比对，只报告不改动（不写盘）"""
        self._index.load()
        on_disk = self._markdown_names()
        indexed = {str(entry["file_path"]): entry for entry in self._index.all()}
        return DriftReport(
            missing_files=[
                str(entry["id"]) for file_name, entry in indexed.items() if file_name not in on_disk
            ],
            orphan_files=sorted(on_disk - set(indexed)),
            mismatched=[
                str(indexed[file_name]["id"])
                for file_name in sorted(on_disk & set(indexed))
                if self._read_file(file_name).to_dict() != indexed[file_name]
            ],
        )

    def rebuild_index(self) -> int:
        """L3：清空索引并按目录全量重建，返回条目数"""
        self._config.notes_dir.mkdir(parents=True, exist_ok=True)
        entries = [self._read_file(name).to_dict() for name in sorted(self._markdown_names())]
        self._index.replace_all(entries)
        self._index.save()
        return len(self._index)
```

同时把文件顶部的导入补全（`re`、`SectionPreview`、`NoteSummary`、`DriftReport`、`score`），并在文件末尾追加模块级函数：

```python
_PREVIEW_CHARS = 80
_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)


def _sections_of(body: str) -> list[SectionPreview]:
    """提取二级及以下标题作为小节，每节取紧随其后的首个非空行（截断 80 字符）

    没有小节标题时取正文首个非空行作为唯一预览（``heading`` 为空串）；
    正文为空时返回空列表。
    """
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        return [SectionPreview(heading="", preview=first[:_PREVIEW_CHARS])] if first else []
    sections: list[SectionPreview] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        preview = next(
            (line.strip() for line in body[match.end() : end].splitlines() if line.strip()),
            "",
        )
        sections.append(SectionPreview(heading=match.group(2), preview=preview[:_PREVIEW_CHARS]))
    return sections
```

导入块最终形态：

```python
import logging
import os
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .base import (
    DriftReport,
    Note,
    NoteConfig,
    NoteError,
    NoteMeta,
    NoteNotFoundError,
    NoteSummary,
    NoteType,
    SectionPreview,
    utcnow,
)
from .index import NoteIndex
from .search import score
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_note_store.py -v
```

Expected: PASS（41 passed）。

- [ ] **Step 5: 格式化并提交**

```bash
uv run ruff format hello_agents/notes tests/test_note_store.py
uv run ruff check hello_agents/notes tests/test_note_store.py
git add hello_agents/notes/store.py tests/test_note_store.py
git commit -m "feat: add note listing, search, summary and integrity verification"
```

---

## Task 6: 公开导出与 `NoteTool`

**Files:**
- Modify: `hello_agents/notes/__init__.py`（定稿）
- Modify: `hello_agents/tools/builtin/__init__.py`（`__all__` 补 `NoteTool`）
- Create: `hello_agents/tools/builtin/note_tool.py`
- Modify: `tests/conftest.py`（夹具改为从包根导入）
- Test: `tests/test_note_tool.py`

**Interfaces:**
- Consumes: `hello_agents.notes` 的全部公开名字、`tools.base.BaseTool` / `ToolParameter`、`tools.response.ToolResponse`
- Produces: `class NoteTool(BaseTool)`，`run(input_data=None, **kwargs) -> ToolResponse`，`get_parameters() -> list[ToolParameter]`，构造签名 `NoteTool(store: NoteStore | None = None)`，模块级 `_ACTIONS: set[str]`
- 契约：七个动作 `create` / `read` / `update` / `delete` / `list` / `search` / `summary`；错误码 `INVALID_PARAM` / `NOT_FOUND` / `NOTE_ERROR`；纯文本入参默认 `action="search"`；`tags` 传字符串时按逗号/空格切分

- [ ] **Step 1: 定稿 `notes/__init__.py`**

```python
"""结构化笔记子系统

每条笔记是一个 Markdown 文件：YAML frontmatter 记元数据，正文记状态、结论、
阻塞与行动项；`notes_index.json` 是派生索引，用于快速检索、元数据集中管理与
完整性校验。**文件是真相源**：手改过的 `.md` 会被读回，索引按文件自动修正，
也可以随时 `rebuild_index()` 全量重建。

典型用法：
    from hello_agents.notes import NoteStore, NoteType

    store = NoteStore()                                    # 落盘到 ./notes/
    note = store.create("依赖冲突排查", "## 现象\\n...", type=NoteType.BLOCKER)
    store.update(note.id, body=note.body + "\\n## 结论\\n锁定 httpx<0.28")
    hits = store.search("依赖冲突")                         # [(NoteMeta, 0.83), ...]
    store.summary()                                        # 全库摘要（含小节预览）
"""

from .base import (
    DriftReport,
    Note,
    NoteConfig,
    NoteError,
    NoteMeta,
    NoteNotFoundError,
    NoteSummary,
    NoteType,
    SectionPreview,
    parse_datetime,
    utcnow,
)
from .index import NoteIndex
from .search import coverage, score, tokenize
from .store import NoteStore

__all__ = [
    "DriftReport",
    "Note",
    "NoteConfig",
    "NoteError",
    "NoteIndex",
    "NoteMeta",
    "NoteNotFoundError",
    "NoteStore",
    "NoteSummary",
    "NoteType",
    "SectionPreview",
    "coverage",
    "parse_datetime",
    "score",
    "tokenize",
    "utcnow",
]
```

- [ ] **Step 2: 两处导出**

`hello_agents/tools/builtin/__init__.py`：加 `from .note_tool import NoteTool`，并在 `__all__` 中按字母序插入 `"NoteTool"`（`MemoryTool` 之后、`RAGTool` 之前）。

**不要动 `hello_agents/tools/__init__.py`**：`MemoryTool` / `RAGTool` 都不在它的 `__all__` 里，`NoteTool` 保持一致。

- [ ] **Step 3: conftest 夹具改从包根导入**

把 Task 4 追加的两行导入改为：

```python
from hello_agents.notes import NoteConfig, NoteStore
```

- [ ] **Step 4: 写失败测试**

创建 `tests/test_note_tool.py`：

```python
"""NoteTool 测试：七个动作的契约与错误码"""

import json

import pytest

from hello_agents.tools.builtin.note_tool import _ACTIONS, NoteTool
from hello_agents.tools.response import ToolStatus


@pytest.fixture
def tool(note_store) -> NoteTool:
    return NoteTool(note_store)


def test_动作集完整():
    assert _ACTIONS == {"create", "read", "update", "delete", "list", "search", "summary"}


def test_get_parameters_覆盖全部动作参数(tool):
    names = {param.name for param in tool.get_parameters()}
    assert {"action", "id", "title", "body", "type", "tags", "query", "limit"} <= names


def test_create_成功(tool):
    response = tool.run(
        {"action": "create", "title": "项目进展", "body": "正文", "tags": ["phase1"]}
    )
    assert response.status is ToolStatus.SUCCESS
    assert response.data["title"] == "项目进展"
    assert response.data["id"].startswith("note_")
    assert "已创建笔记" in response.text


def test_create_缺_title_报_invalid_param(tool):
    assert tool.run({"action": "create", "body": "正文"}).error_info["code"] == "INVALID_PARAM"


def test_read_成功含正文(tool):
    note_id = tool.run(
        {"action": "create", "title": "标题", "body": "## 结论\n\n锁定 httpx<0.28。"}
    ).data["id"]
    response = tool.run({"action": "read", "id": note_id})
    assert response.status is ToolStatus.SUCCESS
    assert response.data["body"] == "## 结论\n\n锁定 httpx<0.28。"
    assert "锁定 httpx<0.28。" in response.text


def test_read_不存在报_not_found(tool):
    assert tool.run({"action": "read", "id": "note_missing"}).error_info["code"] == "NOT_FOUND"


def test_read_缺_id_报_invalid_param(tool):
    assert tool.run({"action": "read"}).error_info["code"] == "INVALID_PARAM"


def test_update_成功(tool):
    note_id = tool.run({"action": "create", "title": "标题", "body": "旧"}).data["id"]
    assert tool.run({"action": "update", "id": note_id, "body": "新"}).status is ToolStatus.SUCCESS
    assert tool.run({"action": "read", "id": note_id}).data["body"] == "新"


def test_update_无字段报_invalid_param(tool):
    note_id = tool.run({"action": "create", "title": "标题"}).data["id"]
    assert tool.run({"action": "update", "id": note_id}).error_info["code"] == "INVALID_PARAM"


def test_update_不存在报_not_found(tool):
    response = tool.run({"action": "update", "id": "note_missing", "body": "x"})
    assert response.error_info["code"] == "NOT_FOUND"


def test_delete_成功与不存在(tool):
    note_id = tool.run({"action": "create", "title": "标题"}).data["id"]
    assert tool.run({"action": "delete", "id": note_id}).data["deleted"] is True
    response = tool.run({"action": "delete", "id": note_id})
    assert response.status is ToolStatus.SUCCESS
    assert response.data["deleted"] is False


def test_list_过滤(tool):
    tool.run({"action": "create", "title": "阻塞", "type": "blocker"})
    tool.run({"action": "create", "title": "决策", "type": "decision"})
    response = tool.run({"action": "list", "type": "blocker"})
    assert [note["title"] for note in response.data["notes"]] == ["阻塞"]
    assert "阻塞" in response.text


def test_list_空库返回提示(tool):
    response = tool.run({"action": "list"})
    assert response.data["notes"] == []
    assert response.text == "暂无笔记。"


def test_search_返回_score(tool):
    tool.run({"action": "create", "title": "依赖冲突排查", "body": "依赖冲突"})
    hits = tool.run({"action": "search", "query": "依赖冲突"}).data["hits"]
    assert hits[0]["title"] == "依赖冲突排查"
    assert hits[0]["score"] == 1.0


def test_search_缺_query_报_invalid_param(tool):
    assert tool.run({"action": "search"}).error_info["code"] == "INVALID_PARAM"


def test_summary_返回_sections(tool):
    tool.run({"action": "create", "title": "项目进展", "body": "## 完成情况\n\n已完成重构。"})
    notes = tool.run({"action": "summary"}).data["notes"]
    assert notes[0]["sections"] == [{"heading": "完成情况", "preview": "已完成重构。"}]


def test_未知_action_报_invalid_param(tool):
    assert tool.run({"action": "rewrite"}).error_info["code"] == "INVALID_PARAM"


def test_纯文本入参默认_search(tool):
    tool.run({"action": "create", "title": "依赖冲突排查"})
    assert tool.run("依赖冲突").data["hits"][0]["title"] == "依赖冲突排查"


def test_json_字符串入参(tool):
    assert tool.run(json.dumps({"action": "create", "title": "标题"})).status is ToolStatus.SUCCESS


def test_tags_传字符串时按逗号与空格切分(tool):
    note_id = tool.run({"action": "create", "title": "标题", "tags": "deps, phase1"}).data["id"]
    assert tool.run({"action": "read", "id": note_id}).data["tags"] == ["deps", "phase1"]


def test_磁盘故障报_note_error(tool, monkeypatch):
    def boom(note_id):
        raise OSError("磁盘满了")

    monkeypatch.setattr(tool.store, "read", boom)
    assert tool.run({"action": "read", "id": "note_x"}).error_info["code"] == "NOTE_ERROR"
```

- [ ] **Step 5: 跑测试确认失败**

```bash
uv run pytest tests/test_note_tool.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'hello_agents.tools.builtin.note_tool'`。

- [ ] **Step 6: 写实现**

创建 `hello_agents/tools/builtin/note_tool.py`：

```python
"""笔记工具

让 Agent 在长时程任务中读写结构化笔记：create / read / update / delete /
list / search / summary 七个动作，底层是 NoteStore。
"""

import json
import re
from typing import Any

from ...notes import NoteError, NoteNotFoundError, NoteStore, NoteType
from ..base import BaseTool, ToolParameter
from ..response import ToolResponse

_ACTIONS = {"create", "read", "update", "delete", "list", "search", "summary"}
_UPDATE_FIELDS = ("title", "body", "type", "tags")


def _invalid(message: str) -> ToolResponse:
    """参数错误响应"""
    return ToolResponse.error(code="INVALID_PARAM", message=message)


def _as_tags(value: Any) -> list[str] | None:
    """把入参 tags 规整为列表：字符串按逗号/空白切分（LLM 常这么传）"""
    if value is None:
        return None
    if isinstance(value, str):
        return [item for item in re.split(r"[,，\s]+", value) if item]
    return [str(item) for item in value]


class NoteTool(BaseTool):
    """结构化笔记工具（长时程任务的外部记忆）"""

    def __init__(self, store: NoteStore | None = None):
        super().__init__(
            name="note",
            description=(
                "笔记工具：读写结构化笔记。action=create 新建；read 读取；"
                "update 更新；delete 删除；list 列出；search 检索；summary 取全库摘要。"
            ),
        )
        self.store = store or NoteStore()

    def run(self, input_data: Any = None, **kwargs) -> ToolResponse:
        """执行笔记操作

        input_data 支持三种形式：dict、JSON 字符串、纯文本
        （纯文本默认按 action=search 检索）。
        """
        params = self._parse_input(input_data, kwargs)
        action = params.get("action", "search")
        if action not in _ACTIONS:
            return _invalid(f"未知 action: {action}，可选 {sorted(_ACTIONS)}")
        try:
            return getattr(self, f"_do_{action}")(params)
        except NoteNotFoundError as error:
            return ToolResponse.error(code="NOT_FOUND", message=str(error))
        except Exception as error:  # 文件系统故障不应击穿 Agent 循环
            return ToolResponse.error(code="NOTE_ERROR", message=str(error))

    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="action",
                type="str",
                description="操作类型: create / read / update / delete / list / search / summary",
            ),
            ToolParameter(
                name="id",
                type="str",
                description="笔记 id（read / update / delete 必填）",
                required=False,
            ),
            ToolParameter(
                name="title",
                type="str",
                description="标题（create 必填）",
                required=False,
            ),
            ToolParameter(
                name="body",
                type="str",
                description="Markdown 正文",
                required=False,
            ),
            ToolParameter(
                name="type",
                type="str",
                description="笔记类型: task_state / decision / blocker / finding / general",
                required=False,
            ),
            ToolParameter(
                name="tags",
                type="list",
                description="标签列表，也接受逗号分隔的字符串",
                required=False,
            ),
            ToolParameter(
                name="query",
                type="str",
                description="检索关键词（search 必填）",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type="int",
                description="返回条数上限",
                required=False,
                default=10,
            ),
        ]

    def _do_create(self, params: dict[str, Any]) -> ToolResponse:
        title = params.get("title")
        if not title:
            return _invalid("action=create 需要提供 title")
        note = self.store.create(
            title=str(title),
            body=str(params.get("body") or ""),
            type=str(params.get("type") or NoteType.GENERAL),
            tags=_as_tags(params.get("tags")),
        )
        return ToolResponse.success(
            text=f"已创建笔记 {note.id}：《{note.title}》", data=note.to_dict()
        )

    def _do_read(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=read 需要提供 id")
        note = self.store.read(str(note_id))
        header = f"[{note.type}] {note.title} ({note.id}) 更新于 {note.updated_at.isoformat()}"
        return ToolResponse.success(
            text=f"{header}\n\n{note.body}", data=note.to_dict() | {"body": note.body}
        )

    def _do_update(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=update 需要提供 id")
        fields: dict[str, Any] = {}
        for name in _UPDATE_FIELDS:
            value = params.get(name)
            if value is None:
                continue
            fields[name] = _as_tags(value) if name == "tags" else value
        if not fields:
            return _invalid(f"action=update 至少需要提供 {list(_UPDATE_FIELDS)} 之一")
        note = self.store.update(str(note_id), **fields)
        return ToolResponse.success(text=f"已更新笔记 {note.id}", data=note.to_dict())

    def _do_delete(self, params: dict[str, Any]) -> ToolResponse:
        note_id = params.get("id")
        if not note_id:
            return _invalid("action=delete 需要提供 id")
        deleted = self.store.delete(str(note_id))
        text = f"已删除笔记 {note_id}" if deleted else f"笔记 {note_id} 不存在"
        return ToolResponse.success(text=text, data={"id": str(note_id), "deleted": deleted})

    def _do_list(self, params: dict[str, Any]) -> ToolResponse:
        notes = self.store.list(
            type=params.get("type"), tags=_as_tags(params.get("tags")), limit=params.get("limit")
        )
        if not notes:
            return ToolResponse.success(text="暂无笔记。", data={"notes": []})
        lines = [
            f"[{meta.type}] {meta.title} ({meta.id}, {meta.updated_at.isoformat()})"
            for meta in notes
        ]
        return ToolResponse.success(
            text="\n".join(lines), data={"notes": [meta.to_dict() for meta in notes]}
        )

    def _do_search(self, params: dict[str, Any]) -> ToolResponse:
        query = params.get("query") or params.get("content")
        if not query:
            return _invalid("action=search 需要提供 query")
        hits = self.store.search(str(query), limit=int(params.get("limit") or 10))
        if not hits:
            return ToolResponse.success(
                text=f"未检索到与「{query}」相关的笔记。", data={"hits": []}
            )
        lines = [
            f"[{meta.type}] {meta.title} ({meta.id}, {value:.2f})" for meta, value in hits
        ]
        return ToolResponse.success(
            text="\n".join(lines),
            data={"hits": [meta.to_dict() | {"score": value} for meta, value in hits]},
        )

    def _do_summary(self, params: dict[str, Any]) -> ToolResponse:
        summaries = self.store.summary(
            type=params.get("type"), tags=_as_tags(params.get("tags")), limit=params.get("limit")
        )
        if not summaries:
            return ToolResponse.success(text="暂无笔记。", data={"notes": []})
        lines: list[str] = []
        for item in summaries:
            lines.append(f"[{item.meta.type}] {item.meta.title} ({item.meta.id})")
            for section in item.sections:
                prefix = f"{section.heading}: " if section.heading else ""
                lines.append(f"  - {prefix}{section.preview}")
        return ToolResponse.success(
            text="\n".join(lines), data={"notes": [item.to_dict() for item in summaries]}
        )

    @staticmethod
    def _parse_input(input_data: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """把 input_data 规整为参数字典（dict / JSON 字符串 / 纯文本）"""
        params: dict[str, Any] = dict(kwargs)
        if input_data is None:
            return params
        if isinstance(input_data, dict):
            params.update(input_data)
            return params
        text = str(input_data).strip()
        if text.startswith("{"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, dict):
                params.update(loaded)
                return params
        params.setdefault("action", "search")
        params.setdefault("query", text)
        return params
```

- [ ] **Step 7: 跑测试确认通过**

```bash
uv run pytest tests/test_note_tool.py -v
```

Expected: PASS（21 passed）。

- [ ] **Step 8: 格式化并提交**

```bash
uv run ruff format hello_agents/notes hello_agents/tools tests/test_note_tool.py tests/conftest.py
uv run ruff check hello_agents/notes hello_agents/tools tests/test_note_tool.py tests/conftest.py
git add hello_agents/notes/__init__.py hello_agents/tools tests/test_note_tool.py tests/conftest.py
git commit -m "feat: add note tool with seven agent-facing actions"
```

---

## Task 7: 示例脚本与收尾

**Files:**
- Create: `examples/note_tool_demo.py`
- Modify: `.gitignore`（追加 `notes/`）

**Interfaces:**
- Consumes: `hello_agents.notes` 的全部公开名字、`hello_agents.tools.builtin.NoteTool`
- Produces: 可离线跑通的演示脚本，覆盖创建 / 更新 / 过滤 / 检索 / 摘要 / 索引自愈

- [ ] **Step 1: 写示例**

创建 `examples/note_tool_demo.py`：

```python
"""NoteTool 离线示例

演示结构化笔记的创建、更新、过滤、检索、摘要与索引自愈，
全程不触网、不需要任何 API key。产物落在 ./notes/（已在 .gitignore 中忽略；
要版本化自己的笔记时，删掉 .gitignore 里的 `notes/` 那一行即可）。
"""

from pathlib import Path

from hello_agents.notes import NoteConfig, NoteStore, NoteType
from hello_agents.tools.builtin import NoteTool

NOTES = [
    (
        "项目进展 - 第一阶段",
        "# 项目进展 - 第一阶段\n\n## 完成情况\n\n已完成数据模型层的重构。\n\n"
        "## 下一步计划\n\n重构业务逻辑层。",
        NoteType.TASK_STATE,
        ["refactoring", "phase1"],
    ),
    (
        "依赖冲突排查",
        "# 依赖冲突排查\n\n## 现象\n\nhttpx 版本冲突导致启动失败。\n\n"
        "## 阻塞\n\n等待上游修复。",
        NoteType.BLOCKER,
        ["deps", "phase1"],
    ),
    (
        "向量检索选型",
        "# 向量检索选型\n\n## 结论\n\n本地 TF-IDF 兜底，配置后升级 Qdrant。",
        NoteType.DECISION,
        ["rag"],
    ),
]


def main() -> None:
    config = NoteConfig(notes_dir=Path("./notes"))
    store = NoteStore(config)
    tool = NoteTool(store)

    print("== 创建 ==")
    for title, body, note_type, tags in NOTES:
        response = tool.run(
            {"action": "create", "title": title, "body": body, "type": note_type, "tags": tags}
        )
        print(f"  {response.text}")

    print("\n== 更新 ==")
    blocker = store.list(type=NoteType.BLOCKER)[0]
    response = tool.run(
        {"action": "update", "id": blocker.id, "body": blocker.body + "\n\n## 结论\n\n锁定 httpx<0.28。"}
    )
    print(f"  {response.text}")

    print("\n== 列出（type=task_state）==")
    print(tool.run({"action": "list", "type": NoteType.TASK_STATE}).text)

    print("\n== 检索「依赖冲突」==")
    print(tool.run({"action": "search", "query": "依赖冲突"}).text)

    print("\n== 全库摘要 ==")
    print(tool.run({"action": "summary"}).text)

    print("\n== 索引自愈 ==")
    scratch = store.create("临时草稿", "## 草稿\n\n用完即弃。")
    (config.notes_dir / scratch.file_path).unlink()
    print(f"  删除文件: {scratch.file_path}")
    print(f"  verify: {store.verify().summary()}")
    print(f"  rebuild 条目数: {store.rebuild_index()}")
    print(f"  verify: {store.verify().summary()}")

    print(f"\n产物目录: {config.notes_dir.resolve()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑示例**

```bash
uv run python examples/note_tool_demo.py
```

Expected: 依次打印创建 / 更新 / 列出 / 检索 / 摘要 / 自愈六段；最后两行 `verify` 分别为 `缺失 1 / 孤儿 0 / 不一致 0` 与 `缺失 0 / 孤儿 0 / 不一致 0`，`rebuild 条目数: 3`。

- [ ] **Step 3: 核对产物**

```bash
ls notes/ && head -8 notes/note_*.md | head -20
```

Expected: 3 个 `.md` 与 1 个 `notes_index.json`；每个 `.md` 的 frontmatter 含 `id` / `title` / `type` / `tags` / `created_at` / `updated_at`，时间戳带 `+00:00`，`tags` 是行内列表。

- [ ] **Step 4: 忽略示例产物**

在 `.gitignore` 的「Serena local config」段之前追加：

```
# NoteTool 示例产物
notes/
```

- [ ] **Step 5: 全量检查**

```bash
uv run ruff format hello_agents tests examples
uv run ruff check hello_agents tests examples
uv run pytest tests/ -q
git status --short
```

Expected:
- `ruff check` 中**本次触碰的文件零告警**（`search.py` / `core/llm.py` / `chain.py` / `memory_tool.py` / `calculator.py` / `neo4j_store.py` / `simple_agent.py` / `react_agent.py` 的既有告警不修）。
- `pytest` 全绿，`tests/test_note_*.py` 共 4 个文件、约 99 个用例全过，既有测试不回归。
- `git status` 中不出现 `notes/`。

- [ ] **Step 6: 提交**

```bash
git add examples/note_tool_demo.py .gitignore
git commit -m "docs: add offline NoteTool demo and ignore its output"
```

---

## 自检记录

**1. Spec 覆盖**：spec §2（数据模型与格式）→ Task 1；§3.2 `NoteIndex` → Task 2；§3.2 `search` → Task 3；§4.4 的 `create` / `read` / `update` / `delete` / `exists` 与 §5.2 的 L1 / L2 → Task 4；§4.4 的 `list` / `search` / `summary` / `verify` / `rebuild_index` 与 §5.2 的 L3 → Task 5；§4.5 `NoteTool` 七动作 → Task 6；§7 交付物中的示例脚本与 `.gitignore` → Task 7。§1.3 的非目标（不动 `context/` / `memory/` / `agents/` / `hello_agents/__init__.py`）在 Global Constraints 中约束，Task 6 Step 2 明确不改 `tools/__init__.py`。

**2. 规划期对 spec 的三处修订**（已在 spec 中同步，实施时以 spec 为准）：
- `Note.from_markdown` 的 `fallback_id` 改为 `file_path`：手改过 frontmatter 的文件，其 `id` 可能与文件名不一致，`file_path` 必须取真实文件名。
- 新增 `base.parse_datetime` 公开辅助函数：`base` / `index` / `store` 三处都要把 ISO 字符串解析为带时区 `datetime`，不该各写一份。
- 测试文件清单补 `tests/test_note_base.py`：YAML 标量引号规则是手写序列化最容易写错的地方，需要直接测试。

**3. 类型一致性**：`NoteMeta.to_dict()` 的 7 个字段与索引条目、`NoteIndex.upsert` 的入参、`NoteStore._repair_one` 的比对对象三者同构；`NoteSummary.to_dict()` 输出 `sections: [{heading, preview}]`，与 Task 6 的 `_do_summary` 断言一致；`search.score` 的返回被 `NoteStore.search` 与 `NoteTool._do_search` 一路保留到 `data["hits"][i]["score"]`，中间无改名。

**4. Review Focus 落点**：CRLF + BOM → Task 1 `test_from_markdown_容忍_crlf_与_bom`；标题特殊字符 → Task 1 `test_to_markdown_标题含_yaml_特殊字符仍可解析`；正文水平线 → Task 1 `test_from_markdown_正文含水平线不被当作围栏`；目录缺失 / 空文件 / 只有 frontmatter → Task 4 `test_create_自动建目录`、`test_read_空文件与只有_frontmatter_的文件不崩`、Task 5 `test_summary_空正文返回空_sections`；工具 `tags` 传字符串 → Task 6 `test_tags_传字符串时按逗号与空格切分`。
