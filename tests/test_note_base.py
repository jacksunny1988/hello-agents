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
    assert (
        Note.from_markdown(note.to_markdown(), file_path=FILE, fallback_time=STAMP)
        == note
    )


def test_from_markdown_正文含水平线不被当作围栏():
    body = "## 现象\n\n---\n\n## 结论\n\n锁定 httpx<0.28。"
    note = make_note(body=body)
    assert (
        Note.from_markdown(note.to_markdown(), file_path=FILE, fallback_time=STAMP).body
        == body
    )


def test_from_markdown_容忍_crlf_与_bom():
    text = "\ufeff" + make_note().to_markdown().replace("\n", "\r\n")
    note = Note.from_markdown(text, file_path=FILE, fallback_time=STAMP)
    assert note.id == "note_20260923_153000_0"
    assert note.body == BODY


def test_from_markdown_无_frontmatter_时用文件名与兜底时间():
    note = Note.from_markdown(
        "# 随手记\n\n正文", file_path="my-notes.md", fallback_time=STAMP
    )
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
    assert Note.from_markdown(text, file_path="x.md", fallback_time=STAMP).tags == [
        "refactoring"
    ]


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
    assert (
        Note.from_markdown("", file_path=FILE, fallback_time=STAMP).to_dict()[
            "file_path"
        ]
        == FILE
    )


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
