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
    assert reloaded.get("note_a") == entry(
        "note_a", updated="2026-09-23T15:30:00+00:00"
    )


def test_save_顶层按_id_排序(index):
    index.load()
    index.upsert(entry("note_b", updated="2026-09-23T15:30:00+00:00"))
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.save()
    assert list(json.loads(index.path.read_text(encoding="utf-8"))) == [
        "note_a",
        "note_b",
    ]


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


def test_索引是无效_utf8_时按损坏处理(index, caplog):
    index.path.parent.mkdir(parents=True)
    index.path.write_bytes(b"\xff\xfe\x00\x01")
    with caplog.at_level("WARNING"):
        index.load()
    assert len(index) == 0
    assert "不可读" in caplog.text
    index.upsert(entry("note_a", updated="2026-09-23T15:30:00+00:00"))
    index.save()


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
    index.upsert(
        entry("note_a", updated="2026-09-23T15:30:00+00:00", tags=["deps", "phase1"])
    )
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
