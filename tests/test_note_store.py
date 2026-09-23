"""NoteStore 测试：CRUD / 原子写 / 索引漂移 L1-L2"""

import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from hello_agents.notes.base import NoteError, NoteNotFoundError, NoteType
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
    assert json.loads(note_config.index_path.read_text(encoding="utf-8"))[note.id][
        "tags"
    ] == ["phase1"]


def test_create_自动建目录(note_config):
    assert not note_config.notes_dir.exists()
    NoteStore(note_config).create("标题")
    assert note_config.notes_dir.is_dir()


def test_create_id_格式为_note_时间戳_序号(note_store, clock):
    note = note_store.create("标题")
    assert note.id == "note_20260923_153001_0"


def test_create_同一秒内序号递增(note_store, clock, monkeypatch):
    clock.now = datetime(2026, 9, 23, 15, 30, tzinfo=UTC)

    def frozen() -> datetime:
        return clock.now

    monkeypatch.setattr("hello_agents.notes.store.utcnow", frozen)
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


def test_read_拒绝含路径分隔符的_id(note_store):
    for bad in ("../secret", "a/b", "a\\b"):
        with pytest.raises(NoteError):
            note_store.read(bad)


def test_read_拒绝绝对路径与驱动器_id(note_store):
    for bad in ("C:/temp/evil", "/tmp/evil"):
        with pytest.raises(NoteError):
            note_store.read(bad)


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
    path.write_text(
        path.read_text(encoding="utf-8").replace("旧标题", "新标题"), encoding="utf-8"
    )
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


def test_delete_拒绝越界_id_且不动盘(note_store, note_config):
    outside = note_config.notes_dir.parent / "secret.md"
    outside.write_text("别动我", encoding="utf-8")
    with pytest.raises(NoteError):
        note_store.delete("../secret")
    assert outside.exists()


def test_exists(note_store):
    note = note_store.create("标题")
    assert note_store.exists(note.id) is True
    assert note_store.exists("note_missing") is False


def test_sync_补录孤儿文件(note_store, note_config):
    write_note(
        note_config, "handwritten", "2026-09-23T15:30:00+00:00", title="手写笔记"
    )
    assert note_store.exists("handwritten") is True
    assert note_store.read("handwritten").title == "手写笔记"


def test_sync_删除缺失条目的索引项(note_store, note_config):
    note = note_store.create("标题")
    (note_config.notes_dir / note.file_path).unlink()
    assert note_store.exists(note.id) is False


def test_自动修复不改写_md_文件字节(note_store, note_config):
    note = note_store.create("标题", "正文")
    write_note(note_config, "handwritten", "2026-09-23T15:30:00+00:00", title="手写")
    before = {
        path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")
    }
    note_store.read(note.id)
    note_store.exists("handwritten")
    after = {
        path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")
    }
    assert before == after


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
    write_note(
        note_config,
        "title-hit",
        "2026-09-23T15:30:00+00:00",
        title="依赖冲突",
        body="无关",
    )
    write_note(
        note_config,
        "body-hit",
        "2026-09-23T15:30:00+00:00",
        title="无关",
        body="依赖冲突",
    )
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
    path.write_text(
        path.read_text(encoding="utf-8").replace("旧标题", "依赖冲突"), encoding="utf-8"
    )
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
    assert [(item.heading, item.preview) for item in sections] == [
        ("", "没有小节的正文")
    ]


def test_summary_空正文返回空_sections(note_store):
    note_store.create("空笔记", "")
    assert note_store.summary()[0].sections == []


def test_summary_预览截断到_80_字符(note_store):
    note_store.create("长笔记", "## 现象\n\n" + "字" * 200)
    assert len(note_store.summary()[0].sections[0].preview) == 80


def test_summary_按_type_过滤并支持_limit(note_store, clock):
    note_store.create("阻塞", type=NoteType.BLOCKER)
    note_store.create("决策", type=NoteType.DECISION)
    assert [item.meta.title for item in note_store.summary(type=NoteType.BLOCKER)] == [
        "阻塞"
    ]
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
    path.write_text(
        path.read_text(encoding="utf-8").replace("保留", "改过"), encoding="utf-8"
    )
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
    before = {
        path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")
    }
    note_store.verify()
    note_store.list()
    after = {
        path.name: path.read_bytes() for path in note_config.notes_dir.glob("*.md")
    }
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
