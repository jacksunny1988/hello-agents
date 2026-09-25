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
