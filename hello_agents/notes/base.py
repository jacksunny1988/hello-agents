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
    def from_markdown(
        cls, text: str, *, file_path: str, fallback_time: datetime
    ) -> "Note":
        """从 Markdown 文本解析笔记，缺失字段按 spec §2.5 兜底

        - 换行符统一为 ``\\n``（Windows / git autocrlf 写出的 CRLF 文件也能解析），
          并容忍开头 BOM（``removeprefix("\\ufeff")``）；读文件侧的 ``utf-8-sig``
          是第二道保险（store 读文件时用 ``utf-8-sig``）。
        - 正文首尾的空行仅在 frontmatter 被成功消费时归一化（Markdown 语义不变）；
          回退路径（无 frontmatter / 解析失败）整篇原文一字不动。
        - ``file_path`` 取真实文件名：手改过 frontmatter 的 ``id`` 可能与文件名不一致。
        """
        meta, body = _split_frontmatter(
            text.replace("\r\n", "\n").removeprefix("\ufeff")
        )
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

    成功消费围栏时返回 (映射, 正文)，正文首尾换行在此归一化；
    无 frontmatter、语法错误或不是映射时返回 (空映射, 全文)，原文一字不动。
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
    return meta, text[match.end() :].strip("\n")


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
